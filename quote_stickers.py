# quote_stickers.py — Высокоточный генератор 3D-видеостикеров v2.2
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

# Надежная прямая ссылка на рукописный кириллический шрифт
FONT_URL = "https://raw.githubusercontent.com/anton-liubushkin/cyrillic-google-fonts/master/fonts/MarckScript-Regular.ttf"

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
        # Сидящая девочка разворачивает рисунок (с 0.530с)
        "file": "templates/02.mp4",
        "start_time": 0.530,
        "end_time": 99.0,
        "is_static": False,
        "pose_1": {
            "time_sec": 0.530,
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
        try:
            LOGGER.info("Скачиваю рукописный шрифт...")
            req = urllib.request.Request(FONT_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp, open(font_path, "wb") as f:
                f.write(resp.read())
        except Exception as e:
            LOGGER.warning(f"Ошибка загрузки шрифта: {e}")
            # Резерв на системный шрифт Linux
            sys_font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
            if sys_font.exists():
                return str(sys_font)
                
    return str(font_path)

# --- УДАЛЕНИЕ БЕЛОГО ФОНА ВОКРУГ ДЕВОЧКИ ---
def make_background_transparent(frame_bgra):
    h, w = frame_bgra.shape[:2]
    rgb = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)
    flood_mask = np.zeros((h + 2, w + 2), np.uint8)
    diff = (6, 6, 6)

    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if np.all(rgb[seed[1], seed[0]] >= 242):
            cv2.floodFill(
                rgb, flood_mask, seed, (0, 255, 0),
                diff, diff, flags=4 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY
            )

    outer_bg = (flood_mask[1:-1, 1:-1] == 255)
    frame_bgra[outer_bg, 3] = 0
    return frame_bgra

# --- РЕНДЕР КРУПНОГО ТЕКСТА ---
def render_text_plate(text: str, card_w=400, card_h=300):
    img = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    font_file = get_font_path()

    words = text.split()
    font_size = 140
    lines = []

    while font_size > 28:
        try:
            font = ImageFont.truetype(font_file, font_size)
        except Exception:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

        lines = []
        curr = ""
        for w in words:
            test = f"{curr} {w}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] < card_w - 30:
                curr = test
            else:
                if curr: lines.append(curr)
                curr = w
        if curr: lines.append(curr)

        total_h = len(lines) * (font_size * 1.05)
        if total_h < card_h - 30:
            break
        font_size -= 4

    y = (card_h - (len(lines) * font_size * 1.05)) / 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x = (card_w - line_w) / 2
        draw.text((x, y), line, fill=(200, 30, 55, 255), font=font)
        y += font_size * 1.05

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

        # 1. Удаляем внешний белый фон
        frame = make_background_transparent(frame)

        # 2. Накладываем 3D-текст
        if start_t <= cur_t <= end_t:
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

            # 3D-гомография
            M = cv2.getPerspectiveTransform(src_pts, dst_pts)
            warped = cv2.warpPerspective(text_plate, M, (512, 512), borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

            # Вырез пальчиков поверх текста
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

    @client.on(events.NewMessage(func=lambda e: (e.raw_text or "").strip().lower().startswith(("sudo цитата", "sudo цит", ".цитата", ".цит"))))
    async def quote_cmd_handler(event):
        has_access, quota_msg = await check_access(event, consume_quota=True)
        if not has_access:
            return await event.reply(quota_msg)

        raw = event.raw_text.strip()
        parts = raw.split(maxsplit=2)
        
        chosen_template = None
        text_arg = ""

        if len(parts) > 2 and parts[1] in ("1", "2"):
            chosen_template = int(parts[1])
            text_arg = parts[2].strip()
        elif len(parts) > 1:
            if parts[1] in ("1", "2"):
                chosen_template = int(parts[1])
            else:
                text_arg = raw.split(maxsplit=1)[1].strip()

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
