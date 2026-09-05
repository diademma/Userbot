# modules/upscaler.py — Облачный нейро-апскейлер v1.0 (Hugging Face ZeroGPU)
import os
import asyncio
import logging
import tempfile
from pathlib import Path
from telethon import events
from PIL import Image

from core.config import OWNER_ID
from core.db import is_authorized

# --- ОБЯЗАТЕЛЬНЫЕ МЕТАДАННЫЕ API ДЛЯ ЯДРА ---
TITLE = "⌕ AI Upscaler"
BANNER = "https://raw.githubusercontent.com/diademma/Userbot/main/assets/LLEHTABPA.jpg"
COMMANDS = (
    "• sudo upscale [2|4] — Апскейл фото на GPU Hugging Face\n"
    "• sudo upscale doc — Отправить результат несжатым файлом\n"
    "• sudo upscale 4x doc — 4x увеличение без сжатия Telegram\n"
    "• sudo upscale noface — Апскейл без изменения черт лица\n\n"
    "⚙️ ВОЗМОЖНОСТИ НЕЙРОСЕТИ:\n"
    "• Движок: CodeFormer + Real-ESRGAN (Nvidia ZeroGPU)\n"
    "• Увеличение резкости, удаление артефактов сжатия\n"
    "• Детальная реконструкция размытых лиц (глаза, кожа)"
)

LOGGER = logging.getLogger("Upscaler")

def sync_upscale_hf(input_path: str, scale: int = 2, face_enhance: bool = True) -> str:
    """Синхронный вызов облачного спейса Hugging Face через gradio_client"""
    from gradio_client import Client, handle_file

    # Подключаемся к популярному и стабильному спейсу CodeFormer / Real-ESRGAN
    client = Client("sczhou/CodeFormer")
    
    # Вызываем инференс на удаленном GPU
    result = client.predict(
        handle_file(input_path),  # Входное фото
        True,                     # background_enhance (Real-ESRGAN для фона)
        face_enhance,             # face_upsample (восстановление лиц)
        scale,                    # масштаб увеличения (2 или 4)
        0.6,                      # fidelity (баланс резкости и исходной формы)
        fn_index=0
    )

    if isinstance(result, (list, tuple)):
        return result[0]
    return str(result)

def register(client, bot=None):
    # Поддерживаем вызов как через 'sudo upscale', так и через точку '.upscale'
    CMD_PATTERN = r"^(?:sudo\s+)?(?:\.|\/)?(?:upscale|апскейл)(?:\s+(.*))?$"

    @client.on(events.NewMessage(pattern=CMD_PATTERN))
    async def upscale_handler(event):
        if not await is_authorized(event):
            return

        reply_msg = await event.get_reply_message()
        if not reply_msg or not (reply_msg.photo or (reply_msg.document and "image" in (reply_msg.document.mime_type or ""))):
            return await event.reply("⚠️ Ответьте командой `sudo upscale` **на фото или картинку**!")

        args = (event.pattern_match.group(1) or "").lower().split()
        
        # Разбор параметров
        scale = 4 if any(x in args for x in ("4", "4x", "4х")) else 2
        force_document = any(x in args for x in ("doc", "док", "файл", "file"))
        face_enhance = not any(x in args for x in ("noface", "nofaces", "безлиц", "аниме", "anime"))

        status = await event.reply(
            f"⚡ `Подключаюсь к GPU Hugging Face ({scale}x)...`"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            in_file = await reply_msg.download_media(file=tmp_path / "input.png")
            if not in_file:
                return await status.edit("❌ Не удалось скачать исходное изображение.")

            try:
                # Читаем исходные размеры
                with Image.open(in_file) as orig_img:
                    orig_w, orig_h = orig_img.size

                await status.edit(
                    f"🎨 `Нейросеть обрабатывает фото...`\n"
                    f"• Исходный размер: `{orig_w}x{orig_h}`\n"
                    f"• Множитель: `{scale}x` {'(с лицами)' if face_enhance else ''}"
                )

                # Выполняем тяжелый запрос в отдельном потоке, чтобы юзербот не висел
                out_path = await asyncio.to_thread(
                    sync_upscale_hf,
                    str(in_file),
                    scale=scale,
                    face_enhance=face_enhance
                )

                if not out_path or not os.path.exists(out_path):
                    return await status.edit("❌ Сервер Hugging Face вернул пустой результат. Попробуйте еще раз.")

                # Читаем полученный размер
                with Image.open(out_path) as res_img:
                    new_w, new_h = res_img.size

                caption = (
                    f"✨ **AI Upscale завершен!**\n"
                    f"• Разрешение: `{orig_w}x{orig_h}` ➔ `{new_w}x{new_h}`\n"
                    f"• Увеличение: **{scale}x** (CodeFormer + Real-ESRGAN)"
                )

                await event.client.send_file(
                    event.chat_id,
                    file=out_path,
                    caption=caption,
                    reply_to=reply_msg.id,
                    force_document=force_document
                )
                await status.delete()

            except Exception as e:
                LOGGER.error(f"Upscale error: {e}")
                err_text = str(e)
                if "loading" in err_text.lower() or "queue" in err_text.lower():
                    await status.edit("⏳ Видеокарта на сервере просыпается. Повторите попытку через 20 секунд.")
                else:
                    await status.edit(f"⚠️ Ошибка Hugging Face: `{err_text[:120]}`")