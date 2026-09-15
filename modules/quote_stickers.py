# modules/quote_stickers.py — Генератор 3D-видеостикеров v3.0 (Pure PIL Engine / No-CV2)
import os
import re
import math
import random
import sqlite3
import logging
import tempfile
import urllib.request
from pathlib import Path
from datetime import datetime
import asyncio
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Поддержка эмодзи Apple / iOS
try:
    from pilmoji import Pilmoji
    from pilmoji.source import AppleEmojiSource
    HAS_PILMOJI = True
except Exception:
    HAS_PILMOJI = False

from telethon import events
from telethon.tl.types import (
    DocumentAttributeVideo,
    DocumentAttributeSticker,
    DocumentAttributeImageSize,
    InputStickerSetEmpty
)

from core.config import OWNER_ID, DB_NAME
from core.db import is_authorized

TITLE = "❝ Quote Stickers"
BANNER = "https://raw.githubusercontent.com/diademma/Userbot/main/assets/LLEHTABPA.jpg"
COMMANDS = (
    "• sudo цитата [текст] — Создать 3D-видеостикер\n"
    "• sudo цитата (в реплай) — Взять текст из сообщения\n"
    "• sudo цитата [1|2] [текст] — Выбор шаблона:\n"
    "  └ 1: Девочка в желтой шапке с блокнотом\n"
    "  └ 2: Девочка разворачивает рисунок\n\n"
    "⚙️ ПАРАМЕТРЫ:\n"
    "• Лимит длины: до 45 символов"
)

LOGGER = logging.getLogger("QuoteStickers")

TARGET_CHAT_ID = -1002281822286
DAILY_LIMIT = 3
MAX_CHAR_LIMIT = 45

FONT_URLS = [
    "https://raw.githubusercontent.com/google/fonts/main/ofl/neucha/Neucha.ttf",
    "https://raw.githubusercontent.com/anton-liubushkin/cyrillic-google-fonts/master/fonts/MarckScript-Regular.ttf",
    "https://raw.githubusercontent.com/anton-liubushkin/cyrillic-google-fonts/master/fonts/BadScript-Regular.ttf"
]

TEMPLATES = {
    1: {
        "file": "templates/01.mp4",
        "start_time": 0.501,
        "end_time": 1.300,
        "is_static": True,
        "pose_1": {
            "corners": [(82, 210), (237, 170), (256, 275), (114, 315)],
            "fingers": []
        }
    },
    2: {
        "file": "templates/02.mp4",
        "start_time": 0.420,
        "end_time": 99.0,
        "is_static": False,
        "pose_1": {
            "time_sec": 0.534,
            "corners": [(170, 260), (275, 223), (305, 349), (220, 405)],
            "fingers": [(302, 301), (288, 299), (277, 312), (282, 323), (288, 336), (300, 347), (310, 353)]
        },
        "pose_2": {
            "time_sec": 0.634,
            "corners": [(73, 295), (256, 228), (312, 355), (132, 427)],
            "fingers": [(310, 350), (295, 342), (281, 333), (282, 316), (295, 318), (309, 311)]
        }
    }
}

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quote_limits (
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
    cur.execute("SELECT count FROM quote_limits WHERE user_id = ? AND usage_date = ?", (user_id, today))
    row = cur.fetchone()
    current_count = row[0] if row else 0

    if current_count >= DAILY_LIMIT:
        conn.close()
        return False, 0

    new_count = current_count + 1
    cur.execute("INSERT OR REPLACE INTO quote_limits (user_id, usage_date, count) VALUES (?, ?, ?)", (user_id, today, new_count))
    conn.commit()
    conn.close()
    return True, DAILY_LIMIT - new_count

def get_font_path():
    fonts_dir = Path("templates/fonts")
    fonts_dir.mkdir(parents=True, exist_ok=True)
    font_path = fonts_dir / "Handwritten.ttf"
    
    if not font_path.exists() or font_path.stat().st_size < 1000:
        for url in FONT_URLS:
            try:
                LOGGER.info("Скачиваю жирный маркерный шрифт...")
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as resp, open(font_path, "wb") as f:
                    f.write(resp.read())
                if font_path.stat().st_size > 1000:
                    break
            except Exception:
                continue

    if not font_path.exists() or font_path.stat().st_size < 1000:
        sys_font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if sys_font.exists():
            return str(sys_font)
                
    return str(font_path)

# Матричный расчет коэффициентов проекции для чистого PIL
def find_perspective_coeffs(source_coords, target_coords):
    matrix = []
    for (x, y), (X, Y) in zip(source_coords, target_coords):
        matrix.extend([
            [X, Y, 1, 0, 0, 0, -x * X, -x * Y],
            [0, 0, 0, X, Y, 1, -y * X, -y * Y]
        ])
    A = np.matrix(matrix, dtype=float)
    B = np.array(source_coords).reshape(8)
    res = np.dot(np.linalg.inv(A.T * A) * A.T, B)
    return np.array(res).reshape(8)

# Рендер текста на карточке через чистый Pillow
def render_text_plate(text: str, card_w=400, card_h=300) -> Image.Image:
    img = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    font_file = get_font_path()

    words = text.split()
    pad_x, pad_y = 22, 18
    avail_w = card_w - (pad_x * 2)
    avail_h = card_h - (pad_y * 2)

    font_size = 150
    best_lines = []
    best_font = None

    while font_size > 22:
        try:
            font = ImageFont.truetype(font_file, font_size)
        except Exception:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

        lines = []
        curr = ""
        fits = True

        for w in words:
            test = f"{curr} {w}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if (bbox[2] - bbox[0]) <= avail_w:
                curr = test
            else:
                if curr: lines.append(curr)
                if (draw.textbbox((0, 0), w, font=font)[2] - draw.textbbox((0, 0), w, font=font)[0]) > avail_w:
                    fits = False
                    break
                curr = w

        if curr: lines.append(curr)

        if fits and lines:
            line_h = font_size * 1.05
            if (len(lines) * line_h) <= avail_h:
                best_lines = lines
                best_font = font
                break

        font_size -= 3

    if not best_lines:
        best_lines = [text]
        best_font = ImageFont.load_default()
        font_size = 24

    line_h = font_size * 1.05
    total_h = len(best_lines) * line_h
    start_y = pad_y + (avail_h - total_h) / 2
    text_color = (195, 25, 45, 255)

    if HAS_PILMOJI:
        with Pilmoji(img, source=AppleEmojiSource) as pilmoji:
            for i, line in enumerate(best_lines):
                bbox = draw.textbbox((0, 0), line, font=best_font)
                x = pad_x + (avail_w - (bbox[2] - bbox[0])) / 2
                y = start_y + (i * line_h)
                pilmoji.text((x, y), line, fill=text_color, font=best_font)
    else:
        for i, line in enumerate(best_lines):
            bbox = draw.textbbox((0, 0), line, font=best_font)
            x = pad_x + (avail_w - (bbox[2] - bbox[0])) / 2
            y = start_y + (i * line_h)
            draw.text((x, y), line, fill=text_color, font=best_font)

    return img

async def generate_quote_sticker(text: str, template_num: int, output_file: str) -> bool:
    cfg = TEMPLATES.get(template_num, TEMPLATES[2])
    template_path = cfg["file"]
    if not os.path.exists(template_path):
        LOGGER.error(f"Шаблон {template_path} не найден!")
        return False

    card_w, card_h = 400, 300
    plate_img = render_text_plate(text, card_w=card_w, card_h=card_h)
    src_corners = [(0, 0), (card_w, 0), (card_w, card_h), (0, card_h)]

    # Получаем FPS и длительность шаблона через ffprobe
    fps = 25.0
    try:
        cmd_fps = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", template_path]
        proc_fps = await asyncio.create_subprocess_exec(*cmd_fps, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc_fps.communicate()
        num, den = out.decode().strip().split('/')
        fps = float(num) / float(den)
    except Exception:
        pass

    # Извлекаем кадры в память через ffmpeg пайп (RGB24)
    ffmpeg_in_cmd = [
        "ffmpeg", "-hide_banner", "-i", template_path,
        "-vf", "scale=512:512",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"
    ]
    proc_in = await asyncio.create_subprocess_exec(*ffmpeg_in_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

    # Запускаем кодировщик WebM (VP9)
    ffmpeg_out_cmd = [
        'ffmpeg', '-hide_banner', '-y',
        '-f', 'rawvideo', '-vcodec', 'rawvideo',
        '-s', '512x512', '-pix_fmt', 'rgba', '-r', str(fps),
        '-i', '-',
        '-c:v', 'libvpx-vp9', '-crf', '30', '-b:v', '250k',
        '-pix_fmt', 'yuva420p', '-an', '-fs', '250K',
        output_file
    ]
    proc_out = await asyncio.create_subprocess_exec(*ffmpeg_out_cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

    start_t = cfg["start_time"]
    end_t = cfg["end_time"]
    is_static = cfg.get("is_static", False)

    frame_idx = 0
    frame_bytes = 512 * 512 * 3

    while True:
        raw_frame = await proc_in.stdout.readexactly(frame_bytes) if not proc_in.stdout.at_eof() else None
        if not raw_frame or len(raw_frame) < frame_bytes:
            break

        cur_t = frame_idx / fps
        frame_img = Image.frombytes("RGB", (512, 512), raw_frame).convert("RGBA")

        # Удаление чисто белого фона вокруг девочки (хромакей)
        arr = np.array(frame_img)
        white_mask = (arr[:, :, 0] > 240) & (arr[:, :, 1] > 240) & (arr[:, :, 2] > 240)
        arr[white_mask, 3] = 0
        frame_img = Image.fromarray(arr)

        # Наложение 3D таблички
        if start_t <= cur_t <= end_t:
            if is_static:
                dst_corners = cfg["pose_1"]["corners"]
                fingers = cfg["pose_1"]["fingers"]
            else:
                p1, p2 = cfg["pose_1"], cfg["pose_2"]
                if cur_t <= p1["time_sec"]:
                    dst_corners, fingers = p1["corners"], p1["fingers"]
                elif cur_t >= p2["time_sec"]:
                    dst_corners, fingers = p2["corners"], p2["fingers"]
                else:
                    factor = (cur_t - p1["time_sec"]) / (p2["time_sec"] - p1["time_sec"])
                    dst_corners = [
                        (int(p1["corners"][k][0] + (p2["corners"][k][0] - p1["corners"][k][0]) * factor),
                         int(p1["corners"][k][1] + (p2["corners"][k][1] - p1["corners"][k][1]) * factor))
                        for k in range(4)
                    ]
                    fingers = p2["fingers"] if factor > 0.5 else p1["fingers"]

            # Расчет перспективы в чистом Pillow
            coeffs = find_perspective_coeffs(src_corners, dst_corners)
            transformed_plate = plate_img.transform((512, 512), Image.PERSPECTIVE, coeffs, Image.BICUBIC)

            # Вырезаем пальчики поверх таблички
            if len(fingers) >= 3:
                f_mask = Image.new("L", (512, 512), 255)
                draw_f = ImageDraw.Draw(f_mask)
                draw_f.polygon(fingers, fill=0)
                transformed_plate.putalpha(Image.composite(transformed_plate.getchannel("A"), f_mask, f_mask))

            frame_img.alpha_composite(transformed_plate)

        try:
            proc_out.stdin.write(frame_img.tobytes())
            await proc_out.stdin.drain()
        except Exception:
            break

        frame_idx += 1

    try:
        proc_out.stdin.close()
        await proc_out.wait()
        await proc_in.wait()
    except Exception:
        pass

    return os.path.exists(output_file) and os.path.getsize(output_file) > 1000

# --- ТОЧКА ВХОДА API ---
def register(client, bot=None):
    init_db()

    async def check_access(event, consume_quota=False) -> tuple[bool, str]:
        sid = event.sender_id
        cid = event.chat_id
        if not sid: return False, "Неизвестный отправитель."
        if sid == OWNER_ID or await is_authorized(event): return True, "unlimited"

        if cid == TARGET_CHAT_ID:
            if consume_quota:
                allowed, left = check_and_inc_limit(sid)
                if not allowed:
                    return False, f"⚠️ Достигнут суточный лимит: **{DAILY_LIMIT}/{DAILY_LIMIT}** стикеров."
                return True, f"Осталось: **{left}**"
            return True, "ok"

        return False, "Доступ ограничен."

    CMD_REGEX = re.compile(r"^(?:sudo\s+)?(?:\.|\/)?(?:цитата|цит|quote)(?:\s+(1|2))?(?:\s+(.+))?$", re.IGNORECASE | re.DOTALL)

    @client.on(events.NewMessage(func=lambda e: bool(CMD_REGEX.match((e.raw_text or "").strip()))))
    async def quote_cmd_handler(event):
        has_access, quota_msg = await check_access(event, consume_quota=True)
        if not has_access:
            return await event.reply(quota_msg)

        raw = event.raw_text.strip()
        match = CMD_REGEX.match(raw)
        if not match: return

        tmpl_group = match.group(1)
        text_arg = (match.group(2) or "").strip()
        chosen_template = int(tmpl_group) if tmpl_group else None

        if not text_arg and event.is_reply:
            rep = await event.get_reply_message()
            text_arg = (rep.raw_text or rep.message or "").strip()

        if not text_arg:
            return await event.reply("❌ **Укажи текст!**\nПример: `sudo цитата Привет` или ответь на сообщение.")

        if len(text_arg) > MAX_CHAR_LIMIT:
            return await event.reply(
                f"⚠️ **Текст слишком длинный!**\n"
                f"Максимум **{MAX_CHAR_LIMIT}** символов (сейчас: {len(text_arg)}).\n"
                f"Табличка маленькая, сократи цитату."
            )

        if not chosen_template:
            chosen_template = random.choice([1, 2])

        status = await event.reply(f"🎨 `Генерирую 3D-видеостикер (Шаблон {chosen_template})...`")

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = os.path.join(tmp_dir, f"quote_{event.id}.webm")
            ok = await generate_quote_sticker(text_arg, chosen_template, out_file)
            if not ok:
                return await status.edit("❌ Ошибка сборки стикера. Проверь файлы `01.mp4` и `02.mp4` в папке `templates/`.")

            custom_attributes = [
                DocumentAttributeSticker(alt="✨", stickerset=InputStickerSetEmpty()),
                DocumentAttributeVideo(duration=2, w=512, h=512),
                DocumentAttributeImageSize(w=512, h=512)
            ]

            reply_target = event.reply_to_msg_id or event.id
            await event.client.send_file(
                event.chat_id,
                file=out_file,
                reply_to=reply_target,
                mime_type="video/webm",
                attributes=custom_attributes
            )
            await status.delete()
