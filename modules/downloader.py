# modules/downloader.py — Ультимативный мультимедиа загрузчик v1.0 (API Compliant)
import os
import re
import sys
import uuid
import shutil
import asyncio
import logging
import tempfile
import aiohttp
from pathlib import Path
from telethon import events, Button
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

from core.config import OWNER_ID
from core.db import is_authorized

# --- ОБЯЗАТЕЛЬНЫЕ МЕТАДАННЫЕ API ДЛЯ ЯДРА ---
TITLE = "⤓ Media Grabber Pro"
BANNER = "https://raw.githubusercontent.com/diademma/Userbot/main/assets/LLEHTABPA.jpg"
COMMANDS = (
    "• sudo {ссылка} — Интерактивный анализ и меню скачивания\n"
    "• .dl {ссылка} — Альтернативный вызов меню\n"
    "• .dl {ссылка} [00:10-00:40] — Скачивание с мгновенной нарезкой\n\n"
    "Поддерживаемые платформы:\n"
    "├ 🔴 YouTube (144p-1080p, MP3, Превью, Нарезка)\n"
    "├ 🎵 Spotify (Трек 320kbps + вшитая обложка и теги)\n"
    "├ ⬛ TikTok (Без водяного знака + Звук)\n"
    "├ 📷 Instagram (Reels, Видео, Фото)\n"
    "└ 📌 Pinterest (Оригинальные видео и фото)"
)

LOGGER = logging.getLogger("MediaGrabber")

# Хранилище сессий инлайн-кнопок
SESSIONS = {}
WAITING_TRIM = {}

def get_ffmpeg_path():
    p = shutil.which("ffmpeg")
    if p: return p
    home_p = os.path.expanduser("~/.local/bin/ffmpeg")
    if os.path.isfile(home_p): return home_p
    return "ffmpeg"

def human_size(bytes_val):
    if not bytes_val or bytes_val <= 0: return "~"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.0f} {unit}" if unit != 'MB' else f"{bytes_val:.1f} MB"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} GB"

def detect_platform(url: str) -> str:
    url_l = url.lower()
    if any(d in url_l for d in ("youtube.com", "youtu.be")): return "youtube"
    if "spotify.com" in url_l: return "spotify"
    if "tiktok.com" in url_l: return "tiktok"
    if "instagram.com" in url_l: return "instagram"
    if any(d in url_l for d in ("pinterest.com", "pin.it")): return "pinterest"
    return "generic"

async def ensure_latest_ytdlp():
    """Фоновое обновление yt-dlp при старте модуля"""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        LOGGER.info("🚀 [MediaGrabber] yt-dlp успешно обновлен до последней версии.")
    except Exception as e:
        LOGGER.warning(f"Ошибка обновления yt-dlp: {e}")

async def get_spotify_meta(url: str) -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://open.spotify.com/oembed?url={url}", timeout=8) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    title = data.get("title", "Unknown Track")
                    author = data.get("author_name", "Unknown Artist")
                    thumb = data.get("thumbnail_url", "")
                    return {"title": title, "author": author, "thumb": thumb}
    except Exception:
        pass
    return None

async def extract_info(url: str):
    import yt_dlp
    opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'format': 'best',
    }
    loop = asyncio.get_event_loop()
    with yt_dlp.YoutubeDL(opts) as ydl:
        return await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))

# --- ТОЧКА ВХОДА API ---
def register(client, bot=None):
    # Запуск фонового обновления yt-dlp
    asyncio.create_task(ensure_latest_ytdlp())

    async def execute_download(target_chat_id, session, action, time_range=None, reply_to_id=None, status_msg=None):
        import yt_dlp
        url = session["url"]
        platform = session["platform"]
        ffmpeg_bin = get_ffmpeg_path()

        if status_msg:
            try: await status_msg.edit(f"⏳ **Загрузка и конвертация медиа...**")
            except Exception: pass

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_template = os.path.join(tmp_dir, "%(title).50s.%(ext)s")
            ydl_opts = {
                'ffmpeg_location': ffmpeg_bin,
                'quiet': True,
                'no_warnings': True,
                'outtmpl': out_template,
            }

            if time_range:
                ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func(None, [(time_range[0], time_range[1])])
                ydl_opts['force_keyframes_at_cuts'] = True

            is_audio = False

            if action == "thumb":
                thumb_url = session.get("thumb")
                if thumb_url:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(thumb_url) as r:
                            if r.status == 200:
                                t_path = os.path.join(tmp_dir, "thumb.jpg")
                                with open(t_path, "wb") as f:
                                    f.write(await r.read())
                                await client.send_file(target_chat_id, file=t_path, reply_to=reply_to_id, caption="🖼 Превью в оригинальном качестве")
                                if status_msg: await status_msg.delete()
                                return

            elif action == "mp3" or platform == "spotify":
                is_audio = True
                if platform == "spotify" and session.get("spotify_meta"):
                    meta = session["spotify_meta"]
                    search_query = f"ytsearch1:{meta['author']} - {meta['title']} audio"
                    url = search_query

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
                    'format': f'bestvideo[height<={res}]+bestaudio/best[height<={res}]/best',
                    'merge_output_format': 'mp4'
                })

            else:
                # Универсальное лучшее качество
                ydl_opts.update({
                    'format': 'bestvideo+bestaudio/best',
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
                if status_msg: await status_msg.edit(f"❌ **Ошибка загрузки:** `{e}`")
                return

            downloaded_files = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if not f.endswith(".part")]
            if not downloaded_files:
                if status_msg: await status_msg.edit("❌ Файл не был сгенерирован.")
                return

            main_file = max(downloaded_files, key=os.path.getsize)

            # Отправка файла через юзербота
            title = info.get("title", "Media")
            uploader = info.get("uploader", "")
            duration = int(info.get("duration") or 0)

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
                caption += f"\n✂️ Нарезка: `[{time_range[0]} - {time_range[1]}]`"

            await client.send_file(
                target_chat_id,
                file=main_file,
                reply_to=reply_to_id,
                caption=caption,
                parse_mode="html",
                attributes=attrs
            )
            if status_msg: await status_msg.delete()

    # --- СЛУШАТЕЛЬ КОМАНД СКАЧИВАНИЯ ---
    @client.on(events.NewMessage(pattern=r"^(?:sudo\s+|\.dl\s+)(https?://[^\s]+)(?:\s+(.*))?"))
    async def media_trigger_handler(event):
        if not await is_authorized(event): return

        raw_url = event.pattern_match.group(1).strip()
        tail_args = (event.pattern_match.group(2) or "").strip()
        platform = detect_platform(raw_url)

        # Быстрая нарезка сразу из команды (например: sudo {url} 00:10-00:40)
        time_m = re.match(r"^(\d+:\d+(?:\.\d+)?)-(\d+:\d+(?:\.\d+)?)$", tail_args)
        if time_m:
            status = await event.reply("⚡ `Загрузка выбранного фрагмента...`")
            sess = {"url": raw_url, "platform": platform, "thumb": None}
            await execute_download(event.chat_id, sess, "best", time_range=(time_m.group(1), time_m.group(2)), reply_to_id=event.id, status_msg=status)
            return

        status = await event.reply("🔎 `Анализирую медиапоток...`")

        try:
            spotify_meta = None
            if platform == "spotify":
                spotify_meta = await get_spotify_meta(raw_url)
                info = {"title": f"{spotify_meta['author']} — {spotify_meta['title']}", "thumbnail": spotify_meta.get("thumb")} if spotify_meta else await extract_info(raw_url)
            else:
                info = await extract_info(raw_url)
        except Exception as e:
            return await status.edit(f"❌ Не удалось проанализировать ссылку: `{e}`")

        sess_id = uuid.uuid4().hex[:8]
        SESSIONS[sess_id] = {
            "url": raw_url,
            "platform": platform,
            "title": info.get("title", "Media"),
            "thumb": info.get("thumbnail"),
            "spotify_meta": spotify_meta,
            "chat_id": event.chat_id,
            "reply_id": event.id
        }

        # --- СБОРКА АДАПТИВНОГО МЕНЮ ПОД ПЛАТФОРМУ ---
        buttons = []

        if platform == "youtube":
            formats = info.get("formats", [])
            dur = info.get("duration") or 0
            dur_str = f"{dur//60}:{dur%60:02d}"

            # Расчет размеров качеств
            def calc_size(height):
                for f in reversed(formats):
                    if f.get("height") == height:
                        sz = f.get("filesize") or f.get("filesize_approx")
                        if sz: return human_size(sz)
                        tbr = f.get("tbr") or (height * 3)
                        if tbr and dur: return human_size(int((tbr * 1024 / 8) * dur))
                return "~"

            s_144 = calc_size(144)
            s_360 = calc_size(360)
            s_720 = calc_size(720)
            s_1080 = calc_size(1080)

            text = (
                f"⚡ <b>144p</b> :  {s_144}\n"
                f"✅ <b>360p</b> :  {s_360}\n"
                f"🚀 <b>720p</b> :  {s_720}\n"
                f"⚡ <b>1080p</b>: {s_1080}\n\n"
                f"<b>Форматы для скачивания ↓</b>"
            )

            buttons = [
                [
                    Button.inline("⚡ 144p", data=f"dl_{sess_id}_144"),
                    Button.inline("📼 360p", data=f"dl_{sess_id}_360"),
                    Button.inline("🚀 720p", data=f"dl_{sess_id}_720")
                ],
                [Button.inline("⚡ 1080p", data=f"dl_{sess_id}_1080")],
                [
                    Button.inline("🔊 MP3", data=f"dl_{sess_id}_mp3"),
                    Button.inline("🖼 Превью", data=f"dl_{sess_id}_thumb")
                ],
                [Button.inline("✂️ Обрезка", data=f"dl_{sess_id}_trim")]
            ]

        elif platform == "spotify":
            author = spotify_meta['author'] if spotify_meta else "Spotify"
            song = spotify_meta['title'] if spotify_meta else info.get('title')
            text = (
                f"🎧 <b>Spotify Music</b>\n\n"
                f"🎵 <b>Трек:</b> {song}\n"
                f"👤 <b>Исполнитель:</b> {author}\n\n"
                f"<b>Выберите формат ↓</b>"
            )
            buttons = [
                [Button.inline("🎵 Скачать трек (320 kbps)", data=f"dl_{sess_id}_mp3")],
                [Button.inline("🖼 Обложка альбома (HD)", data=f"dl_{sess_id}_thumb")]
            ]

        elif platform == "tiktok":
            text = (
                f"⬛ <b>TikTok Original</b>\n\n"
                f"📝 {info.get('title', 'Без описания')[:70]}...\n\n"
                f"<b>Выберите формат ↓</b>"
            )
            buttons = [
                [Button.inline("🎬 Видео (Без водяного знака)", data=f"dl_{sess_id}_best")],
                [
                    Button.inline("🔊 Звук (MP3)", data=f"dl_{sess_id}_mp3"),
                    Button.inline("🖼 Превью", data=f"dl_{sess_id}_thumb")
                ]
            ]

        elif platform == "pinterest":
            text = (
                f"📌 <b>Pinterest Pin</b>\n\n"
                f"📌 {info.get('title', 'Медиа Pinterest')}\n\n"
                f"<b>Выберите действие ↓</b>"
            )
            buttons = [
                [Button.inline("📥 Скачать в оригинале", data=f"dl_{sess_id}_best")]
            ]

        else: # Instagram, Twitter и Generic
            text = (
                f"🌐 <b>Media Stream</b>\n\n"
                f"🎬 {info.get('title', 'Медиафайл')[:70]}\n\n"
                f"<b>Выберите формат ↓</b>"
            )
            buttons = [
                [Button.inline("🎬 Скачать видео", data=f"dl_{sess_id}_best")],
                [
                    Button.inline("🔊 Аудио (MP3)", data=f"dl_{sess_id}_mp3"),
                    Button.inline("🖼 Превью", data=f"dl_{sess_id}_thumb")
                ]
            ]

        await status.delete()

        # Отправка инлайн-меню через бота-симбиота
        if bot:
            try:
                await bot.send_message(event.chat_id, text, buttons=buttons, reply_to=event.id, parse_mode="html")
            except Exception as e:
                LOGGER.error(f"Bot send error: {e}")
                # Фоллбэк если бот не может писать напрямую
                await event.reply(text)

    # --- ОБРАБОТЧИК ИНЛАЙН-КНОПОК СИМБИОТА ---
    if bot:
        @bot.on(events.CallbackQuery(pattern=r"^dl_([a-zA-Z0-9]+)_(.+)"))
        async def dl_callback_handler(event):
            sess_id = event.pattern_match.group(1).decode("utf-8")
            action = event.pattern_match.group(2).decode("utf-8")

            session = SESSIONS.get(sess_id)
            if not session:
                return await event.answer("⚠️ Сессия устарела. Отправьте ссылку повторно.", alert=True)

            if action == "trim":
                WAITING_TRIM[event.chat_id] = session
                await event.answer()
                return await event.edit(
                    f"✂️ <b>Режим обрезки видео</b>\n\n"
                    f"Отправьте таймкод в чат в формате:\n"
                    f"<code>00:10-00:45</code>\n\n"
                    f"<i>yt-dlp скачает только указанный отрезок без загрузки всего видео!</i>",
                    parse_mode="html"
                )

            await event.answer("⚡ Запуск загрузки...")
            status = await event.edit(f"⬇️ **Подготовка и скачивание...**\n`Действие: {action.upper()}`")
            asyncio.create_task(execute_download(event.chat_id, session, action, reply_to_id=session["reply_id"], status_msg=status))

    # Слушатель таймкода для обрезки
    @client.on(events.NewMessage(func=lambda e: e.chat_id in WAITING_TRIM))
    async def trim_catcher_handler(event):
        session = WAITING_TRIM.pop(event.chat_id, None)
        if not session: return

        text = event.raw_text.strip()
        m = re.match(r"^(\d+:\d+(?:\.\d+)?)-(\d+:\d+(?:\.\d+)?)$", text)
        if not m:
            return await event.reply("❌ Формат не распознан. Пример: `00:15-00:45`.")

        status = await event.reply(f"✂️ `Вырезаю фрагмент [{m.group(1)} - {m.group(2)}]...`")
        asyncio.create_task(execute_download(event.chat_id, session, "best", time_range=(m.group(1), m.group(2)), reply_to_id=session["reply_id"], status_msg=status))
