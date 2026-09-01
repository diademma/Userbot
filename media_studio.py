# media_studio.py — Независимый мультимедиа комбайн для Telethon
import os
import re
import math
import shutil
import asyncio
import logging
import tempfile
from pathlib import Path
from telethon import events
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    DocumentAttributeFilename
)

LOGGER = logging.getLogger("MediaStudio")
LEX_BOT_ID = 8617655235
LEX_BOT_USERNAME = "my_LEX_superbot"
OWNER_ID = 5421909121

# Ограничение параллельных тяжелых задач FFmpeg на раннере GitHub
SEMAPHORE = asyncio.Semaphore(2)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def run_ffmpeg(cmd: list[str], timeout: int = 180) -> bool:
    async with SEMAPHORE:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-y", *cmd,
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
    """Парсит формат обрезки: 00:00-00:00, 1:15-2:30 или 15-45"""
    m = re.match(r"^(\d+(?::\d+)*(?:\.\d+)?)-(\d+(?::\d+)*(?:\.\d+)?)$", val.strip())
    if not m:
        return None
    return m.group(1), m.group(2)

# --- ШАБЛОНЫ МЕНЮ С ДИПЛИНКАМИ ---
MENUS = {
    "main": (
        "🎛️ **𝗠𝗘𝗗𝗜𝗔 𝗦𝗧𝗨𝗗𝗜𝗢**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите раздел для работы с медиа:\n\n"
        f"[ 🎵 Аудио студия ](https://t.me/{LEX_BOT_USERNAME}?start=help_audio)  •  "
        f"[ 📹 Видео студия ](https://t.me/{LEX_BOT_USERNAME}?start=help_video)\n"
        f"[ 🖼️ Фото и Фон ](https://t.me/{LEX_BOT_USERNAME}?start=help_photo)  •  "
        f"[ 📄 Файлы и Мета ](https://t.me/{LEX_BOT_USERNAME}?start=help_files)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 Быстрый вызов: `sudo медиа [аудио | видео | фото | файлы]`"
    ),
    "audio": (
        "🎵 **𝗔𝗨𝗗𝗜𝗢 𝗦𝗧𝗨𝗗𝗜𝗢**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• `.pitch [-10..+10]` — Тон + скорость (полутона)\n"
        "• `.bass [100/200/300/666]` — Усиление баса\n"
        "• `.reverb` — Пространственный реверб\n"
        "• `.slow` — Slowed + Reverb\n"
        "• `.cut [старт-конец]` — Обрезка (`.cut 00:15-01:30`)\n"
        "• `.voice` / `.гс` — Перевод в Voice Note (Opus)\n"
        "• `.norm` — Мастеринг громкости (EBU R128)\n"
        "• `.tag \"Артист\" \"Трек\"` — Смена ID3 тегов\n"
        "• `.cover` — Вшить обложку из реплая\n\n"
        f"[ ⬅️ Главное меню ](https://t.me/{LEX_BOT_USERNAME}?start=help_main)"
    ),
    "video": (
        "📹 **𝗩𝗜𝗗𝗘𝗢 𝗦𝗧𝗨𝗗𝗜𝗢**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• `.round` / `.круг` — Видео в видео-кружок 1:1\n"
        "• `.unround` — Кружок обратно в видео\n"
        "• `.vcut [старт-конец]` — Обрезка (`.vcut 00:05-00:45`)\n"
        "• `.mute` — Удалить звуковую дорожку\n"
        "• `.audio` — Извлечь звук в MP3\n"
        "• `.gif` / `.webm` — В GIF / WebM видеостикер\n\n"
        f"[ ⬅️ Главное меню ](https://t.me/{LEX_BOT_USERNAME}?start=help_main)"
    ),
    "photo": (
        "🖼️ **𝗣𝗛𝗢𝗧𝗢 & 𝗦𝗧𝗜𝗖𝗞𝗘𝗥𝗦**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• `.rmbg black` — Удалить черный фон (в PNG)\n"
        "• `.rmbg white` — Удалить белый фон (в PNG)\n"
        "• `.to [png|jpg|webp|pdf|ico|tif]` — Конвертация\n"
        "• `.sticker` — Картинку в WebP стикер\n\n"
        f"[ ⬅️ Главное меню ](https://t.me/{LEX_BOT_USERNAME}?start=help_main)"
    ),
    "files": (
        "📄 **𝗙𝗜𝗟𝗘𝗦 & 𝗠𝗘𝗧𝗔𝗗𝗔𝗧𝗔**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• `.ext [расширение]` — Сменить расширение (`.ext py`)\n"
        "• `.clean` — Полная очистка метаданных и EXIF\n\n"
        f"[ ⬅️ Главное меню ](https://t.me/{LEX_BOT_USERNAME}?start=help_main)"
    )
}

def register_media_studio(client, is_authorized_cb=None):
    async def check_access(event) -> bool:
        sid = event.sender_id
        if not sid:
            return False
        if sid in (OWNER_ID, LEX_BOT_ID):
            return True
        if is_authorized_cb and await is_authorized_cb(event):
            return True
        return False

    # --- ХЭНДЛЕР МЕНЮ SUDO МЕДИА ---
    @client.on(events.NewMessage(pattern=r"^(?:sudo\s+)?(?:медиа|media)(?:\s+(.*))?$", func=lambda e: not e.is_private or e.sender_id in (OWNER_ID, LEX_BOT_ID)))
    async def media_menu_handler(event):
        if not await check_access(event):
            return
        section = (event.pattern_match.group(1) or "").strip().lower()
        mapping = {
            "аудио": "audio", "звук": "audio", "audio": "audio",
            "видео": "video", "video": "video",
            "фото": "photo", "photo": "photo", "фон": "photo",
            "файлы": "files", "files": "files", "мета": "files"
        }
        text = MENUS.get(mapping.get(section, "main"), MENUS["main"])
        await event.reply(text, link_preview=False)

    # --- ХЭНДЛЕР ОБРАБОТКИ МЕДИА ---
    @client.on(events.NewMessage(pattern=r"^(\.[a-zA-Zа-яА-Я0-9_-]+)(?:\s+(.*))?$"))
    async def media_process_handler(event):
        if not await check_access(event):
            return

        cmd = event.pattern_match.group(1).lower()
        args = (event.pattern_match.group(2) or "").strip()

        commands_list = {
            ".pitch", ".bass", ".reverb", ".slow", ".cut", ".voice", ".гс", ".norm", ".tag", ".cover",
            ".round", ".круг", ".unround", ".vcut", ".mute", ".audio", ".gif", ".webm",
            ".rmbg", ".to", ".sticker", ".ext", ".clean"
        }
        if cmd not in commands_list:
            return

        reply_msg = await event.get_reply_message()
        if not reply_msg or not reply_msg.media:
            return await event.reply("⚠️ Ответьте командой на медиафайл.")

        status = await event.reply("⏳ `Обработка медиа...`")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            in_file = await reply_msg.download_media(file=tmp_path / "input")
            if not in_file:
                return await status.edit("❌ Не удалось загрузить медиа.")

            in_file = Path(in_file)
            out_file = tmp_path / "output"
            extra_attrs = []
            as_voice = False
            as_video_note = False
            force_document = False

            try:
                # 🎵 АУДИО БЛОК
                if cmd == ".pitch":
                    step = int(args) if args.lstrip('-+').isdigit() else 0
                    step = max(min(step, 10), -10)
                    freq = int(44100 * (2 ** (step / 12.0)))
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-af", f"asetrate={freq},aresample=44100", "-q:a", "2", str(out_file)])

                elif cmd == ".bass":
                    gains = {"100": 6, "200": 12, "300": 18, "666": 28}
                    gain = gains.get(args, 10)
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-af", f"bass=g={gain}:f=110:w=0.6", "-q:a", "2", str(out_file)])

                elif cmd == ".reverb":
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-af", "aecho=0.8:0.7:40:0.35,stereowiden", "-q:a", "2", str(out_file)])

                elif cmd == ".slow":
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-af", "asetrate=44100*0.85,aresample=44100,aecho=0.8:0.88:60:0.4", "-q:a", "2", str(out_file)])

                elif cmd == ".cut":
                    times = parse_time_range(args)
                    if not times:
                        return await status.edit("❌ Формат: `.cut 00:00-00:00` (например `.cut 00:15-01:30`)")
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-ss", times[0], "-to", times[1], "-i", str(in_file), "-c", "copy", str(out_file)])

                elif cmd in (".voice", ".гс"):
                    out_file = out_file.with_suffix(".ogg")
                    ok = await run_ffmpeg(["-i", str(in_file), "-vn", "-c:a", "libopus", "-b:a", "48k", str(out_file)])
                    as_voice = True

                elif cmd == ".norm":
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-q:a", "2", str(out_file)])

                # 📹 ВИДЕО БЛОК
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
                        return await status.edit("❌ Формат: `.vcut 00:00-00:00` (например `.vcut 00:10-00:40`)")
                    out_file = out_file.with_suffix(".mp4")
                    ok = await run_ffmpeg(["-ss", times[0], "-to", times[1], "-i", str(in_file), "-c", "copy", str(out_file)])

                elif cmd == ".mute":
                    out_file = out_file.with_suffix(".mp4")
                    ok = await run_ffmpeg(["-i", str(in_file), "-c:v", "copy", "-an", str(out_file)])

                elif cmd == ".audio":
                    out_file = out_file.with_suffix(".mp3")
                    ok = await run_ffmpeg(["-i", str(in_file), "-vn", "-q:a", "2", str(out_file)])

                elif cmd in (".gif", ".webm"):
                    out_file = out_file.with_suffix(".gif" if cmd == ".gif" else ".webm")
                    vf = "fps=15,scale=480:-1:flags=lanczos"
                    ok = await run_ffmpeg(["-i", str(in_file), "-vf", vf, str(out_file)])

                # 🖼️ ФОТО / ГРАФИКА
                elif cmd == ".rmbg":
                    mode = args.lower()
                    color = "0xFFFFFF" if mode == "white" else "0x000000"
                    out_file = out_file.with_suffix(".png")
                    vf = f"colorkey={color}:0.18:0.1,format=rgba"
                    ok = await run_ffmpeg(["-i", str(in_file), "-vf", vf, str(out_file)])
                    force_document = True

                elif cmd == ".to":
                    ext = args.lower().lstrip(".") or "png"
                    out_file = out_file.with_suffix(f".{ext}")
                    ok = await run_ffmpeg(["-i", str(in_file), str(out_file)])

                elif cmd == ".sticker":
                    out_file = out_file.with_suffix(".webp")
                    vf = "scale='if(gt(iw,ih),512,-1)':'if(gt(iw,ih),-1,512)'"
                    ok = await run_ffmpeg(["-i", str(in_file), "-vf", vf, str(out_file)])

                # 📄 ФАЙЛЫ И МЕТАДАННЫЕ
                elif cmd == ".ext":
                    target_ext = args.strip().lstrip(".")
                    if not target_ext:
                        return await status.edit("❌ Укажите расширение: `.ext py` / `.ext plugin`")
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
                    return await status.edit("❌ Ошибка обработки FFmpeg.")

                # Отправка результата в тот же контекст
                await event.client.send_file(
                    event.chat_id,
                    file=str(out_file),
                    reply_to=reply_msg.id,
                    voice_note=as_voice,
                    video_note=as_video_note,
                    force_document=force_document
                )
                await status.delete()

            except Exception as e:
                LOGGER.error(f"MediaStudio error: {e}")
                await status.edit(f"⚠️ Ошибка: `{e}`")
