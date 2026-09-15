# modules/downloader.py — Мультимедиа комбайн v7.5 (Piped Direct Stream + Safari Client Bypass)
import os
import re
import sys
import uuid
import json
import time
import urllib.parse
import binascii
import shutil
import asyncio
import logging
import tempfile
import aiohttp
from pathlib import Path
from telethon import events, Button
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    InputBotInlineResult,
    InputBotInlineMessageText
)

from core.config import OWNER_ID
from core.db import is_authorized

TITLE = "⤓ Media Grabber Pro"
BANNER = "https://raw.githubusercontent.com/diademma/Userbot/main/assets/LLEHTABPA.jpg"
COMMANDS = (
    "• sudo {ссылка} — Интерактивная карточка с превью и кнопками\n"
    "• .dl {ссылка} — Быстрый вызов карточки\n"
    "• .dl {ссылка} [00:10-00:40] — Скачивание с нарезкой\n\n"
    "Мульти-поиск аудио:\n"
    "├ 🌐 Hitmo & Sefon (СНГ и мировые треки)\n"
    "├ ☁️ SoundCloud (Оригинальные загрузки)\n"
    "└ 🔴 Piped Stream + Safari Bypass (YouTube без капчи)"
)

# Глушим технический спам Telethon
for noisy in ("telethon.client.updates", "telethon.client.uploads", "telethon.network.mtprotosender"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

LOGGER = logging.getLogger("MediaGrabber")

SESSIONS = {}
WAITING_TRIM = {}

UNIVERSAL_FORMAT = "bv*+ba/b/bestvideo/bestaudio/best"

PIPED_INSTANCES = [
    "https://api.piped.private.coffee",
    "https://pipedapi.reallyaweso.me",
    "https://piped-api.lunar.icu",
    "https://pipedapi.leptons.xyz"
]

class StatusThrottler:
    def __init__(self, event, interval=3.0):
        self.event = event
        self.interval = interval
        self.last_update = 0.0
        self.last_text = ""

    async def update(self, text: str, force: bool = False):
        now = time.time()
        if not force and (now - self.last_update < self.interval):
            return
        if text == self.last_text:
            return
        self.last_update = now
        self.last_text = text
        if self.event:
            try:
                await self.event.edit(text, parse_mode="html")
            except Exception:
                pass

def get_ffmpeg_path():
    p = shutil.which("ffmpeg")
    if p: return p
    home_p = os.path.expanduser("~/.local/bin/ffmpeg")
    if os.path.isfile(home_p): return home_p
    return "ffmpeg"

def detect_platform(url: str) -> str:
    url_l = url.lower()
    if any(d in url_l for d in ("youtube.com", "youtu.be")): return "youtube"
    if any(d in url_l for d in ("soundcloud.com", "on.soundcloud.com")): return "soundcloud"
    if any(d in url_l for d in ("spotify.com", "spotify.link")): return "spotify"
    if "tiktok.com" in url_l: return "tiktok"
    if "instagram.com" in url_l: return "instagram"
    if any(d in url_l for d in ("pinterest.com", "pin.it")): return "pinterest"
    return "generic"

async def ensure_latest_ytdlp():
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        LOGGER.info("🚀 [MediaGrabber] yt-dlp обновлен до актуальной версии.")
    except Exception as e:
        LOGGER.warning(f"Ошибка обновления yt-dlp: {e}")

# Проверяем наличие YouTube куков в переменных окружения
def setup_cookies(tmp_dir: Path) -> str | None:
    cookies_content = os.getenv("YT_COOKIES") or os.getenv("YOUTUBE_COOKIES")
    if cookies_content and len(cookies_content.strip()) > 50:
        c_path = tmp_dir / "cookies.txt"
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(cookies_content.strip())
        return str(c_path)
    return None

# --- ПАРСЕР SPOTIFY ---
async def get_spotify_meta(url: str) -> dict | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }
    m = re.search(r'spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)', url)
    if not m: return None

    res_type, res_id = m.group(1), m.group(2)
    embed_url = f"https://open.spotify.com/embed/{res_type}/{res_id}"
    direct_url = f"https://open.spotify.com/{res_type}/{res_id}"

    title = ""
    artist = ""
    thumb = ""

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(embed_url, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    m_next = re.search(r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>', html, re.DOTALL)
                    if m_next:
                        try:
                            d = json.loads(m_next.group(1))
                            props = d.get('props', {}).get('pageProps', {})
                            entity = props.get('state', {}).get('data', {}).get('entity', {}) or props.get('track') or props.get('album') or {}
                            if entity.get('name'): title = entity['name']
                            if entity.get('artists'): artist = ", ".join([a.get('name', '') for a in entity['artists'] if a.get('name')])
                            vis = entity.get('visualIdentity', {}).get('image', [])
                            if vis and isinstance(vis, list) and vis[0].get('url'): thumb = vis[0]['url']
                            elif entity.get('album', {}).get('images'): thumb = entity['album']['images'][0].get('url', '')
                        except Exception: pass

                    if not title:
                        m_og_title = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html)
                        if m_og_title: title = m_og_title.group(1).strip()
                    if not artist:
                        m_og_desc = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', html)
                        if m_og_desc and "·" in m_og_desc.group(1):
                            artist = m_og_desc.group(1).split("·")[0].strip()
                    if not thumb:
                        m_og_img = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html)
                        if m_og_img: thumb = m_og_img.group(1).strip()
    except Exception: pass

    if not artist or not title:
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(direct_url, timeout=8) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        if not title:
                            m_og_title = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html)
                            if m_og_title: title = m_og_title.group(1).strip()
                        if not artist:
                            m_og_desc = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', html)
                            if m_og_desc and "·" in m_og_desc.group(1):
                                artist = m_og_desc.group(1).split("·")[0].strip()
        except Exception: pass

    return {"title": title or "Track", "author": artist, "thumb": thumb} if title else None

# --- ЗАГРУЗКА АУДИО ИЗ YOUTUBE (САФАРИ-КЛИЕНТ + КУКИ) ---
async def download_youtube_safari(video_id: str, target_file: Path) -> bool:
    """Качает аудио с YouTube через Safari HLS клиент (обход анти-бота)"""
    import yt_dlp
    loop = asyncio.get_event_loop()
    yt_url = f"https://www.youtube.com/watch?v={video_id}"

    cookies_file = setup_cookies(target_file.parent)

    yt_opts = {
        'ffmpeg_location': get_ffmpeg_path(),
        'quiet': True,
        'no_warnings': True,
        'format': 'ba/b/bestaudio/best',
        'outtmpl': str(target_file.with_suffix('')),
        'extractor_args': {
            'youtube': {
                'player_client': ['web_safari', 'tv_downgraded', 'android_vr'],
                'player_skip': ['web', 'mweb', 'android']
            }
        },
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }]
    }
    if cookies_file:
        yt_opts['cookiefile'] = cookies_file

    try:
        def run_dl():
            with yt_dlp.YoutubeDL(yt_opts) as ydl:
                return ydl.extract_info(yt_url, download=True)

        await loop.run_in_executor(None, run_dl)
        if target_file.exists() and target_file.stat().st_size > 400_000:
            return True
    except Exception as e:
        LOGGER.info(f"  ├ ⚠️ Safari-клиент YouTube: {e}")

    return False

# --- МУЛЬТИ-ШЛЮЗ ПОИСКА АУДИО ---
async def search_and_download_audio(query: str, target_file: Path, throttler: StatusThrottler) -> bool:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    clean_q = re.sub(r"\s+", " ", query).strip()
    encoded_query = urllib.parse.quote(clean_q)

    LOGGER.info(f"🔎 [Поиск] Старт поиска трека: '{clean_q}'")

    # 1. Hitmo
    LOGGER.info(f"  ├ 🌐 [1/4] Проверяю Hitmo...")
    await throttler.update(f"🔎 <b>Поиск:</b> <code>{clean_q}</code>\n├ 🌐 <i>Hitmo...</i>")
    try:
        hitmo_url = f"https://rus.hitmotop.com/search?q={encoded_query}"
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(hitmo_url, timeout=6) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    matches = re.findall(r'href=["\'](https?://[^"\']+/get/music/[^"\']+\.mp3)["\']', html)
                    if not matches:
                        matches = re.findall(r'href=["\'](/get/music/[^"\']+\.mp3)["\']', html)
                        matches = [f"https://rus.hitmotop.com{m}" for m in matches]

                    if matches:
                        LOGGER.info(f"  ├ ✅ Hitmo: трек найден! Скачиваю...")
                        await throttler.update(f"⬇️ <b>Hitmo:</b> скачиваю <code>{clean_q}</code>...", force=True)
                        async with session.get(matches[0], timeout=25) as dl_resp:
                            if dl_resp.status == 200:
                                with open(target_file, "wb") as f: f.write(await dl_resp.read())
                                if target_file.stat().st_size > 500_000:
                                    LOGGER.info(f"  └ 🎉 Успешно скачано с Hitmo!")
                                    return True
    except Exception as e:
        LOGGER.info(f"  ├ ⚠️ Hitmo: {e}")
    LOGGER.info(f"  ├ ❌ Hitmo: трек не найден")

    # 2. Sefon
    LOGGER.info(f"  ├ 🌐 [2/4] Проверяю Sefon...")
    await throttler.update(f"🔎 <b>Поиск:</b> <code>{clean_q}</code>\n├ 🌐 <i>Sefon...</i>")
    try:
        sefon_url = f"https://sefon.pro/search/?q={encoded_query}"
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(sefon_url, timeout=6) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    mp3_links = re.findall(r'href=["\'](https?://[^"\']+\.mp3)["\']', html)
                    if mp3_links:
                        LOGGER.info(f"  ├ ✅ Sefon: трек найден! Скачиваю...")
                        await throttler.update(f"⬇️ <b>Sefon:</b> скачиваю <code>{clean_q}</code>...", force=True)
                        async with session.get(mp3_links[0], timeout=25) as dl_resp:
                            if dl_resp.status == 200:
                                with open(target_file, "wb") as f: f.write(await dl_resp.read())
                                if target_file.stat().st_size > 500_000:
                                    LOGGER.info(f"  └ 🎉 Успешно скачано с Sefon!")
                                    return True
    except Exception as e:
        LOGGER.info(f"  ├ ⚠️ Sefon: {e}")
    LOGGER.info(f"  ├ ❌ Sefon: трек не найден")

    # 3. SoundCloud
    LOGGER.info(f"  ├ ☁️ [3/4] Проверяю SoundCloud...")
    await throttler.update(f"🔎 <b>Поиск:</b> <code>{clean_q}</code>\n├ ☁️ <i>SoundCloud...</i>")
    try:
        import yt_dlp
        sc_opts = {
            'ffmpeg_location': get_ffmpeg_path(),
            'quiet': True,
            'no_warnings': True,
            'format': 'bestaudio/best',
            'outtmpl': str(target_file.with_suffix('')),
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]
        }
        loop = asyncio.get_event_loop()
        def run_sc():
            with yt_dlp.YoutubeDL(sc_opts) as ydl:
                return ydl.extract_info(f"scsearch1:{clean_q}", download=True)

        await loop.run_in_executor(None, run_sc)
        if target_file.exists() and target_file.stat().st_size > 500_000:
            LOGGER.info(f"  └ 🎉 Успешно скачано с SoundCloud!")
            return True
    except Exception as e:
        LOGGER.info(f"  ├ ⚠️ SoundCloud: {e}")
    LOGGER.info(f"  ├ ❌ SoundCloud: трек не найден")

    # 4. Piped Stream Proxy ➔ Safari Bypass
    LOGGER.info(f"  ├ 🔴 [4/4] Подключаю YouTube Piped Stream + Safari Bypass...")
    await throttler.update(f"🔎 <b>Поиск:</b> <code>{clean_q}</code>\n└ 🔴 <i>YouTube Gateway...</i>", force=True)

    found_video_id = None
    found_title = clean_q

    for instance in PIPED_INSTANCES:
        try:
            LOGGER.info(f"  ├ 🔄 Ищу на Piped ({instance})...")
            search_api = f"{instance}/search?q={encoded_query}&filter=music_songs"
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(search_api, timeout=7) as resp:
                    if resp.status != 200: continue
                    if "json" not in resp.headers.get("Content-Type", "").lower(): continue
                    data = await resp.json(content_type=None)
                    items = data.get("items", [])

                    if not items:
                        search_api_all = f"{instance}/search?q={encoded_query}"
                        async with session.get(search_api_all, timeout=7) as r_all:
                            if r_all.status == 200 and "json" in r_all.headers.get("Content-Type", "").lower():
                                d_all = await r_all.json(content_type=None)
                                items = d_all.get("items", [])

                    if not items: continue

                    found_video_id = items[0].get("url", "").replace("/watch?v=", "")
                    found_title = items[0].get("title", clean_q)
                    if not found_video_id: continue

                    LOGGER.info(f"  ├ 🎵 Найдено на Piped: '{found_title}' ({found_video_id})")
                    await throttler.update(f"⬇️ <b>Piped:</b> скачиваю <code>{found_title}</code>...")

                    # СПОСОБ А: Скачиваем поток через прокси самого Piped
                    stream_api = f"{instance}/streams/{found_video_id}"
                    async with session.get(stream_api, timeout=10) as s_resp:
                        if s_resp.status == 200 and "json" in s_resp.headers.get("Content-Type", "").lower():
                            s_data = await s_resp.json(content_type=None)
                            audio_streams = s_data.get("audioStreams", [])
                            if audio_streams:
                                best_audio = max(audio_streams, key=lambda x: int(x.get("bitrate") or 0))
                                audio_url = best_audio.get("url")
                                if audio_url:
                                    LOGGER.info(f"  ├ ⚡ Качаю поток через Piped Proxy...")
                                    temp_raw = target_file.with_suffix(".raw")
                                    async with session.get(audio_url, timeout=40) as dl_r:
                                        if dl_r.status == 200:
                                            with open(temp_raw, "wb") as f: f.write(await dl_r.read())
                                            ffmpeg_bin = get_ffmpeg_path()
                                            proc = await asyncio.create_subprocess_exec(
                                                ffmpeg_bin, "-y", "-i", str(temp_raw), "-vn", "-b:a", "320k", str(target_file),
                                                stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.DEVNULL
                                            )
                                            await proc.wait()
                                            if temp_raw.exists(): temp_raw.unlink()

                                            if target_file.exists() and target_file.stat().st_size > 400_000:
                                                LOGGER.info(f"  └ 🎉 Успешно скачано через Piped Proxy!")
                                                return True

                    # СПОСОБ Б: Если Piped не отдал поток — запускаем Safari Client Bypass
                    LOGGER.info(f"  ├ ⚡ Пробую прямой обход YouTube через Safari HLS...")
                    ok = await download_youtube_safari(found_video_id, target_file)
                    if ok:
                        LOGGER.info(f"  └ 🎉 Успешно скачано через Safari HLS Bypass!")
                        return True

        except Exception as e:
            LOGGER.info(f"  ├ ⚠️ Ошибка Piped {instance}: {e}")
            continue

    LOGGER.warning(f"  └ ❌ Все источники исчерпаны.")
    return False

# --- ТЕГИРОВАНИЕ SPOTIFY ---
async def apply_clean_metadata(mp3_path: Path, title: str, artist: str, cover_url: str = None) -> int:
    duration = 0
    cover_data = None

    if cover_url:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(cover_url, timeout=8) as r:
                    if r.status == 200: cover_data = await r.read()
        except Exception: pass

    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, ID3NoHeaderError

        try:
            audio_info = MP3(str(mp3_path))
            duration = int(audio_info.info.length or 0)
        except Exception: duration = 0

        try:
            audio = ID3(str(mp3_path))
            audio.delete()
        except ID3NoHeaderError: pass

        audio = ID3()
        audio.add(TIT2(encoding=3, text=title))
        audio.add(TPE1(encoding=3, text=artist or "Unknown Artist"))
        audio.add(TALB(encoding=3, text=title))
        if cover_data:
            audio.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))
        audio.save(str(mp3_path), v2_version=3)
    except Exception as e:
        LOGGER.warning(f"Mutagen error: {e}")

    return duration

async def resolve_pinterest_pin(raw_url: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        info = await extract_info(raw_url)
        if info:
            formats = info.get("formats", [])
            has_vid = any(f.get("vcodec") and f.get("vcodec") != "none" for f in formats) or info.get("vcodec") != "none"
            if has_vid or "video" in str(info.get("ext", "")):
                return {"is_video": True, "title": info.get("title") or "Pinterest Video", "thumb": info.get("thumbnail"), "direct_url": None}
    except Exception: pass

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(raw_url, allow_redirects=True, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    m_vpin = re.search(r'https://v\.pinimg\.com/videos/[^\s"\'<>]+\.mp4', html)
                    if m_vpin: return {"is_video": True, "title": "Pinterest Video", "direct_url": m_vpin.group(0), "thumb": None}
                    m_vid = re.search(r'<meta\s+property=["\']og:video(?::secure_url)?["\']\s+content=["\']([^"\']+)["\']', html)
                    if m_vid: return {"is_video": True, "title": "Pinterest Video", "direct_url": m_vid.group(1), "thumb": None}
                    m_img = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html)
                    if m_img:
                        orig_url = re.sub(r'/\d+x/', '/originals/', m_img.group(1))
                        return {"is_video": False, "title": "Pinterest Photo", "direct_url": orig_url, "thumb": orig_url}
    except Exception: pass

    return {"is_video": True, "title": "Pinterest Media", "direct_url": None, "thumb": None}

async def extract_info(url: str):
    import yt_dlp
    opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'format': UNIVERSAL_FORMAT,
        'extractor_args': {
            'youtube': {
                'player_client': ['web_safari', 'tv_downgraded', 'android_vr'],
                'player_skip': ['web', 'mweb', 'android']
            }
        }
    }
    loop = asyncio.get_event_loop()
    with yt_dlp.YoutubeDL(opts) as ydl:
        return await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))

# --- ТОЧКА ВХОДА API ---
def register(client, bot=None):
    asyncio.create_task(ensure_latest_ytdlp())

    async def execute_download(target_chat_id, session, action, time_range=None, reply_to_id=None, status_event=None):
        url = session["direct_url"] or session["url"]
        platform = session["platform"]
        ffmpeg_bin = get_ffmpeg_path()
        throttler = StatusThrottler(status_event, interval=3.0)

        await throttler.update("⏳ <b>Загрузка медиа...</b>", force=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # 1. SPOTIFY
            if platform == "spotify":
                meta = session.get("spotify_meta") or {}
                artist = meta.get("author", "").strip()
                title = meta.get("title", "Track").strip()
                search_query = f"{artist} - {title}".strip(" -") if artist else title

                out_mp3 = tmp_path / "track.mp3"
                ok = await search_and_download_audio(search_query, out_mp3, throttler)

                if not ok or not out_mp3.exists():
                    await throttler.update("❌ <b>Трек не найден ни в одной базе.</b>", force=True)
                    return

                duration = await apply_clean_metadata(out_mp3, title, artist, meta.get("thumb"))
                caption = f"🎵 <b>{artist}</b> — <i>{title}</i>" if artist else f"🎵 <b>{title}</b>"

                await client.send_file(
                    target_chat_id,
                    file=str(out_mp3),
                    reply_to=reply_to_id,
                    caption=caption,
                    parse_mode="html",
                    attributes=[DocumentAttributeAudio(duration=int(duration or 0), title=title, performer=artist or "Spotify")]
                )
                await throttler.update("✅ <b>Готово!</b>", force=True)
                return

            # 2. PINTEREST ФОТО
            if platform == "pinterest" and not session.get("is_video"):
                direct_img = session.get("direct_url") or session.get("thumb")
                if direct_img:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(direct_img) as r:
                            if r.status == 200:
                                p_file = tmp_path / "pinterest.jpg"
                                with open(p_file, "wb") as f: f.write(await r.read())
                                await client.send_file(target_chat_id, file=str(p_file), reply_to=reply_to_id, caption=f"📌 <b>{session.get('title', 'Pinterest')}</b>", parse_mode="html")
                                await throttler.update("✅ <b>Готово!</b>", force=True)
                                return

            # 3. YOUTUBE, SOUNDCLOUD, TIKTOK, INSTAGRAM
            import yt_dlp
            out_template = str(tmp_path / "%(title).50s.%(ext)s")
            cookies_file = setup_cookies(tmp_path)

            ydl_opts = {
                'ffmpeg_location': ffmpeg_bin,
                'quiet': True,
                'no_warnings': True,
                'outtmpl': out_template,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web_safari', 'tv_downgraded', 'android_vr'],
                        'player_skip': ['web', 'mweb', 'android']
                    }
                }
            }
            if cookies_file:
                ydl_opts['cookiefile'] = cookies_file

            if time_range:
                ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func(None, [(time_range[0], time_range[1])])
                ydl_opts['force_keyframes_at_cuts'] = True

            is_audio = False

            if action == "mp3" or platform == "soundcloud":
                is_audio = True
                ydl_opts.update({
                    'format': 'bestaudio/best',
                    'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]
                })
            elif action in ("144", "360", "720", "1080"):
                ydl_opts.update({
                    'format': f'bv*[height<={action}]+ba/b[height<={action}]/bestvideo[height<={action}]/best',
                    'merge_output_format': 'mp4'
                })
            else:
                ydl_opts.update({'format': UNIVERSAL_FORMAT, 'merge_output_format': 'mp4'})

            loop = asyncio.get_event_loop()
            try:
                def run_ydl():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl: return ydl.extract_info(url, download=True)
                info = await loop.run_in_executor(None, run_ydl)
            except Exception as e:
                LOGGER.error(f"Download error: {e}")
                await throttler.update(f"❌ <b>Ошибка:</b> <code>{e}</code>", force=True)
                return

            downloaded = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if not f.endswith(".part")]
            if not downloaded:
                await throttler.update("❌ <b>Файл не найден.</b>", force=True)
                return

            main_file = max(downloaded, key=os.path.getsize)
            title = (info.get("title") if info else None) or session.get("title", "Media")
            uploader = (info.get("uploader") if info else None) or (info.get("artist") if info else "")
            duration = int((info.get("duration") if info else 0) or 0)

            attrs = []
            if is_audio:
                attrs = [DocumentAttributeAudio(duration=duration, title=title, performer=uploader)]
            elif main_file.endswith(".mp4"):
                attrs = [DocumentAttributeVideo(duration=duration, w=1280, h=720, supports_streaming=True)]

            caption = f"🎬 <b>{title}</b>" if not is_audio else f"🎵 <b>{uploader}</b> — <i>{title}</i>"
            if time_range:
                caption += f"\n✂️ Нарезка: <code>[{time_range[0]} - {time_range[1]}]</code>"

            await client.send_file(target_chat_id, file=main_file, reply_to=reply_to_id, caption=caption, parse_mode="html", attributes=attrs)
            await throttler.update("✅ <b>Готово!</b>", force=True)

    # --- ИНЛАЙН-ОТВЕТЧИК БОТА ---
    if bot:
        @bot.on(events.InlineQuery(pattern=r"^dl:([a-zA-Z0-9]+)"))
        async def dl_inline_query_handler(event):
            sess_id = event.pattern_match.group(1)
            session = SESSIONS.get(sess_id)
            if not session: return

            parsed_text, entities = await bot._parse_message_text(session["text"], 'html')
            send_msg = InputBotInlineMessageText(
                message=parsed_text,
                no_webpage=False,
                invert_media=True,
                entities=entities,
                reply_markup=bot.build_reply_markup(session["buttons"])
            )
            res_id = binascii.hexlify(os.urandom(8)).decode('ascii')
            result = InputBotInlineResult(id=res_id, type='article', title='Media Grabber', send_message=send_msg)
            await event.answer([result], cache_time=1)

    # --- СЛУШАТЕЛЬ КОМАНД СКАЧИВАНИЯ ---
    @client.on(events.NewMessage(pattern=r"^(?:sudo\s+|\.dl\s+)(https?://[^\s]+)(?:\s+(.*))?"))
    async def media_trigger_handler(event):
        if not await is_authorized(event): return

        raw_url = event.pattern_match.group(1).strip()
        tail_args = (event.pattern_match.group(2) or "").strip()
        platform = detect_platform(raw_url)

        time_m = re.match(r"^(\d+:\d+(?:\.\d+)?)-(\d+:\d+(?:\.\d+)?)$", tail_args)
        if time_m:
            status = await event.reply("⚡ <code>Загрузка нарезки...</code>", parse_mode="html")
            sess = {"url": raw_url, "platform": platform, "thumb": None, "direct_url": None, "title": "Media"}
            await execute_download(event.chat_id, sess, "best", time_range=(time_m.group(1), time_m.group(2)), reply_to_id=event.id, status_event=status)
            return

        status = await event.reply("🔎 <code>Анализирую медиапоток...</code>", parse_mode="html")

        spotify_meta = None
        p_info = None
        info = {}

        try:
            if platform == "spotify":
                spotify_meta = await get_spotify_meta(raw_url)
                author = spotify_meta.get("author", "") if spotify_meta else ""
                title = spotify_meta.get("title", "") if spotify_meta else "Track"
                display_title = f"{author} — {title}" if author else title
                info = {"title": display_title, "thumbnail": spotify_meta.get("thumb") if spotify_meta else None}
            elif platform == "pinterest":
                p_info = await resolve_pinterest_pin(raw_url)
                info = {"title": p_info["title"], "thumbnail": p_info.get("thumb")}
            else:
                info = await extract_info(raw_url)
        except Exception as e:
            return await status.edit(f"❌ Ошибка анализа: <code>{e}</code>", parse_mode="html")

        sess_id = uuid.uuid4().hex[:8]
        thumb_url = info.get("thumbnail") or (p_info.get("thumb") if p_info else None) or (spotify_meta.get("thumb") if spotify_meta else None)
        title = info.get("title", "Медиафайл")

        banner_tag = f'<a href="{thumb_url}">&#8205;</a>' if thumb_url else ''
        buttons = []

        if platform == "youtube":
            dur = info.get("duration") or 0
            dur_str = f" • {dur//60}:{dur%60:02d}" if dur else ""
            text = f"{banner_tag}🎬 <b>{title}</b>{dur_str}"
            buttons = [
                [
                    Button.inline("⚡ 144p", data=f"dl_{sess_id}_144"),
                    Button.inline("📼 360p", data=f"dl_{sess_id}_360"),
                    Button.inline("🚀 720p", data=f"dl_{sess_id}_720")
                ],
                [Button.inline("⚡ 1080p", data=f"dl_{sess_id}_1080")],
                [Button.inline("🔊 MP3", data=f"dl_{sess_id}_mp3"), Button.inline("✂️ Обрезка", data=f"dl_{sess_id}_trim")]
            ]
        elif platform == "pinterest":
            is_vid = p_info.get("is_video", True) if p_info else True
            text = f"{banner_tag}📌 <b>{title}</b>"
            btn_label = "🎬 Скачать видео" if is_vid else "🖼 Скачать фото"
            buttons = [[Button.inline(btn_label, data=f"dl_{sess_id}_media")]]
        elif platform in ("spotify", "soundcloud"):
            text = f"{banner_tag}🎵 <b>{title}</b>"
            buttons = [[Button.inline("🎵 Скачать трек (MP3 320k)", data=f"dl_{sess_id}_mp3")]]
        elif platform == "tiktok":
            text = f"{banner_tag}⬛ <b>{title[:65]}</b>"
            buttons = [[Button.inline("🎬 Скачать видео", data=f"dl_{sess_id}_media")], [Button.inline("🔊 Звук (MP3)", data=f"dl_{sess_id}_mp3")]]
        else:
            text = f"{banner_tag}🎬 <b>{title[:65]}</b>"
            buttons = [[Button.inline("🎬 Скачать медиа", data=f"dl_{sess_id}_media")], [Button.inline("🔊 Аудио (MP3)", data=f"dl_{sess_id}_mp3")]]

        SESSIONS[sess_id] = {
            "url": raw_url,
            "platform": platform,
            "title": title,
            "thumb": thumb_url,
            "spotify_meta": spotify_meta,
            "is_video": p_info.get("is_video", True) if p_info else True,
            "direct_url": p_info.get("direct_url") if p_info else None,
            "chat_id": event.chat_id,
            "reply_id": event.id,
            "text": text,
            "buttons": buttons
        }

        if bot:
            bot_me = await bot.get_me()
            try:
                results = await client.inline_query(bot_me.username, f"dl:{sess_id}")
                if results:
                    await results[0].click(event.chat_id, reply_to=event.id)
                    await status.delete()
                    return
            except Exception as e_inline:
                LOGGER.error(f"Inline error: {e_inline}")

        await status.edit(text, parse_mode="html")

    # --- ОБРАБОТЧИК КНОПОК ---
    if bot:
        @bot.on(events.CallbackQuery(pattern=r"^dl_([a-zA-Z0-9]+)_(.+)"))
        async def dl_callback_handler(event):
            sess_id = event.pattern_match.group(1).decode("utf-8")
            action = event.pattern_match.group(2).decode("utf-8")

            session = SESSIONS.get(sess_id)
            if not session: return await event.answer("⚠️ Ссылка устарела.", alert=True)

            if action == "trim":
                WAITING_TRIM[session["chat_id"]] = session
                await event.answer()
                return await event.edit("✂️ <b>Отправьте отрезок в чат:</b>\nНапример: <code>00:10-00:45</code>", parse_mode="html")

            await event.answer("⚡ Загрузка...")
            asyncio.create_task(
                execute_download(session["chat_id"], session, action, reply_to_id=session["reply_id"], status_event=event)
            )

    # Слушатель нарезки
    @client.on(events.NewMessage(func=lambda e: e.chat_id in WAITING_TRIM))
    async def trim_catcher_handler(event):
        session = WAITING_TRIM.pop(event.chat_id, None)
        if not session: return

        text = event.raw_text.strip()
        m = re.match(r"^(\d+:\d+(?:\.\d+)?)-(\d+:\d+(?:\.\d+)?)$", text)
        if not m: return await event.reply("❌ Формат не распознан. Пример: `00:15-00:45`.")

        status = await event.reply(f"✂️ <code>Вырезаю [{m.group(1)} - {m.group(2)}]...</code>", parse_mode="html")
        asyncio.create_task(
            execute_download(session["chat_id"], session, "best", time_range=(m.group(1), m.group(2)), reply_to_id=session["reply_id"], status_event=status)
        )
