# AI Saharan Melhafa Studio — Phase 1

Architecture document · Infrastructure report · Repository structure · Implementation roadmap · Environment variable checklist · Operating cost model · Security assessment

Prepared: 2026-11-13 (Africa/Algiers) · Single-owner private application (explicitly not SaaS)

---

## 1. Executive summary

Goal unchanged from your brief: one uploaded Melhafa photograph → fabric profile → advertising concept → storyboard → per-scene reference images → per-scene AI video clips → voice-over / music / SFX → FFmpeg-assembled 1080×1920 ad film, delivered through a private web app and a private Telegram bot, fully automatic by default.

Phase 1 inspection found a **partially ready** foundation. Everything below was read live this session; nothing destructive was attempted and no existing resource was modified.

| Area | Verified live state | Verdict |
|---|---|---|
| GitHub | Token valid (classic PAT, `repo` scope). One repository: `mahime011/Telegram-ugc-rahaf-`, public, default branch `main`, push allowed. It already contains an unrelated UGC project. | READY — new app isolated in its own folder |
| Neon PostgreSQL | Project `muddy-snow-74379433`, PostgreSQL **18**, region `aws-us-east-1`, organization-owned (`org-autumn-cake-60413655`), your permission ADMIN. Branch `production` (`br-polished-sky-bcv0qaoe`) state `ready`, ~32 MB used, autoscale 0.25–2 CU, PITR history retention 21 600 s (6 h). No application tables yet. | READY — empty canvas |
| Neon MCP | Connected via OAuth; schema / query / branch / migration tools present and permitted. | READY |
| Context7 MCP | Present as your own server, status untested. | AVAILABLE |
| Cloudflare | A personal API card exists for account `73cd575e6f579540d60847b2cb9c47cf`. Two live calls failed: `/tokens/verify` → HTTP 400 code **6111** (*Invalid format for Authorization header*), `/r2/buckets` → HTTP 400 code **9106** (*Authentication failed*). | **BLOCKED** — token unusable; R2 and Pages state unknown |
| Cloudflare MCP · Hugging Face MCP · GitHub MCP | Not present in your connected MCP list (only context7 + neon). GitHub works through your linked token instead. | NOT CONNECTED |
| Hostinger Ubuntu VPS | No server registered, and the SSH executor is off on this deployment (`AGENT_HOST_DROPLET` off), so remote commands cannot run at all. CPU / RAM / disk / Docker / FFmpeg therefore **unmeasured**. | **GAP** — you run the inspection script |
| MAX Code sandbox | Every shell call fails with: sandbox template `snap_BIbaNEXrL7GKo4yPXCKjNlxfBSTa` is not provisioned for provider `vercel` (`retryable: false`). Local Node/FFmpeg versions could not be verified here either. | **BLOCKED** (platform issue to report) |
| Vercel app | Linked (profile `aprof_eb908dea99f54b19`). Your approved stack names **Cloudflare Pages** for the frontend — two hosting targets now exist. | DECISION NEEDED |
| Telegram | Personal Telegram connection: not linked. Needs a bot token + your numeric Telegram user ID for the owner-only allow-list. | MISSING |
| AI provider credentials | No Anthropic / OpenAI / Gemini / FLUX / Runway / Kling / Seedance / Veo / Hugging Face credential is configured or discoverable from this workspace. | MISSING |

Net position: **code hosting and database are genuinely ready**; storage/CDN needs a working Cloudflare token; compute needs measurement; every AI-provider secret still has to be entered securely.

---

## 2. Architecture document

### 2.1 Design principles

1. **Everything asynchronous.** No AI generation or render ever runs inside an HTTP request. Web and Telegram only enqueue jobs; workers execute them. This is what makes pause / resume / cancel and stage preservation possible.
2. **Providers are plugins.** One capability interface per media type; no model name hardcoded anywhere in the app. Routing selects a provider from its declared capabilities plus your quality/cost policy, and falls back automatically on failure.
3. **Pattern fidelity over prompt beauty.** The original Melhafa design is preserved by conditioning every image on real pixels (original photo + fabric crops + character references), never on a text description alone. Text prompts steer framing, composition and lighting only.
4. **Stages are idempotent and checkpointed.** Each stage writes its output to R2 plus a row in Postgres; a later failure preserves earlier work and resumes from the last completed stage, keyed by a params hash.
5. **Single owner everywhere.** Allow-list auth (one user), registration disabled, Telegram filtered by numeric user ID, audit log on every mutation.
6. **Secrets never leave the server.** Browser talks to backend; backend talks to providers using env/config-store values only. FFmpeg arguments are built as arrays and passed via execFile-style invocation — never string-interpolated into a shell.
7. **Nothing is "done" until ffprobe says so.** Render success requires validation of the actual output file (duration, streams, resolution, fps, audio loudness), not a zero exit code alone.

### 2.2 System components

```
                     ┌────────────────────────────┐
   Browser (owner)   │  Web App  React + Vite + TS │        Telegram (owner)
   Cloudflare Pages  │  Tailwind · shadcn/ui       │            │
                     │  React Query · i18next      │            │
                     │  ar (RTL default) · fr · en │            │
                     └──────────────┬─────────────┘            │ grammY (webhook/polling)
                                    │ REST + OpenAPI (auth)     │
                                    ▼                           ▼
                     ┌──────────────────────────────────────────────────┐
   Hostinger VPS     │  Backend API — Fastify 5 + Zod                    │
   Docker Compose    │  routes · validation · auth · audit · usage meter │
                     └───────┬──────────────────────────┬───────────────┘
                             │ enqueue                  │ read/write
                             ▼                          ▼
              ┌───────────────────────┐     ┌───────────────────────┐
              │ Redis (internal only) │     │ Neon PostgreSQL 18    │
              │ BullMQ queues         │     │ Drizzle ORM metadata  │
              └───────┬───────────────┘     └───────────┬───────────┘
                      │ consume                        │ asset keys
       ┌──────────────┼───────────────┬────────────────┐▼
       ▼              ▼               ▼                ▼
  worker-ai      worker-render   worker-telegram   Cloudflare R2
  fabric/CV      FFmpeg+FFprobe  progress + MP4     private buckets
  images/videos  normalize,trim  delivery            signed URLs
  audio mix      xfade, drawtext,
                 amix/loudnorm    scratch dirs
       │
       ▼
  Provider adapter registry (capabilities + fallback chain + cost policy)
  LLM/vision · image editing/i2i · image-to-video · TTS (ar/darija/fr/en) · music/SFX · CV (segmentation, matting, inpaint, upscale, IQA)
```

Queues (BullMQ): `fabric-analysis`, `image-gen`, `video-gen`, `audio-gen`, `render`, `notify`. Concurrency configurable per queue; `render` starts at **1 concurrent 1080p job** until the VPS benchmark says otherwise.

### 2.3 Campaign pipeline (your 22 steps, mapped)

| # | Stage | Worker | Persists | Failure handling |
|---|---|---|---|---|
| 1 | Upload original photo | api | R2 `original/`, `fabric_references` | reject non-image, MIME sniff, size cap |
| 2 | Fabric analysis | ai | `fabric_analysis` JSON + R2 crops | retry ×2, then manual re-run |
| 3 | Character selection | api | `campaigns.character_id` | n/a |
| 4 | Campaign concept | ai | `campaign_scripts.concept` | retry ×2 |
| 5 | Ad script + VO lines | ai | `campaign_scripts` | retry ×2 |
| 6 | Storyboard | ai | `storyboards`, `storyboard_scenes` | schema-validated, one auto-repair pass |
| 7 | Approval gate | api | `storyboards.status` | automatic policy or wait for owner |
| 8 | Scene reference images | ai | `scene_reference_images`, R2 | regenerate ≤ N per scene |
| 9 | Image QA | ai (CV + VLM) | QA score per image | below threshold → back to 8 |
| 10 | Scene clips (image-to-video) | ai | `video_clips`, R2 | poll/webhook, timeout, fallback provider, ≤ N takes |
| 11 | Clip QA | ai/render | motion/fabric-drift score | swap provider or re-take |
| 12 | Voice-over | ai | `audio_assets` | per-language TTS fallback chain |
| 13 | Background music | ai/library | `audio_assets` | licensed/user-owned preferred |
| 14 | SFX / ambience per scene | ai/library | `audio_assets` | optional, skippable |
| 15 | Editing timeline build | render | `editing_timelines` | deterministic from storyboard |
| 16 | Normalize clips | render | R2 `previews/normalized/` | ffprobe-gated |
| 17 | Assemble + transitions + graphics + audio mix | render | R2 `final/` | ffprobe-gated |
| 18 | Validate final output | render | `render_jobs.validation` JSON | invalid ⇒ job marked failed, never reported done |
| 19 | Thumbnail + SRT + metadata package | render/api | R2 `exports/` | retriable |
| 20 | Save campaign assets | api | `generated_assets` | idempotent upsert |
| 21 | Completion notification | telegram | message + campaign link | resend button |
| 22 | Deliver final MP4 | telegram/api | file or expiring link | size-aware upload strategy |

Each generation/render row records provider, model, parameter hash, wall-clock duration, units and estimated cost — that is what makes the cost report and the "don't regenerate the whole campaign" rule enforceable rather than aspirational.

### 2.4 Fabric-preservation strategy (the critical requirement)

The analyzer produces a structured `FabricProfile`; generation consumes it as *visual* conditioning:

1. **Reference set, not description.** Per campaign we build a small reference pack from the original photo: full garment, 2–4 pattern close-ups (crop coordinates come from the analyzer's ornament map), and a dominant/secondary colour swatch strip with measured hex values.
2. **Image-conditioned generation only.** Providers are ranked by whether they accept multiple reference images / subject-consistency features. Text-to-image without references is disallowed for product shots, allowed only for pure backgrounds.
3. **Segmentation-first compositing.** Where a provider cannot hold the pattern across a pose change, we generate the character/background separately, segment the garment region, and composite/inpaint the original pattern onto it (matting + perspective warp), then upscale. This keeps ornament geometry and colours authoritative from the source pixels.
4. **Colour lock.** Measured dominant/secondary hex values are written into every image prompt as explicit constraints and re-checked after generation by sampling the produced image; a ΔE drift beyond threshold fails QA.
5. **QA gate on every scene image** before any money is spent on video: pattern match, colour delta, garment integrity, artefact check, face/hands sanity, character identity match.
6. **No invented ornaments.** Negative prompts always include the anti-invention set (new patterns, changed colours, added motifs, altered border embroidery); the analyzer's `visible_defects` list is carried forward so defects are hidden, not amplified.

This is why the app must not rely on a single text-model description of the textile: intricate woven/embroidered geometry survives only through pixel-level conditioning and compositing.

### 2.5 Storyboard contract (authoritative shape)

Every scene carries exactly the fields you specified, validated by Zod before use:

`sceneId · title · durationSeconds · shotType · cameraAngle · cameraMovement · characterAction · fabricMovement · background · lighting · imagePrompt · negativePrompt · videoPrompt · soundEffects[] · voiceover · subtitle · transition · referenceImageInstructions`

Outputs: `storyboard.json` (machine), `storyboard.pdf` (human review, contact-sheet of scene references), editable in the web Storyboard page and approvable; the automatic policy decides whether production waits for approval.

Scene durations are constrained by provider reality: each requested duration is snapped to the nearest duration the selected video provider supports, and the total is reconciled against the target (15/30/45/60 s or custom) by adjusting clip trims in the timeline, never by asking a provider for an unsupported length.

### 2.6 Provider abstraction and capability discovery

`ProviderCapability` per provider/model: media types, accepted inputs (text, single image, multi-reference, mask), supported durations, aspect ratios, resolutions, fps, concurrency limits, webhook vs polling, price unit, rate limits, health state. A periodic discovery job refreshes these from provider docs/APIs and stores results in `ai_provider_configs`; routing reads the table, so adding or swapping a provider is configuration, not a code change.

Ranking inputs, in order: pattern/character consistency → visual quality → motion realism → duration & aspect support → availability/health → cost. Open-source is never a reason by itself to pick a model; HF candidates are evaluated head-to-head against commercial ones on the same reference pack and kept only if they win on quality or cost.

### 2.7 FFmpeg render engine design

Independent service, one job = one isolated scratch directory under `/data/render/<jobId>` (cleaned on success/failure with a sweeper for orphans).

- **Probe first:** ffprobe every input (streams, codec, pix_fmt, sar/dar, fps, duration, audio channels) → drives the filter graph; nothing is assumed.
- **Normalize:** scale + pad-or-crop to target canvas (1080×1920 primary, 1920×1080 secondary), fps 30 (24 optional), `setsar=1`, yuv420p, H.264 High profile, AAC 48 kHz stereo, timestamps reset. Aspect ratio is protected: crop or pad chosen per source DAR, never unconditional stretch.
- **Assemble:** order from storyboard; trims and speed adjustments (`setpts`/`atempo` for slow motion); transitions via `xfade` (+`acrossfade`) built as one `filter_complex` chain; optional zoompan Ken-Burns; colour grade as a bounded preset (no per-campaign arbitrary filters).
- **Graphics:** logo/product/promo text/price/CTA via `drawtext` with a declared fontfile; subtitles via `.ass` (libass) because ASS handles RTL and complex shaping far better than `drawtext`. **Arabic risk is called out explicitly:** `drawtext` does not perform Arabic shaping/joining reliably and renders reversed/disjoint letters; the engine therefore uses libass with an Arabic-capable font (Amiri or Noto Naskh Arabic) and pre-shaped text where needed, and the VPS inspection checks whether such a font is installed.
- **Audio:** VO track + music bed + per-scene SFX positioned by timeline offsets; `sidechaincompress` ducking music under VO, `afade` in/out at boundaries, `loudnorm` (EBU R128, target ≈ −14 LUFS for social) as the final pass.
- **Export:** final MP4, preview MP4 (smaller CRF/resolution for fast Telegram delivery), per-scene clips, thumbnail (best-frame probe), SRT, and `production-metadata.json` (storyboard, providers used, costs, ffprobe validation results).
- **Validate:** ffprobe the outputs; assert duration within tolerance of timeline, expected streams present, resolution/fps correct, non-silent audio where VO expected, and no decode errors from a full `-verror` pass. Success is recorded only when validation passes.
- **Safety:** args as arrays, fixed allow-list of filter presets, input paths confined to the job scratch dir, no user-controlled strings reaching a shell, timeouts and memory caps per job.

### 2.8 Audio engine

Script → VO per language (Algerian Arabic/Darija, MSA, French, English) through a configurable TTS chain with per-language ranking; music from licensed/user-owned beds first, generative bed second; SFX/ambience per scene from a curated library first, generated second. All assets get exact durations measured with ffprobe so the timeline can align and duck correctly. Music licensing provenance is stored per asset (`audio_assets.metadata.license`).

### 2.9 Web app information architecture

Login (owner-only) · Dashboard (active/completed/failed campaigns, images, videos, render time, AI cost, recent projects) · New Campaign (upload → style → duration → language → character → quality/cost policy → start) · Fabric Library (profiles reusable across campaigns) · Character Library (built-in + authorized custom references) · Storyboard (edit + approve) · Production Progress (live stage timeline, pause/resume/cancel, provider switches) · Scene Gallery (per-scene image/clip, regenerate one) · Video Preview (final + secondary + download package) · Campaign History · Media Library · AI Provider Settings (capability matrix, routing policy, budgets) · FFmpeg Settings (resolution/fps/profiles/subtitle style/loudness target) · Usage & Cost Reports.

RTL is first-class: Arabic default with logical CSS properties, mirrored layouts, Arabic numerals option, and the same three locales in Telegram messages.

### 2.10 Telegram bot

Same backend, same orchestrator — the bot is a thin client: commands `/start /new /projects /characters /status /settings /cancel`, an upload→character→preset→language→start flow, inline progress updates (stage transitions, not spam), final delivery of the video or a link plus the campaign URL. Owner gate: `telegram_owner` table holds your numeric user ID; every update is rejected before any logic runs unless it matches. Webhook secured with a secret token; polling mode available as fallback.

---

## 3. Repository structure

Your repository already hosts an unrelated project, so the studio lives entirely inside its own top-level folder and touches nothing else:

```
melhafa-studio/
  apps/
    web/                 React + Vite + TS + Tailwind + shadcn/ui + React Query + Router + i18next
      src/pages/…        Login, Dashboard, NewCampaign, FabricLibrary, CharacterLibrary,
                         Storyboard, ProductionProgress, SceneGallery, VideoPreview,
                         CampaignHistory, MediaLibrary, ProviderSettings, FfmpegSettings, UsageReports
      src/i18n/          ar (default, rtl) · fr · en
      src/lib/api.ts     typed client, auth header, error mapping
    backend/             Fastify 5 + Zod
      src/server.ts  src/routes/{health,auth,…}  src/modules/{fabric,characters,creative-director,
                       storyboard,images,videos,audio,render,orchestrator}/
      src/queue/{queues,workers}.ts   src/lib/{env,logger,r2,errors,usage}.ts
    workers/             entrypoints: ai, render, telegram (compose services)
  packages/
    core/                zod schemas + provider interfaces + capability registry
    db/                  drizzle.config.ts, src/schema.ts (all 21 tables), src/client.ts
    config/              tsconfig base, eslint, prettier
  scripts/
    inspect-vps.sh       read-only infrastructure inspection (this phase's deliverable)
  deploy/
    vps/README.md        non-destructive setup instructions (never executed by tooling)
    cloudflare/          Pages settings, R2 bucket plan, custom domain notes
  docker-compose.yml     backend-api · worker-ai · worker-render · worker-telegram · redis(internal)
  .env.example           every variable, documented, no values
  README.md              purpose, mermaid architecture, phase roadmap, dev loop, safety rules
```

Conventions: TypeScript ESM throughout, Node 20+, pnpm workspaces, strict tsconfig, ESLint + Prettier, Vitest for units, one changeset per phase, CI runs typecheck + lint + tests + build.

---

## 4. Database design (Neon + Drizzle)

All 21 tables from your spec, created additively through Neon's migration-branch flow (prepare on a temporary branch, verify, then apply with your approval). Relationship outline:

- `projects` → `campaigns` (1:N) → `campaign_scripts`, `storyboards` → `storyboard_scenes` (ordered)
- `fabric_references` → `fabric_analysis` (versioned profiles, reusable across campaigns)
- `character_profiles` → `character_images` (built-in or authorized uploads)
- `storyboard_scenes` → `scene_reference_images` (takes) → `video_clips` (each bound to its scene + chosen reference image)
- `generation_jobs` (every provider call: stage, provider, model, params hash, status, attempts, cost, error) — the spine of retry/preservation logic
- `audio_assets`, `editing_timelines`, `render_jobs` (with `validation` jsonb), `generated_assets` (R2 keys, mime, size, checksum)
- `ai_provider_configs` (capability matrix + routing policy + enabled flag), `provider_usage` (units, spend estimate per day/campaign)
- `application_settings` (approval policy, quality preset, budget caps, render concurrency), `telegram_owner`, `audit_logs` (actor, action, entity, before/after, ip)

Indexes on `status`, `campaign_id`, `created_at`; `params_hash` unique per stage to make replays cheap and idempotent. Large binaries never enter Postgres — only R2 keys and metadata.

---

## 5. Implementation roadmap (phases 1–13, with gates)

| Phase | Scope | Exit gate | Depends on |
|---|---|---|---|
| 1 | Inspection + architecture (this doc) + VPS inspection script + scaffold PR | You approve the design and the infra gaps are closed or explicitly accepted | — |
| 2 | Monorepo foundation, Docker Compose, CI, env validation, health endpoints | `pnpm typecheck/build/test` green; compose starts locally | 1 |
| 3 | Neon schema + migrations via branch flow; R2 buckets + signed URLs; owner-only auth | Migration applied on `production` after your approval; upload→signed-read round trip works | Cloudflare token |
| 4 | Web app shell (14 pages, RTL) + Telegram bot skeleton on the same API | Both clients create/list a campaign end-to-end | 3 |
| 5 | Fabric Analyzer + Character Engine (built-in + custom references) | Real photo → validated `FabricProfile`; characters reusable | 3 |
| 6 | Creative Director + Storyboard generator (JSON + PDF + edit/approve) | Storyboard validates against schema; edits persist; PDF renders | 5 |
| 7 | Scene image generation + QA gate + provider registry | Pattern/colour QA passes on ≥ 80 % of scenes within regeneration budget | 6 |
| 8 | Video generation engine (i2v, async, polling/webhooks, fallback, takes, per-scene regen) | Every scene has an accepted clip | 7 |
| 9 | Audio engine (VO languages, music, SFX, ducking prep) | Timed audio assets for one full campaign | 6 |
| 10 | FFmpeg render engine (normalize, assemble, transitions, graphics, ASS subtitles, mix, validate, exports) | Final MP4 passes ffprobe validation incl. readable Arabic subtitles | 8, 9 · VPS ffmpeg/fonts |
| 11 | Orchestrator (state machine, pause/resume/cancel, stage preservation, budgets) | Interrupt/resume tested; no wasted regeneration | 10 |
| 12 | Delivery: final MP4 + export package + Telegram notification + links | One command → finished film in Telegram + downloadable package | 11 |
| 13 | Hardening: security review, rate/abuse limits, monitoring, backups, cost dashboards, runbooks | Signed-off checklist; restore drill done | 12 |

Sequencing note: phases 7–10 are where real money appears, so the budget caps and provider policies from phase 6 are enforced before them, and phase 10 is gated on the VPS actually having FFmpeg + an Arabic-capable font (unknown today).

---

## 6. Environment variable checklist

Values belong only in the VPS env file / MAX's secure key entry (Settings → My connections or an API card) — never in chat, never in the frontend bundle.

Runtime/platform
- `NODE_ENV`, `LOG_LEVEL`, `PORT`, `API_PUBLIC_URL`, `WEB_PUBLIC_URL`, `CORS_ALLOWED_ORIGINS`
- `DATABASE_URL` (Neon pooled connection string) · `NEON_PROJECT_ID` · `NEON_BRANCH_PRODUCTION`
- `REDIS_URL` (internal network only) · `BULLMQ_CONCURRENCY_AI` · `BULLMQ_CONCURRENCY_RENDER`
- `AUTH_SECRET` / JWT signing key · `OWNER_USER_ID` · `REGISTRATION_ENABLED=false`
- Clerk (if chosen): `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_ALLOWED_EMAIL`

Storage (Cloudflare R2)
- `CLOUDFLARE_ACCOUNT_ID` · `CLOUDFLARE_API_TOKEN` (least privilege) · `R2_ENDPOINT`
- `R2_ACCESS_KEY_ID` · `R2_SECRET_ACCESS_KEY` · `R2_BUCKET_PRIVATE` · `R2_BUCKET_ASSETS`
- `SIGNED_URL_TTL_SECONDS` · `MAX_UPLOAD_BYTES` · `RENDER_SCRATCH_DIR` · `RENDER_TMP_TTL_HOURS`

AI providers (each optional; presence enables that adapter)
- `ANTHROPIC_API_KEY` (vision, creative director, QA reasoning)
- `OPENAI_API_KEY` (image editing/generation) · `GEMINI_API_KEY` (image) · `FLUX_API_KEY`
- `RUNWAY_API_KEY` · `KLING_API_KEY` · `SEEDANCE_API_KEY` · `VEO_API_KEY` (image-to-video)
- `HF_TOKEN` (inference providers: segmentation, matting, inpaint, upscale, IQA, Arabic TTS evaluation)
- `AI_GATEWAY_*` if you route providers through a gateway
- `TTS_PROVIDER` + `TTS_API_KEY` (voice-over) · `MUSIC_PROVIDER` + `MUSIC_API_KEY`

Telegram
- `TELEGRAM_BOT_TOKEN` · `TELEGRAM_ALLOWED_USER_ID` (numeric) · `TELEGRAM_WEBHOOK_SECRET` · `TELEGRAM_MODE=webhook|polling`

Policy
- `CAMPAIGN_AUTO_APPROVE=true|false` · `MAX_SPEND_PER_CAMPAIGN` · `MAX_IMAGE_REGENS_PER_SCENE` · `MAX_VIDEO_TAKES_PER_SCENE` · `RENDER_MAX_CONCURRENT_1080P=1`

---

## 7. Estimated operating costs (monthly, personal volume)

Estimates only — confirm against current published prices and your own provider dashboards. Fixed infrastructure is small; **AI generation dominates**.

| Item | Notes | Typical range |
|---|---|---|
| Hostinger VPS | KVM 2 (4 vCPU / 8 GB / 50 GB) is the realistic minimum for 1080p FFmpeg + workers; KVM 8 (8 vCPU / 32 GB / 400 GB) if you want parallel renders or local models | ~€8–12 → ~€30–45 |
| Cloudflare Pages | Free tier covers a private dashboard | €0 |
| Cloudflare R2 | 10 GB free storage; egress free. 20 finished 1080p ads ≈ 2–6 GB plus intermediates | ~€0–2 |
| Neon | Free/Launch tier fits metadata comfortably (~32 MB used now); paid only if you need more compute/storage or longer PITR | €0 → ~€19 |
| Redis | On-VPS container, no extra line item | €0 |
| LLM (analysis, concept, storyboard, QA) | Vision + long structured generations; scales with campaigns | ~€5–30 |
| Scene images | 8–12 scenes × 2–4 takes per campaign, quality-tier models | ~€10–60 per campaign |
| Scene video clips | The big one: 8–12 clips per campaign on premium i2v tiers | ~€30–200 per campaign |
| Voice-over + music/SFX | TTS minutes + licensed beds or generated music | ~€2–20 |
| **Realistic monthly total** | depends almost entirely on how many campaigns you produce | **~€60–150 for 2–4 campaigns**, higher if you iterate heavily on clips |

Cost controls built into the design: per-campaign hard budget (`MAX_SPEND_PER_CAMPAIGN`), preview-before-video mode (cheap image QA gate precedes expensive clips), take limits per scene, provider preference ordered by cost among quality-acceptable options, stage preservation so failures never re-bill completed stages, and a usage report per campaign/provider/day.

---

## 8. Security assessment

**Threat posture for a single-owner tool:** the main risks are credential leakage, an exposed queue/storage endpoint, prompt-and-path injection into FFmpeg, an open Telegram webhook, and unauthenticated uploads consuming your paid AI budget.

Controls, mapped to your section 18:

- Access: registration disabled; one `OWNER_USER_ID`; all backend endpoints behind session/JWT verification; no anonymous routes except health; web app served with `X-Frame-Options: DENY`, strict CSP, HSTS via Cloudflare.
- Telegram: numeric allow-list checked before any handler runs; webhook secret; commands carry no privileged parameters; bot token rotation documented.
- Secrets: env-only, never in the bundle or repo; `.env.example` documents names without values; least-privilege Cloudflare token (R2 read/write + Pages edit, not zone-wide); keys pasted only into the secure entry UI, never into chat; anything accidentally pasted is treated as compromised and rotated.
- Uploads: MIME sniffing plus magic-byte check, dimension/size caps, EXIF stripped, re-encoded originals for analysis, per-user quota trivial (one owner) but per-request body limit enforced.
- Storage: private buckets, no public dev URL, short-TTL signed URLs, campaign-scoped key prefixes, orphan-file sweeper for scratch dirs.
- Database: TLS-only Neon connections, pooled role for the app, migrations through the reviewed branch flow, no destructive SQL executed autonomously, PITR retained (currently 6 h — consider raising it), nightly logical dump to R2 as a second line of defence.
- Command execution: FFmpeg invoked as argv arrays with a fixed filter-preset allow-list; all paths resolved inside the job scratch directory and rejected on traversal; storyboard text never reaches a shell; per-job timeout, memory cap and cleanup hook.
- Queue/network: Redis bound to the internal network with no published port, protected mode on, optional AUTH; reverse proxy terminates TLS; workers run as unprivileged containers with read-only rootfs where possible; host firewall default-deny except 443.
- Audit & monitoring: `audit_logs` on every mutation and every provider call; failure alerts to Telegram; disk/memory/queue-depth watchdog; cost alert when a campaign crosses its budget.
- Supply chain: lockfile-committed dependencies, `pnpm audit`/OSV scan in CI, pinned base images, no postinstall trust for rendering deps.
- Backups/DR: Neon PITR + nightly dump, R2 versioning on `final/` and `exports/`, infra described as code so a rebuild is scripted rather than hand-made.

Open risks right now: (1) the Cloudflare token is unusable, so bucket policy and access cannot be audited; (2) the VPS is unmeasured, so we don't know whether it can hold a 1080p render without dropping frames or whether an Arabic font exists for subtitles; (3) the sandbox fault blocks local verification of toolchains; (4) the existing repository is **public** — if the studio lands there, its structure becomes visible even though secrets do not, so a private repository is worth considering.

---

## 9. Decisions I need from you

1. **Repository**: keep the studio in the existing public repo (`melhafa-studio/` folder) or create a **private** repo for it? Recommended: private repo.
2. **Frontend hosting**: Cloudflare Pages (as briefed) or the already-linked Vercel? Recommended: Cloudflare Pages, since R2 is required anyway and Pages+R2 share the account.
3. **Auth**: Clerk (fastest, external dependency) or self-hosted single-owner session auth (no third party, less code)? Recommended: self-hosted owner-only auth for a personal tool.
4. **Approval policy**: fully automatic including storyboard, or pause once for storyboard approval on the first campaigns? Recommended: pause for the first few campaigns, then flip `CAMPAIGN_AUTO_APPROVE=true`.
5. **Video provider priority** once several keys exist: quality-first (premium tiers) or cost-first (cheapest acceptable)? Recommended: quality-first for hero scenes, cost-first for cutaways.

## 10. What you must supply (secure entry points, never in chat)

- A working **Cloudflare API token** (R2 + Pages scopes) re-saved in Settings → My connections / API connections — the current one fails authentication.
- **Hostinger VPS details**: either register the server so MAX can inspect it, or run `melhafa-studio/scripts/inspect-vps.sh` yourself and paste back the two generated report files. Note that remote SSH execution is currently disabled on this deployment, so the script route is the reliable one.
- **AI provider keys** for whichever adapters you want active (Anthropic, image, video, TTS, HF).
- **Telegram**: create the bot with @BotFather, paste the token in Settings → My connections → Telegram, send `/bind` to it, and give me your numeric Telegram user ID for the allow-list.
- Your **logo/brand assets** and, for custom characters, the authorized reference photographs.

## 11. Next actions

- In flight: a background MAX Code run is building `scripts/inspect-vps.sh` + the monorepo scaffold and opening a pull request; its commit SHAs and PR URL will be reported here once verified.
- Then, on your answers above: phase 2 (foundation + compose + CI) and phase 3 (schema migration through Neon's branch flow, R2 buckets, owner auth) — with your approval before anything touches shared infrastructure.
