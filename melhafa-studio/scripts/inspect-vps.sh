#!/usr/bin/env bash
# AI Saharan Melhafa Studio — Phase 1 infrastructure inspection
# READ-ONLY by design: it never installs, stops, restarts, deletes or changes anything.
# Writes ./infra-report-<stamp>.md + ./infra-report-<stamp>.json and prints the markdown.
#
# Usage (on the Hostinger Ubuntu VPS):
#   bash inspect-vps.sh
#   bash inspect-vps.sh --env-file /srv/melhafa/.env   # checks expected env var NAMES only; values are never printed
#   sudo -E bash inspect-vps.sh                        # optional: process names on ports + docker without group membership
#
# Paste the generated report back into MAX to finish Phase 1.

set -uo pipefail

STAMP="$(date +%Y%m%d-%H%M)"
OUT_MD="infra-report-${STAMP}.md"
OUT_JSON="infra-report-${STAMP}.json"
BENCH=0
ENVFILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --bench) BENCH=1 ;;
    --env-file) shift; ENVFILE="${1:-}" ;;
    *) echo "unknown option: $1" >&2 ;;
  esac
  shift || true
done

have() { command -v "$1" >/dev/null 2>&1; }

T=""
if have timeout; then T="timeout 25"; fi

run() {
  local label="$1"; shift
  {
    printf '\n### %s\n' "$label"
    printf '```\n'
    if [ $# -eq 0 ] || ! have "$1"; then
      printf 'NOT AVAILABLE: %s\n' "${1:-?}"
    else
      $T "$@" </dev/null 2>&1 | head -60 || printf '(non-zero exit above)\n'
    fi
    printf '```\n'
  } >> "$OUT_MD"
}

kv() {
  local key="$1" val="$2"
  val="$(printf '%s' "$val" | tr '\n' ' ' | cut -c1-200)"
  printf -- '- **%s**: `%s`\n' "$key" "$val" >> "$OUT_MD"
  printf '%s\t%s\n' "$key" "$val" >> "$KVFILE"
}

add_gap() { printf -- '- %s\n' "$1" >> "$OUT_MD"; }

: > "$OUT_MD"
KVFILE="$(mktemp)"

{
  printf '# Infrastructure report — AI Saharan Melhafa Studio (Phase 1)\n\n'
  printf -- '- Generated: `%s` by user `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(id -un 2>/dev/null || echo unknown)"
  printf -- '- Mode: **read-only** — nothing was installed, stopped, restarted, deleted or modified.\n'
} >> "$OUT_MD"

# ---------- 1. host & OS ----------
printf '\n## 1. Host and OS\n' >> "$OUT_MD"
kv hostname "$(hostname 2>/dev/null)"
kv os "$( (. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-unknown}") )"
run "uname -a" uname -a
run "lsb_release -a" lsb_release -a
run "uptime" uptime
run "systemd-detect-virt" systemd-detect-virt
kv kernel "$(uname -r 2>/dev/null)"

# ---------- 2. CPU ----------
printf '\n## 2. CPU\n' >> "$OUT_MD"
kv cpu_cores "$(nproc 2>/dev/null)"
run "lscpu" lscpu
run "cpu model" sh -c "grep -m1 'model name' /proc/cpuinfo"
run "load average" sh -c "cat /proc/loadavg"

# ---------- 3. memory ----------
printf '\n## 3. Memory\n' >> "$OUT_MD"
run "free -h" free -h
run "swap devices" swapon --show

# ---------- 4. disk ----------
printf '\n## 4. Disk\n' >> "$OUT_MD"
run "df -h" df -h
run "df -i (inodes)" df -i
run "largest content dirs" sh -c "for d in /var/www /opt /srv /home /root; do [ -d \"\$d\" ] && du -sh \$d 2>/dev/null; done | sort -h | tail -10"

# ---------- 5. docker & services ----------
printf '\n## 5. Docker and services\n' >> "$OUT_MD"
run "docker --version" docker --version
run "docker compose version" docker compose version
run "docker info (needs docker group or sudo)" docker info
run "containers (names/status/ports)" sh -c "docker ps -a --format '{{.Names}} | {{.Status}} | {{.Ports}}'"
run "compose files found (maxdepth 4)" sh -c "find / -maxdepth 4 -name docker-compose.yml -not -path '*/node_modules/*' -not -path '/proc/*' -not -path '/sys/*' 2>/dev/null | head -20"
run "running services" sh -c "systemctl list-units --type=service --state=running --no-pager --no-legend | awk '{print \$1}' | head -40"
run "failed units" systemctl --failed --no-pager

# ---------- 6. network / ports ----------
printf '\n## 6. Ports and reverse proxy\n' >> "$OUT_MD"
run "listening TCP sockets" sh -c "ss -ltnp 2>/dev/null | head -40 || netstat -ltn 2>/dev/null | head -40"
run "nginx" sh -c "nginx -v 2>&1; ls /etc/nginx/sites-enabled 2>/dev/null"
run "caddy" sh -c "caddy version 2>/dev/null"
run "traefik version" traefik version
run "ufw status verbose" sh -c "ufw status verbose 2>/dev/null || echo 'ufw unreadable (try with sudo)'"
run "iptables rules (first 30)" sh -c "iptables -S 2>/dev/null | head -30 || echo 'iptables unreadable (try with sudo)'"

# ---------- 7. toolchain ----------
printf '\n## 7. Toolchain\n' >> "$OUT_MD"
run "node -v" node -v
run "npm -v" npm -v
run "pnpm -v" pnpm -v
run "yarn -v" yarn -v
run "git --version" git --version
run "python3 --version" python3 --version
run "openssl version" openssl version
run "redis-cli ping (local redis?)" sh -c "redis-cli -h 127.0.0.1 ping 2>&1 | head -2"
run "psql client" psql --version

# ---------- 8. ffmpeg (core component) ----------
printf '\n## 8. FFmpeg / FFprobe\n' >> "$OUT_MD"
run "ffmpeg -version" sh -c "ffmpeg -version 2>&1 | head -4"
run "ffprobe -version" sh -c "ffprobe -version 2>&1 | head -2"
run "filters required by the render engine" sh -c "ffmpeg -hide_banner -filters 2>/dev/null | grep -oE '\b(xfade|acrossfade|subtitles|drawtext|loudnorm|sidechaincompress|zoompan|overlay|setsar|fps|format|concat|trim)\b' | sort -u"
run "encoders (x264/x265/aac/mp3)" sh -c "ffmpeg -hide_banner -encoders 2>/dev/null | grep -iE 'libx264|libx265|\baac\b|libmp3lame' | head -10"
run "hwaccels" sh -c "ffmpeg -hide_banner -hwaccels 2>/dev/null | head -10"
run "build config (libass/harfbuzz/freetype/fontconfig)" sh -c "ffmpeg -hide_banner -buildconf 2>/dev/null | grep -iE 'libass|harfbuzz|freetype|fontconfig|x264' | head -12"
run "nvidia-smi (GPU encode?)" nvidia-smi

# ---------- 9. fonts (Arabic rendering risk) ----------
printf '\n## 9. Fonts — Arabic RTL text/subtitle rendering\n' >> "$OUT_MD"
run "installed font count" sh -c "fc-list 2>/dev/null | wc -l"
run "Arabic-capable families" sh -c "fc-list :lang=ar family 2>/dev/null | sort -u | head -20"
run "known good candidates" sh -c "fc-list 2>/dev/null | grep -iE 'amiri|noto.*(arab|naskh)|kufi|scheherazade' | head -20"

# ---------- 10. env var presence (never values) ----------
printf '\n## 10. Environment variables — presence check only\n' >> "$OUT_MD"
if [ -n "$ENVFILE" ] && [ -f "$ENVFILE" ]; then
  for n in DATABASE_URL REDIS_URL CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN R2_ENDPOINT \
           R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET_PRIVATE R2_BUCKET_ASSETS \
           ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY RUNWAY_API_KEY KLING_API_KEY \
           SEEDANCE_API_KEY VEO_API_KEY HF_TOKEN TTS_API_KEY TELEGRAM_BOT_TOKEN \
           TELEGRAM_ALLOWED_USER_ID AUTH_SECRET OWNER_USER_ID MAX_SPEND_PER_CAMPAIGN \
           RENDER_SCRATCH_DIR RENDER_MAX_CONCURRENT_1080P; do
    if grep -qE "^[[:space:]]*(export[[:space:]]+)?${n}=" "$ENVFILE" 2>/dev/null; then
      printf -- '- %s: present\n' "$n" >> "$OUT_MD"
    else
      printf -- '- %s: MISSING\n' "$n" >> "$OUT_MD"
    fi
  done
  printf '\n(Only names were checked. No value was read, printed or written to the report.)\n' >> "$OUT_MD"
else
  printf 'No env file inspected — re-run with `--env-file /path/to/.env` to check which expected variables exist (names only).\n' >> "$OUT_MD"
fi

# ---------- 11. optional benchmark ----------
if [ "$BENCH" -eq 1 ]; then
  printf '\n## 11. Render benchmark (--bench only; writes temp files under /tmp and removes them)\n' >> "$OUT_MD"
  run "1080x1920@30 x264 encode speed (20 s clip)" sh -c '
    V=/tmp/melhafa-bench-$$.mp4
    if ffmpeg -y -hide_banner -loglevel error -f lavfi -i color=c=gray:s=1080x1920:d=20:r=30 -c:v libx264 -preset medium -crf 20 "$V" 2>/tmp/melhafa-bench-$$.log; then
      T=$( { time -p ffmpeg -y -hide_banner -loglevel error -f lavfi -i color=c=gray:s=1080x1920:d=20:r=30 -c:v libx264 -preset medium -crf 20 "$V" >/dev/null 2>&1 ; } 2>&1 | awk "/^real/{print \$2}")
      echo "encoded 20 s of 1080x1920@30 in ${T} s wall time (under ~20 s = near-realtime single stream)"
      ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,nb_frames -of default=nw=1 "$V"
    else
      echo "encode failed:"; cat /tmp/melhafa-bench-$$.log
    fi
    rm -f "$V" /tmp/melhafa-bench-$$.log'
  run "disk write throughput (512 MB, fdatasync)" sh -c 'dd if=/dev/zero of=/tmp/melhafa-dd-$$.tmp bs=1M count=512 conv=fdatasync 2>&1 | tail -1; rm -f /tmp/melhafa-dd-$$.tmp'
fi

# ---------- 12. automatic gap detection ----------
printf '\n## 12. Automatic gap detection\n' >> "$OUT_MD"
have docker || add_gap "Docker CLI not found — backend and workers cannot run as containers here."
have ffmpeg || add_gap "FFmpeg missing — the render engine (Phase 10) cannot run here."
have ffprobe || add_gap "FFprobe missing — rendered outputs could never be validated, so success could not be honestly reported."
if have ffmpeg; then
  FL="$(ffmpeg -hide_banner -filters 2>/dev/null)"
  printf '%s' "$FL" | grep -qw xfade || add_gap "ffmpeg has no xfade — crossfade/dissolve transitions unavailable (rebuild with a full ffmpeg)."
  printf '%s' "$FL" | grep -qw subtitles || add_gap "ffmpeg has no subtitles/libass filter — ASS/SRT burn-in unavailable; Arabic shaping would fail."
  printf '%s' "$FL" | grep -qw loudnorm || add_gap "ffmpeg has no loudnorm — audio normalization needs another method."
fi
if have fc-list; then
  [ -z "$(fc-list :lang=ar 2>/dev/null)" ] && add_gap "NO Arabic-capable font installed — Arabic drawtext/subtitles would render reversed or as boxes. Install Amiri or Noto Naskh Arabic before Phase 10."
else
  add_gap "fontconfig/fc-list unavailable — Arabic font availability could not be verified."
fi
MEM_MB="$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null)"
DISK_GB="$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')"
CORES="$(nproc 2>/dev/null)"
[ -n "${MEM_MB:-}" ] && [ "$MEM_MB" -lt 3800 ] && add_gap "RAM ≈ ${MEM_MB} MB — below the ~4 GB floor for one concurrent 1080p render plus API/AI workers."
[ -n "${DISK_GB:-}" ] && [ "$DISK_GB" -lt 15 ] && add_gap "Only ${DISK_GB} GB free on / — scratch and render directories need headroom; tighten cleanup policy now."
[ -n "${CORES:-}" ] && [ "$CORES" -lt 4 ] && add_gap "Only ${CORES} vCPU — keep render concurrency at 1 and expect slow encodes."
if ss -ltn 2>/dev/null | grep -q ':6379'; then
  printf -- '- NOTE: Redis is listening on port 6379. Confirm it is bound to 127.0.0.1 / an internal network and never published publicly.\n' >> "$OUT_MD"
fi
printf '\nIf section 12 lists no gaps, this host is ready for Phase 2.\n' >> "$OUT_MD"

# ---------- machine-readable JSON ----------
if have python3; then
  python3 - "$KVFILE" "$OUT_JSON" <<'PY' 2>/dev/null || true
import json, sys, os, subprocess
kv = {}
for line in open(sys.argv[1], encoding='utf-8', errors='replace'):
    if '\t' in line:
        k, v = line.rstrip('\n').split('\t', 1)
        kv[k] = v
out = {"host": kv, "markdownReport": os.path.basename(sys.argv[1]).replace('.tmp','')}
try:
    out["ffmpegVersion"] = subprocess.run("ffmpeg -version", shell=True, capture_output=True, text=True, timeout=20).stdout.splitlines()[0]
except Exception:
    out["ffmpegVersion"] = None
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), indent=2, ensure_ascii=False)
PY
fi
rm -f "$KVFILE"

printf '\n---\n\nReport written: %s%s\nPaste the markdown content back into MAX (or attach the file).\n' "$OUT_MD" "$([ -f "$OUT_JSON" ] && printf ' and %s' "$OUT_JSON")" >> "$OUT_MD"
cat "$OUT_MD"
