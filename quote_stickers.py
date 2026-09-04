# quote_stickers.py — Высокоточный генератор 3D-видеостикеров v2.8
import os
import re
import time
import random
import sqlite3
import logging
import tempfile
import urllib.request
from pathlib import Path
from datetime import datetime
import asyncio
import subprocess

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Поддержка современных Apple / iOS эмодзи
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

LOGGER = logging.getLogger("QuoteStickers")

OWNER_ID = 5421909121
TARGET_CHAT_ID = -1002281822286
DAILY_LIMIT = 3
DB_NAME = "sniper_memory_v3.db"
MAX_CHAR_LIMIT = 45

# Надежные источники красивых жирных кириллических маркеров
FONT_URLS = [
    "https://raw.githubusercontent.com/google/fonts/main/ofl/neucha/Neucha.ttf",
    "https://raw.githubusercontent.com/anton-liubushkin/cyrillic-google-fonts/master/fonts/MarckScript-Regular.ttf",
    "https://raw.githubusercontent.com/anton-liubushkin/cyrillic-google-fonts/master/fonts/BadScript-Regular.ttf"
]

# =========================================================================
# ТОЧНЫЕ ШАБЛОНЫ 01.mp4 И 02.mp4
# =========================================================================
TEMPLATES = {
    1: {
        # Девочка в желтой шапке достает блокнот из-за спины
        "file": "templates/01.mp4",
        "start_time": 0.501,
        "end_time": 1.300,
        "is_static": True,
        "pose_1": {
            "corners": np.float32([[82, 210], [237, 170], [256, 275], [114, 315]]),
            "fingers": []
        }
    },
    2: {
        # Сидящая девочка разворачивает рисунок (с 0.420с)
        "file": "templates/02.mp4",
        "start_time": 0.420,
        "end_time": 99.0,
        "is_static": False,
        "pose_1": {
            "time_sec": 0.534,
            "corners": np.float32([[170, 260], [275, 223], [305, 349], [220, 405]]),
            "fingers": np.array([[302, 301], [288, 299], [277, 312], [282, 323], [288, 336], [300, 347], [310, 353]], dtype=np.int32)
        },
        "pose_2": {
            "time_sec": 0.634,
            "corners": np.float32([[73, 295], [256, 228], [312, 355], [132, 427]]),
            "fingers": np.array([[310, 350], [295, 342], [281, 333], [282, 316], [295, 318], [309, 311]], dtype=np.int32)
        }
    }
}

# --- БАЗА ДАННЫХ ЛИМИТОВ ---
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

# --- ЗАГРУЗКА ШРИФТА ---
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

# --- ИДЕАЛЬНОЕ УДАЛЕНИЕ ФОНА (ВКЛЮЧАЯ ОСТРОВКИ МЕЖДУ ВОЛОСАМИ) + МЯГКИЙ КРАЙ ---
def make_background_transparent(frame_bgra, protected_corners=None):
    """
    Удаляет внешний белый фон и внутренние белые островки (между волос/руками),
    защищая табличку, и накладывает мягкое субпиксельное сглаживание по краям.
    """
    h, w = frame_bgra.shape[:2]
    rgb = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_BGR2HSV)

    # 1. Детектор белых островков: очень светлые пиксели с околонулевой насыщенностью
    lower_white = np.array([0, 0, 236], dtype=np.uint8)
    upper_white = np.array([180, 20, 255], dtype=np.uint8)
    white_mask = cv2.inRange(hsv, lower_white, upper_white)

    # 2. Защита: зона таблички НЕ должна стать прозрачной!
    if protected_corners is not None and len(protected_corners) == 4:
        card_poly = np.array(protected_corners, dtype=np.int32)
        card_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(card_mask, [card_poly], 255)
        
        # Расширяем защиту таблички на 6 пикселей для надежности
        kernel_protect = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        card_shield = cv2.dilate(card_mask, kernel_protect)
        white_mask[card_shield == 255] = 0

    # 3. Внешняя угловая заливка
    flood_mask = np.zeros((h + 2, w + 2), np.uint8)
    diff = (10, 10, 10)
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if np.all(rgb[seed[1], seed[0]] >= 235):
            cv2.floodFill(
                rgb, flood_mask, seed, (0, 255, 0),
                diff, diff, flags=4 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY
            )

    outer_bg = (flood_mask[1:-1, 1:-1] == 255)
    
    # Объединяем внешний фон и внутренние белые щели
    total_bg = cv2.bitwise_or(white_mask, np.uint8(outer_bg * 255))
    
    # Инвертируем: получаем маску силуэта девочки и таблички
    fg_mask = cv2.bitwise_not(total_bg)

    # 4. МЯГКИЙ СУБПИКСЕЛЬНЫЙ КРАЙ (Anti-Aliased Feathering)
    smooth_alpha = cv2.GaussianBlur(fg_mask, (3, 3), 0.75)
    
    frame_bgra[:, :, 3] = smooth_alpha
    return frame_bgra

# --- РЕНДЕР КРУПНОГО ТЕКСТА С APPLE EMOJI ---
def render_text_plate(text: str, card_w=400, card_h=300):
    img = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    font_file = get_font_path()

    words = text.split()
    
    pad_x = 22
    pad_y = 18
    avail_w = card_w - (pad_x * 2)
    avail_h = card_h - (pad_y * 2)

    font_size = 160
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
            w_len = bbox[2] - bbox[0]
            
            if w_len <= avail_w:
                curr = test
            else:
                if curr:
                    lines.append(curr)
                w_single_len = draw.textbbox((0, 0), w, font=font)[2] - draw.textbbox((0, 0), w, font=font)[0]
                if w_single_len > avail_w:
                    fits = False
                    break
                curr = w

        if curr:
            lines.append(curr)

        if fits and lines:
            line_h = font_size * 1.05
            total_h = len(lines) * line_h
            if total_h <= avail_h:
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
                line_w = bbox[2] - bbox[0]
                x = pad_x + (avail_w - line_w) / 2
                y = start_y + (i * line_h)
                pilmoji.text((x, y), line, fill=text_color, font=best_font, stroke_width=1, stroke_fill=text_color)
    else:
        for i, line in enumerate(best_lines):
            bbox = draw.textbbox((0, 0), line, font=best_font)
            line_w = bbox[2] - bbox[0]
            x = pad_x + (avail_w - line_w) / 2
            y = start_y + (i * line_h)
            draw.text((x, y), line, fill=text_color, font=best_font, stroke_width=1, stroke_fill=text_color)

    return cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGRA)

# --- ГЕНЕРАТОР WEBM СТИКЕРА ---
async def generate_quote_sticker(text: str, template_num: int, output_file: str) -> bool:
    cfg = TEMPLATES.get(template_num, TEMPLATES[2])
    if not os.path.exists(cfg["file"]):
        LOGGER.error(f"Шаблон {cfg['file']} не найден!")
        return False

    cap = cv2.VideoCapture(cfg["file"])
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    
    card_w, card_h = 400, 300
    text_plate = render_text_plate(text, card_w=card_w, card_h=card_h)
    src_pts = np.float32([[0, 0], [card_w, 0], [card_w, card_h], [0, card_h]])

    ffmpeg_cmd = [
        'ffmpeg', '-hide_banner', '-y',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-s', '512x512',
        '-pix_fmt', 'bgra',
        '-r', str(fps),
        '-i', '-',
        '-c:v', 'libvpx-vp9',
        '-crf', '30',
        '-b:v', '250k',
        '-pix_fmt', 'yuva420p',
        '-an',
        '-fs', '250K',
        output_file
    ]

    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )

    start_t = cfg["start_time"]
    end_t = cfg["end_time"]
    is_static = cfg.get("is_static", False)

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        cur_t = frame_idx / fps

        if frame.shape[:2] != (512, 512):
            frame = cv2.resize(frame, (512, 512))
        if frame.shape[2] == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)

        # Вычисляем положение таблички для защиты от прозрачности
        if is_static:
            dst_pts = cfg["pose_1"]["corners"]
            fingers = cfg["pose_1"]["fingers"]
        else:
            p1 = cfg["pose_1"]
            p2 = cfg["pose_2"]
            if cur_t <= p1["time_sec"]:
                dst_pts = p1["corners"]
                fingers = p1["fingers"]
            elif cur_t >= p2["time_sec"]:
                dst_pts = p2["corners"]
                fingers = p2["fingers"]
            else:
                f = (cur_t - p1["time_sec"]) / (p2["time_sec"] - p1["time_sec"])
                dst_pts = (p1["corners"] + (p2["corners"] - p1["corners"]) * f).astype(np.float32)
                fingers = p2["fingers"] if f > 0.5 else p1["fingers"]

        # 1. Удаляем фон и щели между волосами с защитой таблички и мягким краем
        frame = make_background_transparent(frame, protected_corners=dst_pts)

        # 2. Накладываем 3D-текст
        if start_t <= cur_t <= end_t:
            M = cv2.getPerspectiveTransform(src_pts, dst_pts)
            warped = cv2.warpPerspective(text_plate, M, (512, 512), borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

            # 3. Вырез пальчиков поверх текста
            if len(fingers) >= 3:
                f_mask = np.zeros((512, 512), dtype=np.uint8)
                cv2.fillPoly(f_mask, [fingers], 255)
                warped[f_mask == 255] = [0, 0, 0, 0]

            alpha = warped[:, :, 3] / 255.0
            for c in range(3):
                frame[:, :, c] = (warped[:, :, c] * alpha + frame[:, :, c] * (1.0 - alpha)).astype(np.uint8)
            frame[:, :, 3] = np.maximum(frame[:, :, 3], warped[:, :, 3])

        try:
            proc.stdin.write(frame.tobytes())
            await proc.stdin.drain()
        except Exception:
            break

        frame_idx += 1

    cap.release()
    try:
        proc.stdin.close()
        await proc.wait()
    except Exception:
        pass

    return os.path.exists(output_file) and os.path.getsize(output_file) > 1000

# =========================================================================
# РЕГИСТРАЦИЯ ДЛЯ TELETHON
# =========================================================================
def register_quote_stickers(client, is_authorized_cb=None):
    init_db()

    async def check_access(event, consume_quota=False) -> tuple[bool, str]:
        sid = event.sender_id
        cid = event.chat_id
        if not sid:
            return False, "Неизвестный отправитель."

        if sid == OWNER_ID:
            return True, "unlimited"
        if is_authorized_cb and await is_authorized_cb(event):
            return True, "unlimited"

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
        if not match:
            return

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
                return await status.edit("❌ Ошибка сборки видеостикера. Проверь наличие `01.mp4` и `02.mp4` в `templates/`.")

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
