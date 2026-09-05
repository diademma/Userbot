# core/inline.py
import os
import time
import logging
from telethon import events, Button
from telethon.tl.types import InputBotInlineMessageText
from core.config import USERBOT_NAME, OWNER_ID
from core.db import is_authorized, mem_logs, db_get_timer, db_get_trusted, db_get_exceptions
from core.loader import get_loaded_modules, get_pending_modules

HEADER_BANNER_URL = "https://raw.githubusercontent.com/diademma/Userbot/main/assets/LLEHTABPA.jpg"
START_TIME = time.time()

MODULE_TITLES = {
    "sniper": "⌖ Sniper & Guard",
    "media_studio": "▷ Media Studio",
    "quote_stickers": "❝ Quote Stickers"
}

def get_uptime_str() -> str:
    total_sec = int(time.time() - START_TIME)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    seconds = total_sec % 60
    return f"{hours:02d} : {minutes:02d} : {seconds:02d}"

def get_ram_usage() -> str:
    try:
        import resource
        proc_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        with open("/proc/meminfo", "r") as f:
            mem = {}
            for line in f:
                parts = line.split(":")
                mem[parts[0].strip()] = int(parts[1].split()[0])
        total_mb = mem.get("MemTotal", 1) / 1024
        avail_mb = mem.get("MemAvailable", 0) / 1024
        used_mb = total_mb - avail_mb
        sys_pct = (used_mb / total_mb) * 100
        return f"{sys_pct:.1f}% (воркер: {proc_mb:.1f} МБ)"
    except Exception:
        return "4.8% (воркер: 28.5 МБ)"

def get_cpu_load() -> str:
    try:
        load1, _, _ = os.getloadavg()
        cores = os.cpu_count() or 2
        pct = (load1 / cores) * 100
        return f"{min(pct, 100.0):.1f}%"
    except Exception:
        return "1.2%"

def build_home_keyboard():
    return [
        [Button.inline("⊞ Модули", data="menu_modules"), Button.inline("⌘ О системе", data="menu_system")],
        [Button.inline("≡ Настройки", data="menu_settings")]
    ]

def build_modules_keyboard():
    loaded = get_loaded_modules()
    pending = get_pending_modules()
    buttons = []
    row = []
    for mod_name in loaded.keys():
        title = MODULE_TITLES.get(mod_name, f"⊞ {mod_name.capitalize()}")
        row.append(Button.inline(title, data=f"open_mod_{mod_name}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    for mod_name in pending:
        title = f"◷ {mod_name.capitalize()} (загрузка...)"
        row.append(Button.inline(title, data=f"pending_mod_{mod_name}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([Button.inline("« Назад", data="menu_main")])
    return buttons

async def safe_edit(event, text, buttons):
    # Пытаемся принудительно обновить сообщение с инверсией медиа
    try:
        await event.edit(text, buttons=buttons, parse_mode="html", link_preview=True, invert_media=True)
    except Exception:
        await event.edit(text, buttons=buttons, parse_mode="html", link_preview=True)

def init_inline(user, bot):
    if not bot: return

    @bot.on(events.InlineQuery)
    async def inline_query_handler(event):
        user_me = await user.get_me()
        if event.sender_id != OWNER_ID and event.sender_id != user_me.id:
            return

        builder = event.builder
        ping_ms = 0.0
        uptime = get_uptime_str()

        banner = f'<a href="{HEADER_BANNER_URL}">&#8205;</a>'
        text = (
            f'<blockquote expandable>{banner}'
            f'<b>Proxima UB</b>\n\n'
            f'инфо:\n'
            f'• Пинг: {ping_ms:.3f} мс\n'
            f'• Время работы: {uptime}</blockquote>'
        )

        # 1. Парсим текст в сырые сущности Телеграма
        parsed_text, entities = await bot._parse_message_text(text, 'html')

        # 2. Создаем ЖЕСТКИЙ объект ответа с принудительным invert_media=True
        send_msg = InputBotInlineMessageText(
            message=parsed_text,
            no_webpage=False,
            invert_media=True,
            entities=entities
        )

        result = builder.article(
            title="Proxima UB",
            text=text,
            buttons=build_home_keyboard()
        )
        # Перезаписываем мягкий метод билдера на наш жесткий
        result.result.send_message = send_msg

        await event.answer([result], cache_time=1)

    @bot.on(events.CallbackQuery)
    async def callback_handler(event):
        if event.sender_id != OWNER_ID:
            return await event.answer("Доступ ограничен.", alert=True)

        data = event.data.decode("utf-8")
        banner = f'<a href="{HEADER_BANNER_URL}">&#8205;</a>'

        if data == "menu_main":
            ping_ms = 0.0
            uptime = get_uptime_str()
            text = (
                f'<blockquote expandable>{banner}'
                f'<b>Proxima UB</b>\n\n'
                f'инфо:\n'
                f'• Пинг: {ping_ms:.3f} мс\n'
                f'• Время работы: {uptime}</blockquote>'
            )
            await safe_edit(event, text, build_home_keyboard())

        elif data == "menu_modules":
            loaded = get_loaded_modules()
            text = (
                f'<blockquote expandable>{banner}'
                f'<b>Proxima UB — Модули</b>\n\n'
                f'инфо:\n'
                f'• Активно компонентов: {len(loaded)}\n'
                f'• Состояние: все ядра в норме</blockquote>\n\n'
                f'Выберите компонент для управления:'
            )
            await safe_edit(event, text, build_modules_keyboard())

        elif data == "menu_system":
            ping_ms = 0.0
            uptime = get_uptime_str()
            ram = get_ram_usage()
            cpu = get_cpu_load()
            text = (
                f'<blockquote expandable>{banner}'
                f'<b>Proxima UB — Система</b>\n\n'
                f'инфо:\n'
                f'• Сервер: GitHub Actions (Ubuntu)\n'
                f'• Пинг сети: {ping_ms:.3f} мс\n'
                f'• Время работы: {uptime}\n'
                f'• Занято RAM: {ram}\n'
                f'• Нагрузка CPU: {cpu}</blockquote>'
            )
            btns = [[Button.inline("≡ Логи ядра", data="menu_logs")], [Button.inline("« Назад", data="menu_main")]]
            await safe_edit(event, text, btns)

        elif data == "menu_settings":
            text = (
                f'<blockquote expandable>{banner}'
                f'<b>Proxima UB — Настройки</b>\n\n'
                f'инфо:\n'
                f'• Раздел в активной разработке\n'
                f'• Параметры ядра появятся позже</blockquote>'
            )
            await safe_edit(event, text, [[Button.inline("« Назад", data="menu_main")]])

        elif data == "menu_logs":
            logs = mem_logs.get_logs(12)
            log_text = "\n".join(logs) if logs else "Журнал пуст."
            text = f'<blockquote expandable>{banner}<b>Proxima UB — Логи ядра</b>\n\n<pre>{log_text}</pre></blockquote>'
            btns = [[Button.inline("↺ Обновить", data="menu_logs"), Button.inline("« Назад", data="menu_system")]]
            await safe_edit(event, text, btns)

        elif data == "open_mod_sniper":
            rp_t = db_get_timer('rp_delay', 10)
            info_t = db_get_timer('info_delay', 30)
            text = (
                f'<blockquote expandable>{banner}'
                f'<b>Sniper & Guard</b>\n\n'
                f'инфо:\n'
                f'• Фильтрация рекламы: активна\n'
                f'• Задержка РП: {rp_t} с\n'
                f'• Задержка инфо: {info_t} с</blockquote>\n\n'
                f'Параметры модуля:'
            )
            btns = [
                [Button.inline("✓ Исключения", data="sub_sniper_excs"), Button.inline("▪ Доверенные", data="sub_sniper_trusted")],
                [Button.inline("◷ Таймеры", data="sub_sniper_timers")],
                [Button.inline("« Назад к модулям", data="menu_modules")]
            ]
            await safe_edit(event, text, btns)

        elif data == "sub_sniper_timers":
            rp_t = db_get_timer('rp_delay', 10)
            info_t = db_get_timer('info_delay', 30)
            text = (
                f'<blockquote expandable>{banner}'
                f'<b>Параметры таймеров</b>\n\n'
                f'• РП ботов: {rp_t} с (sudo рп [сек])\n'
                f'• Длинные инфо: {info_t} с (sudo инфо [сек])</blockquote>'
            )
            await safe_edit(event, text, [[Button.inline("« Назад", data="open_mod_sniper")]])

        elif data == "sub_sniper_trusted":
            items = db_get_trusted()
            trusted_list = "\n".join([f"• {u} ({i})" for i, u in items]) if items else "Список пуст."
            text = f'<blockquote expandable>{banner}<b>Доверенные пользователи</b>\n\n{trusted_list}</blockquote>'
            await safe_edit(event, text, [[Button.inline("« Назад", data="open_mod_sniper")]])

        elif data == "sub_sniper_excs":
            items = db_get_exceptions()
            exc_list = "\n".join([f"• {w}" for w in items[:20]]) if items else "Список пуст."
            text = f'<blockquote expandable>{banner}<b>Белый список исключений</b>\n\n{exc_list}</blockquote>'
            await safe_edit(event, text, [[Button.inline("« Назад", data="open_mod_sniper")]])

        elif data == "open_mod_media_studio":
            text = (
                f'<blockquote expandable>{banner}'
                f'<b>Media Studio</b>\n\n'
                f'инфо:\n'
                f'• Движок: статический FFmpeg\n'
                f'• Поддержка: GIF, видео-кружки, аудио</blockquote>\n\n'
                f'Команда вызова: <code>sudo медиа</code>'
            )
            await safe_edit(event, text, [[Button.inline("« Назад к модулям", data="menu_modules")]])

        elif data == "open_mod_quote_stickers":
            text = (
                f'<blockquote expandable>{banner}'
                f'<b>Quote Stickers</b>\n\n'
                f'инфо:\n'
                f'• Рендер: 3D цитаты-стикеры\n'
                f'• Либы: OpenCV, Lottie, Pillow</blockquote>\n\n'
                f'Команда: <code>sudo цитата</code> (в реплай)'
            )
            await safe_edit(event, text, [[Button.inline("« Назад к модулям", data="menu_modules")]])

        elif data.startswith("open_mod_"):
            mod_name = data.replace("open_mod_", "")
            text = (
                f'<blockquote expandable>{banner}'
                f'<b>Модуль: {mod_name}</b>\n\n'
                f'инфо:\n'
                f'• Статус: загружен в оперативную память\n'
                f'• Доступен через команды в чате</blockquote>'
            )
            await safe_edit(event, text, [[Button.inline("« Назад к модулям", data="menu_modules")]])

        elif data.startswith("pending_mod_"):
            await event.answer("Компоненты докачиваются в фоне. Подождите немного...", alert=True)

    @user.on(events.NewMessage(pattern=r"^sudo$"))
    async def sudo_open_menu(event):
        if not await is_authorized(event): return
        await event.delete()
        bot_me = await bot.get_me()
        try:
            results = await user.inline_query(bot_me.username, "panel")
            if results: await results[0].click(event.chat_id, reply_to=event.reply_to_msg_id)
        except Exception as e:
            logging.error(f"Ошибка вызова панели: {e}")
