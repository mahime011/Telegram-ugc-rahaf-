import os
import asyncio
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ========== الإعدادات ==========
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
YUNWU_API_KEY = os.environ.get("YUNWU_API_KEY")
YUNWU_BASE = "https://yunwu.ai/v1"

# ========== توليد صورة UGC ==========
async def generate_ugc_image(scene):
    scenes = {
        "coffee": "a person's hand holding an iPhone with a stylish phone case, sitting at a cozy coffee shop, warm natural lighting, coffee cup on wooden table, authentic UGC style, shot on iPhone, realistic, 4K",
        "mirror": "mirror selfie of a stylish person holding iPhone with trendy phone case, bedroom background, casual outfit, authentic UGC style, natural lighting, TikTok aesthetic",
        "desk": "flat lay on aesthetic desk, iPhone with designer phone case next to laptop and plants, warm afternoon sunlight, UGC aesthetic, authentic photo",
        "outdoor": "person holding iPhone with colorful phone case while walking in the city, street background, golden hour lighting, candid shot, UGC style, realistic"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{YUNWU_BASE}/images/generations",
            headers={"Authorization": f"Bearer {YUNWU_API_KEY}"},
            json={
                "model": "dall-e-3",
                "prompt": scenes[scene],
                "size": "1024x1024",
                "n": 1
            }
        ) as resp:
            data = await resp.json()
            return data["data"][0]["url"]

# ========== توليد فيديو من الصورة ==========
async def submit_video_job(image_url, style="ugc"):
    prompts = {
        "ugc": "authentic user generated content, person casually showing the phone, subtle hand movement, natural lighting, realistic, TikTok style",
        "cinematic": "cinematic product showcase, slow elegant rotation, soft lighting, premium feel"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://yunwu.ai/luma/generations",
            headers={"Authorization": f"Bearer {YUNWU_API_KEY}"},
            json={
                "user_prompt": prompts[style],
                "model_name": "ray-v2",
                "duration": "5s",
                "resolution": "720p",
                "image_url": image_url
            }
        ) as resp:
            data = await resp.json()
            return data["id"], data.get("state", "pending")

async def poll_video_status(job_id, max_attempts=30, delay=10):
    async with aiohttp.ClientSession() as session:
        for i in range(max_attempts):
            async with session.get(
                f"https://yunwu.ai/luma/generations/{job_id}",
                headers={"Authorization": f"Bearer {YUNWU_API_KEY}"}
            ) as resp:
                data = await resp.json()
                if data.get("state") == "completed":
                    return data.get("video") or data.get("video_raw")
            await asyncio.sleep(delay)
    return None

# ========== كابشن UGC ==========
async def generate_ugc_caption():
    prompt = """
    اكتب كابشن تسويقي بالعربية لمنشور إنستغرام عن غلاف هاتف مخصص. 
    اجعله يبدو من عميل حقيقي (UGC): لغة عادية غير رسمية، emojis، تجربة شخصية، اطلب التعليق، 5 هاشتاغات.
    """
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{YUNWU_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {YUNWU_API_KEY}"},
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": prompt}]
            }
        ) as resp:
            data = await resp.json()
            return data["choices"][0]["message"]["content"]

# ========== أوامر البوت ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📱 *بوت توليد محتوى UGC — Rahaf*\n\n"
        "أرسل لي صورة غلاف هاتف وسأولد لك:\n"
        "1️⃣ صورة UGC واقعية\n"
        "2️⃣ فيديو UGC قصير\n"
        "3️⃣ كابشن جاهز للنشر\n\n"
        "🚀 ابدأ بإرسال صورة المنتج!",
        parse_mode="Markdown"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    photo_url = file.file_path
    context.user_data["product_image"] = photo_url
    
    keyboard = [
        [InlineKeyboardButton("☕ Coffee Shop", callback_data="scene_coffee")],
        [InlineKeyboardButton("🪞 Mirror Selfie", callback_data="scene_mirror")],
        [InlineKeyboardButton("🌿 Desk Aesthetic", callback_data="scene_desk")],
        [InlineKeyboardButton("🏙️ Outdoor/City", callback_data="scene_outdoor")]
    ]
    await update.message.reply_text(
        "✅ تم استلام الصورة!\n\nاختر سيناريو UGC:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_scene(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    scene = query.data.replace("scene_", "")
    
    loading = await query.edit_message_text("⏳ جاري توليد صورة UGC...")
    
    try:
        # ===== صورة UGC =====
        ugc_image_url = await generate_ugc_image(scene)
        
        await loading.delete()
        await query.message.reply_photo(
            photo=ugc_image_url,
            caption="📸 *صورة UGC جاهزة!*\n\nالآن سأولد الفيديو...",
            parse_mode="Markdown"
        )
        
        # ===== فيديو UGC =====
        loading_video = await query.message.reply_text(
            "🎬 *جاري توليد الفيديو UGC...*\n⏳ قد يستغرق 1-3 دقائق",
            parse_mode="Markdown"
        )
        
        job_id, _ = await submit_video_job(ugc_image_url, "ugc")
        video_url = await poll_video_until_done(job_id, max_attempts=25, delay=10)
        
        # ===== كابشن =====
        caption = await generate_ugc_caption()
        
        await loading_video.delete()
        
        if video_url:
            await query.message.reply_video(
                video=video_url,
                caption=f"🎬 *فيديو UGC جاهز!*\n\n{caption}",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text(
                f"⏱️ *الفيديو يستغرق وقتاً أطول*\n\n✍️ الكابشن:\n\n{caption}",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        await loading.edit_text(f"❌ خطأ: {str(e)}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_scene, pattern="^scene_"))
    print("🤖 بوت UGC Rahaf يعمل...")
    app.run_polling()

if __name__ == "__main__":
    main()

