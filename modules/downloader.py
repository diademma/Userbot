# modules/downloader.py — Мультимедиа комбайн v3.6 (DocumentAttributeAudio Fix & Spotify Polish)
import os
import re
import sys
import uuid
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
    "Музыкальный движок:\n"
    "├ 🎵 Spotify (Парсинг альбомов/треков + умный поиск + ID3 теги)\n"
    "├ ☁️ SoundCloud (Оригинал 320 kbps)\n"
    "├ 📌 Pinterest (Видео и Фото без сжатия)\n"
    "├ 🔴 YouTube (144p-1080p, MP3, Нарезка)\n"
    "└ ⬛ TikTok / Instagram (Оригиналы без водяных знаков)"
)

LOGGER = logging.getLogger("MediaGrabber")

SESSIONS = {}
WAITING_TRIM = {}

UNIVERSAL_FORMAT = "bv*+ba/b/bestvideo/bestaudio/best"

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

# --- SPOTIFY ПАРСИНГ ---
async def get_spotify_meta(url: str) -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://open.spotify.com/oembed?url={url}", timeout=8) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    raw_title = data.get("title", "Track")
                    author = data.get("author_name", "")
                    
                    # Если автор пустой или равен стандартной заглушке
                    if not author or author.lower() in ("artist", "unknown artist"):
                        author = ""

                    thumb = data.get("thumbnail_url", "")
                    return {
                        "title": raw_title,
                        "author": author,
                        "thumb": thumb
                    }
    except Exception as e:
        LOGGER.warning(f"Spotify oEmbed error: {e}")
    return None

# --- МУЛЬТИ-ПОИСКОВИК МУЗЫКИ (HITMO, SEFON, SOUNDCLOUD) ---
async def search_and_download_audio(query: str, target_file: Path) -> bool:
    """Ищет трек на открытых музыкальных ресурсах"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    clean_q = re.sub(r"\s+", " ", query).strip()
    encoded_query = urllib.parse.quote(clean_q)

    # 1. Источник: Hitmo (rus.hitmotop.com)
    try:
        hitmo_url = f"https://rus.hitmotop.com/search?q={encoded_query}"
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(hitmo_url, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    matches = re.findall(r'href=["\'](https?://[^"\']+/get/music/[^"\']+\.mp3)["\']', html)
                    if not matches:
                        matches = re.findall(r'href=["\'](/get/music/[^"\']+\.mp3)["\']', html)
                        matches = [f"https://rus.hitmotop.com{m}" for m in matches]

                    if matches:
                        download_url = matches[0]
                        async with session.get(download_url, timeout=25) as dl_resp:
                            if dl_resp.status == 200:
                                with open(target_file, "wb") as f:
                                    f.write(await dl_resp.read())
                                if target_file.stat().st_size > 500_000:
                                    LOGGER.info(f"✅ Трек успешно скачан с Hitmo: {clean_q}")
                                    return True
    except Exception as e:
        LOGGER.warning(f"Hitmo search: {e}")

    # 2. Источник: Sefon (sefon.pro)
    try:
        sefon_url = f"https://sefon.pro/search/?q={encoded_query}"
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(sefon_url, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    mp3_links = re.findall(r'href=["\'](https?://[^"\']+\.mp3)["\']', html)
                    if mp3_links:
                        async with session.get(mp3_links[0], timeout=25) as dl_resp:
                            if dl_resp.status == 200:
                                with open(target_file, "wb") as f:
                                    f.write(await dl_resp.read())
                                if target_file.stat().st_size > 500_000:
                                    LOGGER.info(f"✅ Трек успешно скачан с Sefon: {clean_q}")
                                    return True
    except Exception as e:
        LOGGER.warning(f"Sefon search: {e}")

    # 3. Источник: SoundCloud через yt-dlp (Работает безотказно)
    try:
        import yt_dlp
        sc_opts = {
            'ffmpeg_location': get_ffmpeg_path(),
            'quiet': True,
            'no_warnings': True,
            'format': 'bestaudio/best',
            'outtmpl': str(target_file.with_suffix('')),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }]
        }
        loop = asyncio.get_event_loop()
        def run_sc():
            with yt_dlp.YoutubeDL(sc_opts) as ydl:
                return ydl.extract_info(f"scsearch1:{clean_q}", download=True)

        await loop.run_in_executor(None, run_sc)
        if target_file.exists() and target_file.stat().st_size > 500_000:
            LOGGER.info(f"✅ Трек скачан с SoundCloud: {clean_q}")
            return True
    except Exception as e:
        LOGGER.warning(f"SoundCloud fallback search: {e}")

    return False

# --- ВШИВКА ТЕГОВ И ОБЛОЖКИ SPOTIFY ---
async def apply_clean_metadata(mp3_path: Path, title: str, artist: str, cover_url: str = None) -> int:
    """Стирает мусорные теги сайтов, вшивает официальный паспорт Spotify и возвращает длительность"""
    duration = 0
    cover_data = None

    if cover_url:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(cover_url, timeout=10) as r:
                    if r.status == 200:
                        cover_data = await r.read()
        except Exception:
            pass

    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, ID3NoHeaderError

        # Считываем реальную длительность
        try:
            audio_info = MP3(str(mp3_path))
            duration = int(audio_info.info.length or 0)
        except Exception:
            duration = 0

        try:
            audio = ID3(str(mp3_path))
            audio.delete() # Полная зачистка мусора
        except ID3NoHeaderError:
            pass

        audio = ID3()
        audio.add(TIT2(encoding=3, text=title))
        audio.add(TPE1(encoding=3, text=artist or "Spotify Track"))
        audio.add(TALB(encoding=3, text=title))
        if cover_data:
            audio.add(APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,
                desc='Cover',
                data=cover_data
            ))
        audio.save(str(mp3_path), v2_version=3)
    except Exception as e:
        LOGGER.warning(f"Mutagen tag error: {e}")

    return duration

async def resolve_pinterest_pin(raw_url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    try:
        info = await extract_info(raw_url)
        if info:
            formats = info.get("formats", [])
            has_vid = any(f.get("vcodec") and f.get("vcodec") != "none" for f in formats) or info.get("vcodec") != "none"
            if has_vid or "video" in str(info.get("ext", "")):
                return {
                    "is_video": True,
                    "title": info.get("title") or "Pinterest Video",
                    "thumb": info.get("thumbnail"),
                    "direct_url": None
                }
    except Exception:
        pass

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(raw_url, allow_redirects=True, timeout=10) as resp:
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
    except Exception:
        pass

    return {"is_video": True, "title": "Pinterest Media", "direct_url": None, "thumb": None}

async def extract_info(url: str):
    import yt_dlp
    opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'format': UNIVERSAL_FORMAT,
        'extractor_args': {
            'youtube': {'player_client': ['android', 'mweb', 'web']},
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

        if status_event:
            try: await status_event.edit("⏳ <b>Загрузка медиа...</b>", parse_mode="html")
            except Exception: pass

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # 1. ОБРАБОТКА SPOTIFY
            if platform == "spotify":
                meta = session.get("spotify_meta") or {}
                artist = meta.get("author", "").strip()
                title = meta.get("title", "Track").strip()

                # Формируем чистый запрос: если исполнитель пустой, ищем чисто по названию
                search_query = f"{artist} - {title}".strip(" -") if artist else title

                out_mp3 = tmp_path / "track.mp3"
                ok = await search_and_download_audio(search_query, out_mp3)

                if not ok or not out_mp3.exists():
                    if status_event:
                        try: await status_event.edit("❌ <b>Не удалось найти трек в аудиобазах.</b>", parse_mode="html")
                        except Exception: pass
                    return

                # Вшиваем теги и получаем реальную длительность
                duration = await apply_clean_metadata(out_mp3, title, artist, meta.get("thumb"))

                caption = f"🎵 <b>{artist}</b> — <i>{title}</i>" if artist else f"🎵 <b>{title}</b>"

                # ИСПРАВЛЕНО: duration передан обязательным первым аргументом
                await client.send_file(
                    target_chat_id,
                    file=str(out_mp3),
                    reply_to=reply_to_id,
                    caption=caption,
                    parse_mode="html",
                    attributes=[DocumentAttributeAudio(duration=duration, title=title, performer=artist or "Spotify")]
                )
                if status_event:
                    try: await status_event.edit("✅ <b>Готово!</b>", parse_mode="html")
                    except Exception: pass
                return

            # 2. ПРЯМАЯ ЗАГРУЗКА ФОТО PINTEREST
            if platform == "pinterest" and not session.get("is_video"):
                direct_img = session.get("direct_url") or session.get("thumb")
                if direct_img:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(direct_img) as r:
                            if r.status == 200:
                                p_file = tmp_path / "pinterest.jpg"
                                with open(p_file, "wb") as f:
                                    f.write(await r.read())
                                await client.send_file(
                                    target_chat_id,
                                    file=str(p_file),
                                    reply_to=reply_to_id,
                                    caption=f"📌 <b>{session.get('title', 'Pinterest')}</b>",
                                    parse_mode="html"
                                )
                                if status_event:
                                    try: await status_event.edit("✅ <b>Готово!</b>", parse_mode="html")
                                    except Exception: pass
                                return

            # 3. YOUTUBE, TIKTOK, INSTAGRAM, SOUNDCLOUD
            import yt_dlp
            out_template = str(tmp_path / "%(title).50s.%(ext)s")
            ydl_opts = {
                'ffmpeg_location': ffmpeg_bin,
                'quiet': True,
                'no_warnings': True,
                'outtmpl': out_template,
                'extractor_args': {
                    'youtube': {'player_client': ['android', 'mweb', 'web']},
                }
            }

            if time_range:
                ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func(None, [(time_range[0], time_range[1])])
                ydl_opts['force_keyframes_at_cuts'] = True

            is_audio = False

            if action == "mp3" or platform == "soundcloud":
                is_audio = True
                ydl_opts.update({
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '320',
                    }]
                })
            elif action in ("144", "360", "720", "1080"):
                res = action
                ydl_opts.update({
                    'format': f'bv*[height<={res}]+ba/b[height<={res}]/bestvideo[height<={res}]/best',
                    'merge_output_format': 'mp4'
                })
            else:
                ydl_opts.update({
                    'format': UNIVERSAL_FORMAT,
                    'merge_output_format': 'mp4'
                })

            loop = asyncio.get_event_loop()
            try:
                def run_ydl():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(url, download=True)
                info = await loop.run_in_executor(None, run_ydl)
            except Exception as e:
                LOGGER.error(f"Download error: {e}")
                if status_event:
                    try: await status_event.edit(f"❌ <b>Ошибка загрузки:</b> <code>{e}</code>", parse_mode="html")
                    except Exception: pass
                return

            downloaded = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if not f.endswith(".part")]
            if not downloaded:
                if status_event:
                    try: await status_event.edit("❌ <b>Файл не найден.</b>", parse_mode="html")
                    except Exception: pass
                return

            main_file = max(downloaded, key=os.path.getsize)
            title = (info.get("title") if info else None) or session.get("title", "Media")
            uploader = (info.get("uploader") if info else None) or (info.get("artist") if info else "")
            duration = int((info.get("duration") if info else 0) or 0)

            attrs = []
            if is_audio:
                # ИСПРАВЛЕНО: duration передан обязательным первым аргументом
                attrs = [DocumentAttributeAudio(duration=duration, title=title, performer=uploader)]
            elif main_file.endswith(".mp4"):
                attrs = [DocumentAttributeVideo(duration=duration, w=1280, h=720, supports_streaming=True)]

            caption = f"🎬 <b>{title}</b>" if not is_audio else f"🎵 <b>{uploader}</b> — <i>{title}</i>"
            if time_range:
                caption += f"\n✂️ Нарезка: <code>[{time_range[0]} - {time_range[1]}]</code>"

            await client.send_file(
                target_chat_id,
                file=main_file,
                reply_to=reply_to_id,
                caption=caption,
                parse_mode="html",
                attributes=attrs
            )
            if status_event:
                try: await status_event.edit("✅ <b>Готово!</b>", parse_mode="html")
                except Exception: pass

    # --- ИНЛАЙН-ОТВЕТЧИК БОТА ---
    if bot:
        @bot.on(events.InlineQuery(pattern=r"^dl:([a-zA-Z0-9]+)"))
        async def dl_inline_query_handler(event):
            sess_id = event.pattern_match.group(1)
            session = SESSIONS.get(sess_id)
            if not session:
                return

            text = session["text"]
            buttons = session["buttons"]

            parsed_text, entities = await bot._parse_message_text(text, 'html')
            send_msg = InputBotInlineMessageText(
                message=parsed_text,
                no_webpage=False,
                invert_media=True,
                entities=entities,
                reply_markup=bot.build_reply_markup(buttons)
            )

            res_id = binascii.hexlify(os.urandom(8)).decode('ascii')
            result = InputBotInlineResult(
                id=res_id,
                type='article',
                title='Media Grabber',
                send_message=send_msg
            )
            await event.answer([result], cache_time=1)

    # --- СЛУШАТЕЛЬ КОМАНД СКАЧИВАНИЯ ---
    @client.on(events.NewMessage(pattern=r"^(?:sudo\s+|\.dl\s+)(https?://[^\s]+)(?:\s+(.*))?"))
    async def media_trigger_handler(event):
        if not await is_authorized(event): return

        raw_url = event.pattern_match.group(1).strip()
        tail_args = (event.pattern_match.group(2) or "").strip()
        platform = detect_platform(raw_url)

        # Быстрая нарезка
        time_m = re.match(r"^(\d+:\d+(?:\.\d+)?)-(\d+:\d+(?:\.\d+)?)$", tail_args)
        if time_m:
            status = await event.reply("⚡ <code>Загрузка нарезки...</code>", parse_mode="html")
            sess = {"url": raw_url, "platform": platform, "thumb": None, "direct_url": None, "title": "Media"}
            await execute_download(event.chat_id, sess, "best", time_range=(time_m.group(1), time_m.group(2)), reply_to_id=event.id, status_event=status)
            return

        status = await event.reply("🔎 <code>Анализирую медиа...</code>", parse_mode="html")

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
                [
                    Button.inline("🔊 MP3", data=f"dl_{sess_id}_mp3"),
                    Button.inline("✂️ Обрезка", data=f"dl_{sess_id}_trim")
                ]
            ]

        elif platform == "pinterest":
            is_vid = p_info.get("is_video", True) if p_info else True
            text = f"{banner_tag}📌 <b>{title}</b>"
            btn_label = "🎬 Скачать видео" if is_vid else "🖼 Скачать фото"
            buttons = [
                [Button.inline(btn_label, data=f"dl_{sess_id}_media")]
            ]

        elif platform in ("spotify", "soundcloud"):
            text = f"{banner_tag}🎵 <b>{title}</b>"
            buttons = [
                [Button.inline("🎵 Скачать трек (MP3 320k)", data=f"dl_{sess_id}_mp3")]
            ]

        elif platform == "tiktok":
            text = f"{banner_tag}⬛ <b>{title[:65]}</b>"
            buttons = [
                [Button.inline("🎬 Скачать видео", data=f"dl_{sess_id}_media")],
                [Button.inline("🔊 Звук (MP3)", data=f"dl_{sess_id}_mp3")]
            ]

        else: # Instagram и прочие
            text = f"{banner_tag}🎬 <b>{title[:65]}</b>"
            buttons = [
                [Button.inline("🎬 Скачать медиа", data=f"dl_{sess_id}_media")],
                [Button.inline("🔊 Аудио (MP3)", data=f"dl_{sess_id}_mp3")]
            ]

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

        # Отправка инлайн-карточки
        if bot:
            bot_me = await bot.get_me()
            try:
                results = await client.inline_query(bot_me.username, f"dl:{sess_id}")
                if results:
                    await results[0].click(event.chat_id, reply_to=event.id)
                    await status.delete()
                    return
            except Exception as e_inline:
                LOGGER.error(f"Inline query error: {e_inline}")

        await status.edit(text, parse_mode="html")

    # --- ОБРАБОТЧИК КНОПОК ---
    if bot:
        @bot.on(events.CallbackQuery(pattern=r"^dl_([a-zA-Z0-9]+)_(.+)"))
        async def dl_callback_handler(event):
            sess_id = event.pattern_match.group(1).decode("utf-8")
            action = event.pattern_match.group(2).decode("utf-8")

            session = SESSIONS.get(sess_id)
            if not session:
                return await event.answer("⚠️ Ссылка устарела.", alert=True)

            if action == "trim":
                WAITING_TRIM[session["chat_id"]] = session
                await event.answer()
                return await event.edit(
                    "✂️ <b>Отправьте отрезок в чат:</b>\nНапример: <code>00:10-00:45</code>",
                    parse_mode="html"
                )

            await event.answer("⚡ Загрузка...")
            asyncio.create_task(
                execute_download(
                    session["chat_id"],
                    session,
                    action,
                    reply_to_id=session["reply_id"],
                    status_event=event
                )
            )

    # Слушатель нарезки
    @client.on(events.NewMessage(func=lambda e: e.chat_id in WAITING_TRIM))
    async def trim_catcher_handler(event):
        session = WAITING_TRIM.pop(event.chat_id, None)
        if not session: return

        text = event.raw_text.strip()
        m = re.match(r"^(\d+:\d+(?:\.\d+)?)-(\d+:\d+(?:\.\d+)?)$", text)
        if not m:
            return await event.reply("❌ Формат не распознан. Пример: `00:15-00:45`.")

        status = await event.reply(f"✂️ <code>Вырезаю [{m.group(1)} - {m.group(2)}]...</code>", parse_mode="html")
        asyncio.create_task(
            execute_download(
                session["chat_id"],
                session,
                "best",
                time_range=(m.group(1), m.group(2)),
                reply_to_id=session["reply_id"],
                status_event=status
            )
        )
