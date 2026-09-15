# modules/downloader.py — Минималистичный Media Grabber v3.0 (Preview Card + Fix)
import os
import re
import sys
import uuid
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
    "• .dl {ссылка} [00:10-00:40] — Скачивание с нарезкой"
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
        LOGGER.info("🚀 [MediaGrabber] yt-dlp обновлен до последней версии.")
    except Exception as e:
        LOGGER.warning(f"Ошибка обновления yt-dlp: {e}")

async def get_spotify_meta(url: str) -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://open.spotify.com/oembed?url={url}", timeout=8) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "title": data.get("title", "Track"),
                        "author": data.get("author_name", "Artist"),
                        "thumb": data.get("thumbnail_url", "")
                    }
    except Exception:
        pass
    return None

async def resolve_pinterest_pin(raw_url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
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
                    if m_vpin:
                        return {"is_video": True, "title": "Pinterest Video", "direct_url": m_vpin.group(0), "thumb": None}

                    m_m3u8 = re.search(r'https://v\.pinimg\.com/videos/[^\s"\'<>]+\.m3u8', html)
                    if m_m3u8:
                        return {"is_video": True, "title": "Pinterest Video", "direct_url": m_m3u8.group(0), "thumb": None}

                    m_vid = re.search(r'<meta\s+property=["\']og:video(?::secure_url)?["\']\s+content=["\']([^"\']+)["\']', html)
                    if m_vid:
                        return {"is_video": True, "title": "Pinterest Video", "direct_url": m_vid.group(1), "thumb": None}

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
        import yt_dlp
        url = session["direct_url"] or session["url"]
        platform = session["platform"]
        ffmpeg_bin = get_ffmpeg_path()

        if status_event:
            try: await status_event.edit("⏳ <b>Загрузка медиа...</b>", parse_mode="html")
            except Exception: pass

        with tempfile.TemporaryDirectory() as tmp_dir:
            # 1. Скачивание фото Pinterest напрямую
            if platform == "pinterest" and not session.get("is_video"):
                direct_img = session.get("direct_url") or session.get("thumb")
                if direct_img:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(direct_img) as r:
                            if r.status == 200:
                                p_file = os.path.join(tmp_dir, "pinterest.jpg")
                                with open(p_file, "wb") as f:
                                    f.write(await r.read())
                                await client.send_file(
                                    target_chat_id,
                                    file=p_file,
                                    reply_to=reply_to_id,
                                    caption=f"📌 <b>{session.get('title', 'Pinterest')}</b>",
                                    parse_mode="html"
                                )
                                if status_event:
                                    try: await status_event.edit("✅ <b>Готово!</b>", parse_mode="html")
                                    except Exception: pass
                                return

            out_template = os.path.join(tmp_dir, "%(title).50s.%(ext)s")
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

            if action == "mp3" or platform in ("spotify", "soundcloud"):
                is_audio = True
                if platform == "spotify" and session.get("spotify_meta"):
                    meta = session["spotify_meta"]
                    url = f"ytsearch1:{meta['author']} - {meta['title']} audio"

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

            downloaded_files = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if not f.endswith(".part")]
            if not downloaded_files:
                if status_event:
                    try: await status_event.edit("❌ <b>Файл не найден.</b>", parse_mode="html")
                    except Exception: pass
                return

            main_file = max(downloaded_files, key=os.path.getsize)

            title = (info.get("title") if info else None) or session.get("title", "Media")
            uploader = (info.get("uploader") if info else None) or (info.get("artist") if info else "")
            duration = int((info.get("duration") if info else 0) or 0)

            attrs = []
            if is_audio:
                if platform == "spotify" and session.get("spotify_meta"):
                    title = session["spotify_meta"]["title"]
                    uploader = session["spotify_meta"]["author"]
                attrs = [DocumentAttributeAudio(title=title, performer=uploader, duration=duration)]
            elif main_file.endswith(".mp4"):
                attrs = [DocumentAttributeVideo(duration=duration, w=1280, h=720, supports_streaming=True)]

            caption = f"🎬 <b>{title}</b>" if not is_audio else f"🎵 <b>{uploader}</b> — <i>{title}</i>"
            if time_range:
                caption += f"\n✂️ Нарезка: <code>[{time_range[0]} - {time_range[1]}]</code>"

            # ОТПРАВКА НАПРЯМУЮ В ЧАТ СЕССИИ (Никаких PeerIdInvalidError)
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
                info = {"title": f"{spotify_meta['author']} — {spotify_meta['title']}", "thumbnail": spotify_meta.get("thumb")} if spotify_meta else await extract_info(raw_url)
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

        # Вшиваем превью в начало карточки (баннер сверху сообщения)
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
            artist = spotify_meta['author'] if (platform == "spotify" and spotify_meta) else (info.get("uploader") or "")
            song = spotify_meta['title'] if (platform == "spotify" and spotify_meta) else title
            caption_title = f"{artist} — {song}" if artist else song
            text = f"{banner_tag}🎵 <b>{caption_title}</b>"
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
                [Button.inline("🎬 Скачать видео", data=f"dl_{sess_id}_media")],
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
            "chat_id": event.chat_id,        # Реальный ID чата для загрузки
            "reply_id": event.id,
            "text": text,
            "buttons": buttons
        }

        # Отправка инлайн-карточки через симбиота
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
            # ВАЖНО: передаем session["chat_id"], так как в event.chat_id инлайн-кнопок лежит None!
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
