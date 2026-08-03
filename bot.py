"""
UGC Rahaf — Telegram bot for automated marketing content (Print-on-Demand phone cases).

Pipeline:
    product photo  ->  GPT-4o vision (describe the actual case design)
                   ->  DALL-E 3      (lifestyle UGC scene featuring that design)
                   ->  Luma ray-v2   (image-to-video, 5s 720p)
                   ->  GPT-4o        (Arabic marketing caption)

All model calls go through the YUNWU AI relay.

Environment variables:
    TELEGRAM_TOKEN   (required)  BotFather token
    YUNWU_API_KEY    (required)  YUNWU API key
    YUNWU_BASE_URL   (optional)  default https://yunwu.ai
    IMAGE_MODEL      (optional)  default dall-e-3
    CHAT_MODEL       (optional)  default gpt-4o
    VIDEO_MODEL      (optional)  default ray-v2
    VIDEO_RESOLUTION (optional)  default 720p
    ENABLE_VIDEO     (optional)  set to "0" to skip the video step
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
from typing import Any, Dict, Optional, Tuple

import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("ugc-rahaf")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
YUNWU_API_KEY = os.environ.get("YUNWU_API_KEY", "").strip()
YUNWU_BASE_URL = os.environ.get("YUNWU_BASE_URL", "https://yunwu.ai").rstrip("/")

IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "dall-e-3")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-4o")
VIDEO_MODEL = os.environ.get("VIDEO_MODEL", "ray-v2")
VIDEO_RESOLUTION = os.environ.get("VIDEO_RESOLUTION", "720p")
ENABLE_VIDEO = os.environ.get("ENABLE_VIDEO", "1") != "0"

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=180)
POLL_INTERVAL_SECONDS = 6
POLL_MAX_ATTEMPTS = 80  # ~8 minutes

# Scene key -> (Arabic button label, English scene description for the image model)
SCENES: Dict[str, Tuple[str, str]] = {
    "coffee": (
        "☕ مقهى",
        "a sunlit specialty coffee shop table, latte in a ceramic cup, "
        "a young woman's manicured hand holding the phone above the table, "
        "warm morning light through a window, shallow depth of field, "
        "casual authentic influencer photo",
    ),
    "mirror": (
        "🪞 مرآة",
        "a full-length mirror selfie in a bright modern bedroom, "
        "a stylish young woman holding the phone up so the case faces the mirror, "
        "soft natural daylight, minimalist neutral interior, "
        "candid social-media selfie aesthetic",
    ),
    "desk": (
        "💻 مكتب",
        "a clean minimalist work desk flat-lay, open laptop, notebook and pen, "
        "a small plant, the phone resting case-up beside a keyboard, "
        "soft diffused overhead light, top-down productivity aesthetic",
    ),
    "outdoor": (
        "🌿 خارجي",
        "an outdoor city street scene at golden hour, "
        "a young woman holding the phone casually at chest height, "
        "blurred greenery and warm bokeh city lights behind her, "
        "lifestyle street-style photography",
    ),
}


# --------------------------------------------------------------------------- #
# YUNWU API helpers
# --------------------------------------------------------------------------- #


def _headers() -> Dict[str, str]:
    return {
        "Authorization": "Bearer " + YUNWU_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _post_json(
    session: aiohttp.ClientSession, path: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """POST JSON to the relay and return the decoded body, raising on HTTP errors."""
    url = YUNWU_BASE_URL + path
    async with session.post(url, headers=_headers(), json=payload) as resp:
        body = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(
                "POST {} -> HTTP {}: {}".format(path, resp.status, body[:500])
            )
        try:
            return await resp.json(content_type=None)
        except Exception as exc:
            raise RuntimeError(
                "POST {} returned non-JSON: {}".format(path, body[:500])
            ) from exc


async def _get_json(session: aiohttp.ClientSession, path: str) -> Dict[str, Any]:
    url = YUNWU_BASE_URL + path
    async with session.get(url, headers=_headers()) as resp:
        body = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(
                "GET {} -> HTTP {}: {}".format(path, resp.status, body[:500])
            )
        try:
            return await resp.json(content_type=None)
        except Exception as exc:
            raise RuntimeError(
                "GET {} returned non-JSON: {}".format(path, body[:500])
            ) from exc


async def describe_product(
    session: aiohttp.ClientSession, image_bytes: bytes
) -> str:
    """
    Use GPT-4o vision to describe the uploaded phone-case design.

    This step exists because /v1/images/generations is text-to-image only -- it
    cannot accept the customer's photo. Without this, DALL-E would invent a
    random case that has nothing to do with the actual product.
    """
    data_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": CHAT_MODEL,
        "max_tokens": 300,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You describe phone case designs for an image generator. "
                    "Reply with ONE dense English paragraph, no preamble. Capture the "
                    "exact artwork, colour palette, patterns, text and finish on the "
                    "case back. Be specific and literal -- this description will be "
                    "used to recreate the case faithfully in another image."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Describe the phone case design in this photo.",
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }
    data = await _post_json(session, "/v1/chat/completions", payload)
    return data["choices"][0]["message"]["content"].strip()


async def generate_ugc_image(
    session: aiohttp.ClientSession, product_description: str, scene_key: str
) -> str:
    """Generate the lifestyle UGC still via DALL-E 3. Returns a public image URL."""
    _, scene_prompt = SCENES[scene_key]
    prompt = (
        "Authentic user-generated-content lifestyle photo, shot on an iPhone, "
        "featuring a smartphone in a custom printed phone case. "
        "THE CASE DESIGN MUST BE EXACTLY: {desc} "
        "Scene: {scene}. "
        "Photorealistic, natural imperfect lighting, no studio setup, "
        "no text overlays, no watermarks, no visible brand logos. "
        "The phone case artwork must be clearly visible and in sharp focus."
    ).format(desc=product_description, scene=scene_prompt)

    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "size": "1024x1792",
        "quality": "hd",
    }
    data = await _post_json(session, "/v1/images/generations", payload)
    entry = data["data"][0]
    url = entry.get("url")
    if not url:
        raise RuntimeError("Image API returned no URL: {}".format(str(data)[:400]))
    return url


async def submit_video_job(
    session: aiohttp.ClientSession, image_url: str, scene_key: str
) -> str:
    """
    Submit the image-to-video job to Luma via the YUNWU relay.

    NOTE the relay's field names differ from the official Luma API:
    it wants `user_prompt` / `image_url` / `model_name`, not
    `prompt` / `keyframes` / `model`.
    """
    payload = {
        "user_prompt": (
            "Subtle cinematic motion: gentle handheld camera drift and a slow push-in "
            "toward the phone case, soft natural light shifting. "
            "Keep the case artwork sharp, stable and unchanged."
        ),
        "model_name": VIDEO_MODEL,
        "image_url": image_url,
        "duration": "5s",
        "resolution": VIDEO_RESOLUTION,
        "expand_prompt": True,
        "loop": False,
    }
    data = await _post_json(session, "/luma/generations", payload)

    task_id = None
    if isinstance(data.get("data"), dict):
        task_id = data["data"].get("task_id") or data["data"].get("id")
    task_id = task_id or data.get("task_id") or data.get("id")

    if not task_id:
        raise RuntimeError("No task_id in video response: {}".format(str(data)[:400]))
    log.info("Video task submitted: %s", task_id)
    return str(task_id)


def _extract_video_url(payload: Dict[str, Any]) -> Optional[str]:
    """Pull a video URL out of whichever shape the relay returns."""
    node: Any = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(node, dict):
        return None

    assets = node.get("assets")
    if isinstance(assets, dict) and assets.get("video"):
        return assets["video"]

    for key in ("video_url", "videoUrl", "url", "video"):
        value = node.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, dict) and isinstance(value.get("url"), str):
            return value["url"]
    return None


def _extract_status(payload: Dict[str, Any]) -> str:
    node: Any = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(node, dict):
        return ""
    for key in ("task_status", "status", "state"):
        value = node.get(key)
        if isinstance(value, str):
            return value.lower()
    return ""


async def poll_video_status(session: aiohttp.ClientSession, task_id: str) -> str:
    """Poll until the video is ready. Returns the video URL, or raises."""
    path = "/luma/generations/{}".format(task_id)
    for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            data = await _get_json(session, path)
        except Exception as exc:
            log.warning("Poll %s attempt %s failed: %s", task_id, attempt, exc)
            continue

        video_url = _extract_video_url(data)
        if video_url:
            log.info("Video ready after %s polls: %s", attempt, task_id)
            return video_url

        status = _extract_status(data)
        if status in ("failed", "error", "cancelled", "canceled"):
            raise RuntimeError(
                "Video generation {}: {}".format(status, str(data)[:400])
            )
        if attempt % 10 == 0:
            log.info("Still waiting on %s (status=%s, poll %s)", task_id, status, attempt)

    raise TimeoutError("Video {} did not finish within the polling window".format(task_id))


async def generate_ugc_caption(
    session: aiohttp.ClientSession, product_description: str, scene_key: str
) -> str:
    """Write the Arabic marketing caption via GPT-4o."""
    scene_label, _ = SCENES[scene_key]
    payload = {
        "model": CHAT_MODEL,
        "temperature": 0.9,
        "max_tokens": 400,
        "messages": [
            {
                "role": "system",
                "content": (
                    "أنت كاتب محتوى تسويقي عربي متخصص في السوشيال ميديا. "
                    "تكتب منشورات قصيرة وجذابة لمتجر أغلفة هواتف مخصصة بالطباعة حسب الطلب. "
                    "الأسلوب: عامي راقٍ، حماسي بدون مبالغة، يخاطب الشباب. "
                    "اكتب: سطر افتتاحي قوي (hook)، ثم سطرين أو ثلاثة عن المنتج، "
                    "ثم دعوة واضحة للشراء، ثم 5 إلى 7 هاشتاقات عربية وإنجليزية. "
                    "استخدم الإيموجي باعتدال. لا تكتب أي مقدمات أو شروحات، المنشور فقط."
                ),
            },
            {
                "role": "user",
                "content": (
                    "اكتب منشور تسويقي لغلاف جوال بهذا التصميم:\n{desc}\n\n"
                    "المشهد المستخدم في الإعلان: {scene}"
                ).format(desc=product_description, scene=scene_label),
            },
        ],
    }
    data = await _post_json(session, "/v1/chat/completions", payload)
    return data["choices"][0]["message"]["content"].strip()


# --------------------------------------------------------------------------- #
# Telegram handlers
# --------------------------------------------------------------------------- #

WELCOME = (
    "🎨 *أهلاً بك في بوت UGC Rahaf*\n\n"
    "أرسل لي صورة غلاف الجوال، وأنا أجهّز لك:\n"
    "1️⃣ صورة إعلانية واقعية بأسلوب UGC\n"
    "2️⃣ فيديو قصير ٥ ثواني\n"
    "3️⃣ منشور تسويقي جاهز بالعربي\n\n"
    "📸 أرسل الصورة الآن للبدء."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME, parse_mode="Markdown")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store the uploaded product photo and offer the scene choices."""
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await tg_file.download_as_bytearray())

    context.user_data["product_image"] = image_bytes
    context.user_data.pop("product_description", None)

    keyboard = [
        [
            InlineKeyboardButton(SCENES["coffee"][0], callback_data="scene:coffee"),
            InlineKeyboardButton(SCENES["mirror"][0], callback_data="scene:mirror"),
        ],
        [
            InlineKeyboardButton(SCENES["desk"][0], callback_data="scene:desk"),
            InlineKeyboardButton(SCENES["outdoor"][0], callback_data="scene:outdoor"),
        ],
    ]
    await update.message.reply_text(
        "✅ وصلت الصورة!\n\n🎬 اختر المشهد الإعلاني:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_scene_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the full generation pipeline for the chosen scene."""
    query = update.callback_query
    await query.answer()

    scene_key = query.data.split(":", 1)[1]
    if scene_key not in SCENES:
        await query.edit_message_text("⚠️ مشهد غير معروف.")
        return

    image_bytes = context.user_data.get("product_image")
    if not image_bytes:
        await query.edit_message_text("⚠️ انتهت صلاحية الصورة. أرسل صورة المنتج من جديد.")
        return

    if context.user_data.get("busy"):
        await query.answer("⏳ هناك طلب قيد التنفيذ، انتظر قليلاً.", show_alert=True)
        return
    context.user_data["busy"] = True

    chat_id = query.message.chat_id
    scene_label = SCENES[scene_key][0]
    status_msg = await query.edit_message_text(
        "🔍 أحلّل تصميم الغلاف...\n\nالمشهد: {}".format(scene_label)
    )

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            # 1. Understand the actual product
            description = context.user_data.get("product_description")
            if not description:
                description = await describe_product(session, image_bytes)
                context.user_data["product_description"] = description
            log.info("Product description: %s", description[:160])

            # 2. Lifestyle still
            await status_msg.edit_text(
                "🎨 أنشئ الصورة الإعلانية...\n\nالمشهد: {}".format(scene_label)
            )
            await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
            image_url = await generate_ugc_image(session, description, scene_key)

            await context.bot.send_photo(
                chat_id,
                photo=image_url,
                caption="🖼 الصورة الإعلانية — {}".format(scene_label),
            )

            # 3. Caption (fast, send before the slow video step)
            await status_msg.edit_text("✍️ أكتب المنشور التسويقي...")
            caption = await generate_ugc_caption(session, description, scene_key)
            await context.bot.send_message(chat_id, caption)

            # 4. Video
            if ENABLE_VIDEO:
                await status_msg.edit_text(
                    "🎬 أنتج الفيديو... قد يستغرق ٢-٤ دقائق ⏳"
                )
                task_id = await submit_video_job(session, image_url, scene_key)
                video_url = await poll_video_status(session, task_id)

                await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
                await context.bot.send_video(
                    chat_id,
                    video=video_url,
                    caption="🎬 الفيديو الإعلاني — {}".format(scene_label),
                )

            await status_msg.edit_text("✅ تم! جاهز للنشر 🚀\n\nأرسل صورة جديدة لتصميم آخر.")

    except TimeoutError as exc:
        log.error("Timeout: %s", exc)
        await context.bot.send_message(
            chat_id, "⏱ الفيديو استغرق وقتاً أطول من المتوقع. الصورة والمنشور جاهزان أعلاه."
        )
    except Exception as exc:  # noqa: BLE001 - surface any pipeline failure to the user
        log.exception("Pipeline failed")
        await context.bot.send_message(
            chat_id, "❌ حدث خطأ أثناء التنفيذ:\n`{}`".format(str(exc)[:300]),
            parse_mode="Markdown",
        )
    finally:
        context.user_data["busy"] = False


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📸 أرسل صورة غلاف الجوال للبدء، أو اكتب /start.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled error", exc_info=context.error)


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #


def main() -> None:
    missing = [
        name
        for name, value in (
            ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
            ("YUNWU_API_KEY", YUNWU_API_KEY),
        )
        if not value
    ]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        log.error("Set them in Railway -> your service -> Variables, then redeploy.")
        sys.exit(1)

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_scene_choice, pattern=r"^scene:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)

    log.info("🤖 بوت UGC Rahaf يعمل...")
    log.info("Relay: %s | image=%s chat=%s video=%s",
             YUNWU_BASE_URL, IMAGE_MODEL, CHAT_MODEL, VIDEO_MODEL)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
