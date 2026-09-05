# modules/media_studio.py — Высокоточный мультимедиа комбайн v2.5 (API Compliant)
import os
import re
import shutil
import asyncio
import sqlite3
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from telethon import events
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    DocumentAttributeSticker,
    DocumentAttributeImageSize,
    DocumentAttributeAnimated,
    InputStickerSetEmpty
)

from core.config import OWNER_ID, DB_NAME
from core.db import is_authorized

# --- ОБЯЗАТЕЛЬНЫЕ МЕТАДАННЫЕ API ДЛЯ ЯДРА ---
TITLE = "▷ Media Studio"
BANNER = "https://raw.githubusercontent.com/diademma/Userbot/main/assets/LLEHTABPA.jpg"
COMMANDS = (
    "• sudo медиа — Полное интерактивное меню\n\n"
    "📹 ВИДЕО И КРУЖОЧКИ:\n"
    "• .round / .круг — Видео в кружок 1:1\n"
    "• .unround — Кружок обратно в видео MP4\n"
    "• .cstick / .кругстик — Круглый видеостикер\n"
    "• .webm — В живой видеостикер WEBM\n"
    "• .gif — Видео / .tgs стикер в плавную GIF\n"
    "• .vcut [00:00-00:00] — Обрезка видео\n"
    "• .mute — Удалить аудиодорожку\n"
    "• .audio — Извлечь чистый MP3 из видео\n\n"
    "🎵 АУДИО ЭФФЕКТЫ:\n"
    "• .pitch [-10..+10] — Плавный Stellio-питч\n"
    "• .bass [1..10] — Ступенчатый сабвуфер\n"
    "• .reverb — Концертное эхо\n"
    "• .slow — Замедление + реверб атмосфера\n"
    "• .cut [00:00-00:00] — Обрезка трека\n"
    "• .cover — Установить обложку песни\n"
    "• .voice / .гс — Перевод трека в голосовое\n"
    "• .norm — Нормализация звука EBU R128\n\n"
    "🖼️ ФОТО И ФАЙЛЫ:\n"
    "• .rmbg [black|white] — Срезка фона\n"
    "• .sticker — Фото в стикер WebP\n"
    "• .to [png|jpg|webp|gif|ico] — Конвертер\n"
    "• .ext [расширение] — Смена типа файла\n"
    "• .clean — Полное удаление EXIF метаданных"
)

# Поддержка векторных анимированных стикеров Telegram (.tgs)
try:
    from rlottie_python import LottieAnimation
    HAS_RLOTTIE = True
except Exception:
    HAS_RLOTTIE = False

LOGGER = logging.getLogger("MediaStudio")

TARGET_CHAT_ID = -1002281822286
DAILY_LIMIT = 3
COVER_WAITING = {}

def get_ffmpeg_bin() -> str:
    bin_path = shutil.which("ffmpeg")
    if bin_path:
        return bin_path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"

FFMPEG_BIN = get_ffmpeg_bin()
SEMAPHORE = asyncio.Semaphore(2)

# --- БАЗА ДАННЫХ ДЛЯ ЛИМИТОВ ---
def init_media_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS media_limits (
            user_id INTEGER,
            usage_date TEXT,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, usage_date)
        )
    """)
    conn.commit()
    conn.close()

def check_and_inc_limit(user_id: int) -> tuple[bool, int]:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT count FROM media_limits WHERE user_id = ? AND usage_date = ?", (user_id, today))
    row = cur.fetchone()
    current_count = row[0] if row else 0

    if current_count >= DAILY_LIMIT:
        conn.close()
        return False, 0

    new_count = current_count + 1
    cur.execute("INSERT OR REPLACE INTO media_limits (user_id, usage_date, count) VALUES (?, ?, ?)", (user_id, today, new_count))
    conn.commit()
    conn.close()
    return True, DAILY_LIMIT - new_count

# --- АСИНХРОННЫЙ FFMPEG ---
async def run_ffmpeg(cmd: list[str], timeout: int = 180) -> bool:
    async with SEMAPHORE:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG_BIN, "-hide_banner", "-y", *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                LOGGER.error(f"FFmpeg Error: {stderr.decode(errors='ignore')}")
                return False
            return True
        except asyncio.TimeoutError:
            proc.kill()
            LOGGER.error("FFmpeg таймаут процесса.")
            return False

def parse_time_range(val: str) -> tuple[str, str] | None:
    m = re.match(r"^(\d+(?::\d+)*(?:\.\d+)?)-(\d+(?::\d+)*(?:\.\d+)?)$", val.strip())
    if not m:
        return None
    return m.group(1), m.group(2)

# --- АДАПТИВНЫЕ МЕНЮ ---
MENUS = {
    "main": (
        "🎛️ **MEDIA STUDIO**\n"
        "──────────────\n\n"
        "📂 **Разделы студии:**\n\n"
        "🎵 `sudo медиа аудио`\n"
        "└ Питч, бас, реверб, гс, обрезка, обложка\n\n"
        "📹 `sudo медиа видео`\n"
        "└ Кружки, круглые стикеры, GIF\n\n"
        "🖼️ `sudo медиа фото`\n"
        "└ Удаление фона, форматы, стикеры\n\n"
        "📄 `sudo медиа файлы`\n"
        "└ Смена расширений, очистка EXIF\n\n"
        "──────────────\n"
        "💡 *Ответьте командой на медиа для обработки.*"
    ),
    "audio": (
        "🎵 **AUDIO STUDIO**\n"
        "──────────────\n\n"
        "• `.pitch [-10..+10]` — Плавный Stellio-питч\n"
        "• `.bass [1..10]` — Ступенчатый сабвуфер\n"
        "• `.reverb` — Объёмное концертное эхо\n"
        "• `.slow` — Slowed + Reverb атмосфера\n"
        "• `.cut [00:00-00:00]` — Обрезка трека\n"
        "• `.cover` — Установить обложку песни\n"
        "• `.voice` / `.гс` — Перевод в Voice Note\n"
        "• `.norm` — Мастеринг громкости EBU R128"
    ),
    "video": (
        "📹 **VIDEO STUDIO**\n"
        "──────────────\n\n"
        "• `.cstick` / `.кругстик` — Круглый стикер (без фона)\n"
        "• `.round` / `.круг` — Видео в кружок 1:1\n"
        "• `.unround` — Кружок обратно в видео MP4\n"
        "• `.vcut [00:00-00:00]` — Обрезка видео\n"
        "• `.mute` — Удалить звуковую дорожку\n"
        "• `.audio` — Извлечь чистый MP3\n"
        "• `.webm` — В живой видеостикер\n"
        "• `.gif` — В плавную зацикленную GIF"
    ),
    "photo": (
        "🖼️ **PHOTO & STICKERS**\n"
        "──────────────\n\n"
        "• `.rmbg black` — Срезать черный фон (PNG)\n"
        "• `.rmbg white` — Срезать белый фон (PNG)\n"
        "• `.to [png|jpg|webp|pdf|ico|gif]` — Конвертация\n"
        "• `.sticker` — Фото в WebP стикер"
    ),
    "files": (
        "📄 **FILES & METADATA**\n"
        "──────────────\n\n"
        "• `.ext [расширение]` — Смена типа файла\n"
        "• `.clean` — Полная очистка метаданных"
    )
}

# --- ТОЧКА ВХОДА (НОВЫЙ СТАНДАРТ API) ---
def register(client, bot=None):
    init_media_db()

    async def check_access(event, consume_quota=False) -> tuple[bool, str]:
        sid = event.sender_id
        cid = event.chat_id
        if not sid:
            return False, "Неизвестный отправитель."

        if sid == OWNER_ID or await is_authorized(event):
            return True, "unlimited"

        if cid == TARGET_CHAT_ID:
            if consume_quota:
                allowed, left = check_and_inc_limit(sid)
                if not allowed:
                    return False, f"⚠️ Достигнут лимит: **{DAILY_LIMIT}/{DAILY_LIMIT}** операций в день."
                return True, f"Осталось операций: **{left}**"
            return True, "ok"

        return False, "Доступ ограничен."

    # Меню команд
    @client.on(events.NewMessage(func=lambda e: (e.raw_text or "").replace('\xa0', ' ').strip().lower().startswith(("sudo медиа", "sudo media", "медиа", "media"))))
    async def media_menu_handler(event):
        has_access, _ = await check_access(event, consume_quota=False)
        if not has_access:
            return

        tokens = event.raw_text.replace('\xa0', ' ').strip().lower().split()

        if any(w in tokens for w in ("видео", "video")):
            text = MENUS["video"]
        elif any(w in tokens for w in ("аудио", "audio", "звук")):
            text = MENUS["audio"]
        elif any(w in tokens for w in ("фото", "photo", "фон", "стикеры", "стикер")):
            text = MENUS["photo"]
        elif any(w in tokens for w in ("файлы", "files", "мета", "файл")):
            text = MENUS["files"]
        else:
            text = MENUS["main"]

        await event.reply(text)

    # Интерактивная смена обложки
    @client.on(events.NewMessage(func=lambda e: (e.chat_id, e.sender_id) in COVER_WAITING))
    async def cover_catcher_handler(event):
        key = (event.chat_id, event.sender_id)
        audio_msg = COVER_WAITING.pop(key, None)
        if not audio_msg:
            return

        is_valid_image = bool(event.photo or (event.document and "image" in (event.document.mime_type or "")) or event.sticker)
        if not is_valid_image:
            return await event.reply("❌ Операция отменена: ожидалось изображение или стикер.")

        status = await event.reply("🎨 `Вшиваем новую обложку...`")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            raw_audio = await audio_msg.download_media(file=tmp_path / "track_in")
            raw_cover = await event.download_media(file=tmp_path / "cover_in")

            if not raw_audio or not raw_cover:
                return await status.edit("❌ Ошибка при скачивании файлов.")

            clean_cover = tmp_path / "cover.jpg"
            out_track = tmp_path / "track_out.mp3"

            ok_img = await run_ffmpeg([
                "-i", str(raw_cover),
                "-vf", "scale='if(gt(iw,ih),600,-1)':'if(gt(iw,ih),-1,600)'",
                "-q:v", "2", str(clean_cover)
            ])

            ok_audio = await run_ffmpeg([
                "-i", str(raw_audio),
                "-i", str(clean_cover),
                "-map", "0:a", "-map", "1:v",
                "-c:a", "copy", "-c:v", "mjpeg",
                "-id3v2_version", "3",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)",
                "-disposition:v:0", "attached_pic",
                str(out_track)
            ])

            if not (ok_img and ok_audio) or not out_track.exists():
                return await status.edit("❌ Ошибка FFmpeg при сборке трека.")

            title, performer = "Track", "Artist"
            if audio_msg.file and audio_msg.file.name:
                title = Path(audio_msg.file.name).stem
            for attr in getattr(audio_msg.document, 'attributes', []):
                if isinstance(attr, DocumentAttributeAudio):
                    title = attr.title or title
                    performer = attr.performer or performer

            await event.client.send_file(
                event.chat_id,
                file=str(out_track),
                reply_to=event.id,
                attributes=[DocumentAttributeAudio(title=title, performer=performer, duration=audio_msg.file.duration or 0)]
            )
            await status.delete()

    # Основной диспетчер обработки медиа
    @client.on(events.NewMessage(pattern=r"^(\.[a-zA-Zа-яА-Я0-9_-]+)(?:\s+(.*))?$"))
    async def media_process_handler(event):
        cmd = event.pattern_match.group(1).lower()
        args = (event.pattern_match.group(2) or "").strip()

        commands_list = {
            ".pitch", ".bass", ".reverb", ".slow", ".cut", ".voice", ".гс", ".norm", ".cover",
            ".round", ".круг", ".unround", ".vcut", ".mute", ".audio", ".gif", ".webm",
            ".cstick", ".кругстик", ".rmbg", ".to", ".sticker", ".ext", ".clean"
        }
        if cmd not in commands_list:
            return

        has_access, quota_msg = await check_access(event, consume_quota=True)
        if not has_access:
            return await event.reply(quota_msg)

        reply_msg = await event.get_reply_message()
        if not reply_msg or not reply_msg.media:
            return await event.reply("⚠️ Ответьте командой на медиафайл.")

        if cmd == ".cover":
            if not reply_msg.audio and not (reply_msg.document and "audio" in (reply_msg.document.mime_type or "")):
                return await event.reply("⚠️ Команда `.cover` работает только в ответ на аудиофайл!")
            COVER_WAITING[(event.chat_id, event.sender_id)] = reply_msg
            return await event.reply("🖼️ **Жду обложку!** Отправьте следующим сообщением фото или стикер.")

        status = await event.reply("⏳ `Обработка медиа...`")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            in_file = await reply_msg.download_media(file=tmp_path / "input")
            if not in_file:
                return await status.edit("❌ Не удалось скачать медиа.")

            in_file = Path(in_file)
            out_file = tmp_path / "output"
            as_voice = False
            as_video_note = False
            force_document = False
            mime_type = None
            custom_attributes = []

            # Детекция векторных TGS стикеров
            is_tgs = (in_file.suffix.lower() == ".tgs") or (getattr(reply_msg, "document", None) and "tgsticker" in (reply_msg.document.mime_type or ""))
            if not is_tgs and in_file.exists():
                try:
                    with open(in_file, "rb") as f:
                        if f.read(2) == b"\x1f\x8b":
                            import gzip
                            with gzip.open(in_file, "rt", encoding="utf-8") as gf:
                                if '"v":' in gf.read(80):
                                    is_tgs = True
                except Exception:
                    pass

            try:
                # 🎵 АУДИО
                if cmd == ".pitch":
                    step = int(args) if args.lstrip('-+').isdigit() else 0
                    step = max(min(step, 10), -10)
                    freq = int(44100 * (2 ** (step / 36.0)))
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-af", f"asetrate={freq},aresample=44100", "-q:a", "2", str(out_file)])

                elif cmd == ".bass":
                    val = float(args) if args.replace('.', '', 1).isdigit() else 3.0
                    val = max(min(val, 10.0), 1.0)
                    gain = round(val * 1.8, 1)
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-af", f"bass=g={gain}:f=100:w=0.6", "-q:a", "2", str(out_file)])

                elif cmd == ".reverb":
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-af", "aecho=0.8:0.7:40:0.35,stereowiden", "-q:a", "2", str(out_file)])

                elif cmd == ".slow":
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-af", "asetrate=44100*0.88,aresample=44100,aecho=0.8:0.88:60:0.4", "-q:a", "2", str(out_file)])

                elif cmd == ".cut":
                    times = parse_time_range(args)
                    if not times:
                        return await status.edit("❌ Формат: `.cut 00:00-00:00` (напр. `.cut 00:15-01:30`)")
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-ss", times[0], "-to", times[1], "-i", str(in_file), "-q:a", "2", str(out_file)])

                elif cmd in (".voice", ".гс"):
                    out_file = out_file.with_suffix(".ogg")
                    ok = await run_ffmpeg(["-i", str(in_file), "-vn", "-c:a", "libopus", "-b:a", "48k", str(out_file)])
                    as_voice = True

                elif cmd == ".norm":
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-q:a", "2", str(out_file)])

                # 📹 ВИДЕО
                elif cmd in (".round", ".круг"):
                    out_file = out_file.with_suffix(".mp4")
                    vf = "crop='min(iw,ih)':'min(iw,ih)',scale=512:512,setsar=1"
                    ok = await run_ffmpeg(["-i", str(in_file), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "aac", "-b:a", "64k", str(out_file)])
                    as_video_note = True

                elif cmd == ".unround":
                    out_file = out_file.with_suffix(".mp4")
                    ok = await run_ffmpeg(["-i", str(in_file), "-c", "copy", str(out_file)])

                elif cmd == ".vcut":
                    times = parse_time_range(args)
                    if not times:
                        return await status.edit("❌ Формат: `.vcut 00:00-00:00` (напр. `.vcut 00:10-00:40`)")
                    out_file = out_file.with_suffix(".mp4")
                    ok = await run_ffmpeg(["-ss", times[0], "-to", times[1], "-i", str(in_file), "-c", "copy", str(out_file)])

                elif cmd == ".mute":
                    out_file = out_file.with_suffix(".mp4")
                    ok = await run_ffmpeg(["-i", str(in_file), "-c:v", "copy", "-an", str(out_file)])

                elif cmd == ".audio":
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-vn", "-q:a", "2", str(out_file)])

                # ⭕ КРУГЛЫЙ ВИДЕОСТИКЕР
                elif cmd in (".cstick", ".кругстик"):
                    out_file = out_file.with_suffix(".webm")
                    times = parse_time_range(args) if args else None
                    time_flags = ["-ss", times[0], "-to", times[1]] if times else ["-t", "3"]

                    vf = (
                        "crop='min(iw,ih)':'min(iw,ih)',scale=512:512,"
                        "format=yuva420p,"
                        "geq=lum='p(X,Y)':a='if(lte(hypot(X-256\,Y-256)\,254)\,255\,if(lte(hypot(X-256\,Y-256)\,256)\,(256-hypot(X-256\,Y-256))*127.5\,0))'"
                    )

                    ok = await run_ffmpeg([
                        *time_flags,
                        "-i", str(in_file),
                        "-r", "30",
                        "-vf", vf,
                        "-c:v", "libvpx-vp9",
                        "-crf", "32",
                        "-b:v", "256k",
                        "-an",
                        "-fs", "256K",
                        str(out_file)
                    ])
                    mime_type = "video/webm"
                    custom_attributes = [
                        DocumentAttributeSticker(alt="✨", stickerset=InputStickerSetEmpty()),
                        DocumentAttributeVideo(duration=3, w=512, h=512),
                        DocumentAttributeImageSize(w=512, h=512)
                    ]

                # 🎬 ЖИВОЙ ВИДЕОСТИКЕР WEBM
                elif cmd == ".webm":
                    out_file = out_file.with_suffix(".webm")
                    vf = "scale=512:512:force_original_aspect_ratio=decrease"
                    ok = await run_ffmpeg([
                        "-i", str(in_file),
                        "-t", "3",
                        "-r", "30",
                        "-vf", vf,
                        "-c:v", "libvpx-vp9",
                        "-crf", "32",
                        "-b:v", "256k",
                        "-pix_fmt", "yuva420p",
                        "-an",
                        "-fs", "256K",
                        str(out_file)
                    ])
                    mime_type = "video/webm"
                    custom_attributes = [
                        DocumentAttributeSticker(alt="✨", stickerset=InputStickerSetEmpty()),
                        DocumentAttributeVideo(duration=3, w=512, h=512),
                        DocumentAttributeImageSize(w=512, h=512)
                    ]

                # 🎞️ GIF АНИМАЦИЯ
                elif cmd == ".gif":
                    out_file = out_file.with_suffix(".mp4")
                    if is_tgs:
                        if not HAS_RLOTTIE:
                            return await status.edit("⚠️ Для конвертации .tgs стикера нужен `rlottie-python`.")
                        tmp_gif = tmp_path / "temp.gif"
                        anim = LottieAnimation.from_tgs(str(in_file))
                        anim.save_animation(str(tmp_gif))
                        vf = "fps=20,scale=480:-1:flags=lanczos"
                        ok = await run_ffmpeg(["-i", str(tmp_gif), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-an", str(out_file)])
                    else:
                        vf = "fps=20,scale=480:-1:flags=lanczos"
                        ok = await run_ffmpeg(["-i", str(in_file), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-an", str(out_file)])

                    mime_type = "video/mp4"
                    custom_attributes = [
                        DocumentAttributeAnimated(),
                        DocumentAttributeVideo(duration=0, w=0, h=0, supports_streaming=True)
                    ]

                # 🖼️ КОНВЕРТЕР .TO
                elif cmd == ".to":
                    ext = args.lower().lstrip(".") or "png"
                    out_file = out_file.with_suffix(f".{ext}")

                    if is_tgs:
                        if not HAS_RLOTTIE:
                            return await status.edit("⚠️ Для .tgs стикеров нужен `rlottie-python`.")
                        anim = LottieAnimation.from_tgs(str(in_file))
                        if ext in ("gif", "webp", "apng"):
                            anim.save_animation(str(out_file))
                            ok = True
                        else:
                            tmp_gif = tmp_path / "temp.gif"
                            anim.save_animation(str(tmp_gif))
                            ok = await run_ffmpeg(["-i", str(tmp_gif), str(out_file)])
                    elif ext == "gif":
                        ok = await run_ffmpeg(["-i", str(in_file), "-vf", "fps=18,scale=512:-1:flags=lanczos", str(out_file)])
                        mime_type = "image/gif"
                    else:
                        ok = await run_ffmpeg(["-i", str(in_file), str(out_file)])

                # 🖼️ ФОТО / СТИКЕРЫ
                elif cmd == ".rmbg":
                    color = "0xFFFFFF" if args.lower() == "white" else "0x000000"
                    out_file = out_file.with_suffix(".png")
                    vf = f"colorkey={color}:0.18:0.1,format=rgba"
                    ok = await run_ffmpeg(["-i", str(in_file), "-vf", vf, str(out_file)])
                    force_document = True

                elif cmd == ".sticker":
                    out_file = out_file.with_suffix(".webp")
                    vf = "scale=512:512:force_original_aspect_ratio=decrease"
                    ok = await run_ffmpeg(["-i", str(in_file), "-vf", vf, str(out_file)])
                    mime_type = "image/webp"
                    custom_attributes = [
                        DocumentAttributeSticker(alt="✨", stickerset=InputStickerSetEmpty()),
                        DocumentAttributeImageSize(w=512, h=512)
                    ]

                # 📄 ФАЙЛЫ
                elif cmd == ".ext":
                    target_ext = args.strip().lstrip(".")
                    if not target_ext:
                        return await status.edit("❌ Укажите расширение: `.ext py`")
                    out_file = out_file.with_suffix(f".{target_ext}")
                    shutil.copyfile(in_file, out_file)
                    force_document = True
                    ok = True

                elif cmd == ".clean":
                    ext = in_file.suffix or ".dat"
                    out_file = out_file.with_suffix(ext)
                    ok = await run_ffmpeg(["-i", str(in_file), "-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy", str(out_file)])
                    force_document = True

                else:
                    return await status.delete()

                if not ok or not out_file.exists():
                    return await status.edit("❌ Ошибка обработки файла.")

                await event.client.send_file(
                    event.chat_id,
                    file=str(out_file),
                    reply_to=reply_msg.id,
                    voice_note=as_voice,
                    video_note=as_video_note,
                    force_document=force_document,
                    mime_type=mime_type,
                    attributes=custom_attributes if custom_attributes else None
                )
                await status.delete()

            except Exception as e:
                LOGGER.error(f"MediaStudio error: {e}")
                await status.edit(f"⚠️ Ошибка: `{e}`")
