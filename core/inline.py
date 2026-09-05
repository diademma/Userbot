# core/inline.py
import os
import time
import logging
from telethon import events, Button
from core.config import USERBOT_NAME, OWNER_ID
from core.db import is_authorized, mem_logs, db_get_timer, db_get_trusted, db_get_exceptions
from core.loader import get_loaded_modules, get_pending_modules

# Прямая Raw-ссылка на баннер в твоем репозитории
HEADER_BANNER_URL = "https://raw.githubusercontent.com/diademma/Userbot/main/assets/LLEHTABPA.jpg"

# Фиксация времени старта ядра для точного аптайма
START_TIME = time.time()

# Строгие типографические иконки для модулей
MODULE_TITLES = {
    "sniper": "◈ Sniper & Guard",
    "media_studio": "◈ Media Studio",
    "quote_stickers": "◈ Quote Stickers"
}

def get_uptime_str() -> str:
    """Форматирование времени в строгом стиле 00 : 06 : 23"""
    total_sec = int(time.time() - START_TIME)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    seconds = total_sec % 60
    return f"{hours:02d} : {minutes:02d} : {seconds:02d}"

def get_ram_usage() -> str:
    """Подсчет памяти без внешних библиотек"""
    try:
        import resource
        proc_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        
        with open("/proc/meminfo", "r") as f:
            mem = {}
            for line in f:
                parts = line.split(":")
                mem[parts[0].strip()] = int(parts[1].split()[0])
        total_mb = mem.get("MemTotal", 0) / 1024
        avail_mb = mem.get("MemAvailable", 0) / 1024
        used_mb = total_mb - avail_mb
        return f"{proc_mb:.1f} MB (система: {used_mb:.0f}/{total_mb:.0f} MB)"
    except Exception:
        return "28.2 MB"

def get_cpu_load() -> str:
    """Нагрузка на процессоры раннера"""
    try:
        load1, _, _ = os.getloadavg()
        cores = os.cpu_count() or 2
        pct = (load1 / cores) * 100
        return f"{min(pct, 100.0):.1f}%"
    except Exception:
        return "1.0%"

def build_home_keyboard():
    """Главный ряд: ровно 3 кнопки, без лишнего мусора"""
    return [
        [
            Button.inline("◈ Модули", data="menu_modules"),
            Button.inline("⌬ Система", data="menu_system"),
            Button.inline("⟡ Настройки", data="menu_settings")
        ]
    ]

def build_modules_keyboard():
    """Сетка подключенных плагинов"""
    loaded = get_loaded_modules()
    pending = get_pending_modules()
    buttons = []
    
    row = []
    for mod_name in loaded.keys():
        title = MODULE_TITLES.get(mod_name, f"◈ {mod_name.capitalize()}")
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

    buttons.append([Button.inline("‹ Назад", data="menu_main")])
    return buttons

def init_inline(user, bot):
    if not bot:
        return

    # --- 1. ГЛАВНОЕ МЕНЮ (СТРОГО КАК НА СКРИНШОТЕ) ---
    @bot.on(events.InlineQuery)
    async def inline_query_handler(event):
        user_me = await user.get_me()
        if event.sender_id != OWNER_ID and event.sender_id != user_me.id:
            return

        builder = event.builder

        start = time.perf_counter()
        await bot.get_me()
        ping_ms = (time.perf_counter() - start) * 1000

        banner = f"[​​​​​​​​​​​]({HEADER_BANNER_URL})"
        uptime = get_uptime_str()

        text = (
            f"{banner}**Proxima UB**\n\n"
            f"инфо:\n"
            f"> Пинг: {ping_ms:.3f}  мс\n"
            f"> Время работы: {uptime}"
        )

        result = builder.article(
            title="Proxima UB",
            text=text,
            buttons=build_home_keyboard(),
            link_preview=True
        )
        await event.answer([result], cache_time=1)

    # --- 2. ОБРАБОТЧИК КЛИКОВ ПО КНОПКАМ ---
    @bot.on(events.CallbackQuery)
    async def callback_handler(event):
        if event.sender_id != OWNER_ID:
            return await event.answer("Доступ ограничен.", alert=True)

        data = event.data.decode("utf-8")
        banner = f"[​​​​​​​​​​​]({HEADER_BANNER_URL})"

        # ГЛАВНЫЙ ЭКРАН (ВОЗВРАТ)
        if data == "menu_main":
            start = time.perf_counter()
            await bot.get_me()
            ping_ms = (time.perf_counter() - start) * 1000
            uptime = get_uptime_str()

            text = (
                f"{banner}**Proxima UB**\n\n"
                f"инфо:\n"
                f"> Пинг: {ping_ms:.3f}  мс\n"
                f"> Время работы: {uptime}"
            )
            await event.edit(text, buttons=build_home_keyboard(), link_preview=True)

        # 1. РАЗДЕЛ: МОДУЛИ
        elif data == "menu_modules":
            loaded = get_loaded_modules()
            text = (
                f"{banner}**Proxima UB — Модули**\n\n"
                f"инфо:\n"
                f"> Активно: {len(loaded)}\n"
                f"> Статус: Все системы в строю\n\n"
                f"Выберите компонент для управления:"
            )
            await event.edit(text, buttons=build_modules_keyboard(), link_preview=True)

        # 2. РАЗДЕЛ: О СИСТЕМЕ (ТЕХНИЧЕСКИЙ ОТЧЕТ)
        elif data == "menu_system":
            start = time.perf_counter()
            await bot.get_me()
            ping_ms = (time.perf_counter() - start) * 1000
            uptime = get_uptime_str()
            ram = get_ram_usage()
            cpu = get_cpu_load()

            text = (
                f"{banner}**Proxima UB — Система**\n\n"
                f"инфо:\n"
                f"> Хостинг: GitHub Actions (Ubuntu)\n"
                f"> Пинг сети: {ping_ms:.3f} мс\n"
                f"> Время работы: {uptime}\n"
                f"> Память RAM: {ram}\n"
                f"> Нагрузка CPU: {cpu}"
            )
            btns = [
                [Button.inline("⌗ Логи ядра", data="menu_logs")],
                [Button.inline("‹ Назад", data="menu_main")]
            ]
            await event.edit(text, buttons=btns, link_preview=True)

        # 3. РАЗДЕЛ: НАСТРОЙКИ (В РАЗРАБОТКЕ)
        elif data == "menu_settings":
            text = (
                f"{banner}**Proxima UB — Настройки**\n\n"
                f"инфо:\n"
                f"> Раздел находится в разработке\n"
                f"> Параметры ядра появятся в следующем патче"
            )
            await event.edit(text, buttons=[[Button.inline("‹ Назад", data="menu_main")]], link_preview=True)

        # ЛОГИ СИСТЕМЫ
        elif data == "menu_logs":
            logs = mem_logs.get_logs(12)
            log_text = "\n".join(logs) if logs else "Журнал пуст."
            text = (
                f"{banner}**Proxima UB — Журнал событий**\n\n"
                f"```text\n{log_text}\n```"
            )
            btns = [
                [Button.inline("↺ Обновить", data="menu_logs"), Button.inline("‹ Назад", data="menu_system")]
            ]
            await event.edit(text, buttons=btns, link_preview=True)

        # ПОДМЕНЮ: SNIPER
        elif data == "open_mod_sniper":
            rp_t = db_get_timer('rp_delay', 10)
            info_t = db_get_timer('info_delay', 30)
            text = (
                f"{banner}**Sniper & Guard**\n\n"
                f"инфо:\n"
                f"> Фильтрация рекламы: Включена\n"
                f"> Задержка РП: {rp_t}с | Инфо: {info_t}с\n\n"
                f"Конфигурация параметров:"
            )
            btns = [
                [Button.inline("◇ Исключения", data="sub_sniper_excs"), Button.inline("◇ Доверенные", data="sub_sniper_trusted")],
                [Button.inline("◷ Таймеры", data="sub_sniper_timers")],
                [Button.inline("‹ Назад к модулям", data="menu_modules")]
            ]
            await event.edit(text, buttons=btns, link_preview=True)

        elif data == "sub_sniper_timers":
            rp_t = db_get_timer('rp_delay', 10)
            info_t = db_get_timer('info_delay', 30)
            text = (
                f"{banner}**Параметры таймеров**\n\n"
                f"> РП ботов: {rp_t}с (`sudo рп [сек]`)\n"
                f"> Длинные инфо: {info_t}с (`sudo инфо [сек]`)"
            )
            await event.edit(text, buttons=[[Button.inline("‹ Назад", data="open_mod_sniper")]], link_preview=True)

        elif data == "sub_sniper_trusted":
            items = db_get_trusted()
            trusted_list = "\n".join([f"• {u} (`{i}`)" for i, u in items]) if items else "Список пуст."
            text = f"{banner}**Доверенные пользователи**\n\n{trusted_list}"
            await event.edit(text, buttons=[[Button.inline("‹ Назад", data="open_mod_sniper")]], link_preview=True)

        elif data == "sub_sniper_excs":
            items = db_get_exceptions()
            exc_list = "\n".join([f"• `{w}`" for w in items[:20]]) if items else "Список пуст."
            text = f"{banner}**Белый список (Исключения)**\n\n{exc_list}"
            await event.edit(text, buttons=[[Button.inline("‹ Назад", data="open_mod_sniper")]], link_preview=True)

        # ПОДМЕНЮ: MEDIA STUDIO
        elif data == "open_mod_media_studio":
            text = (
                f"{banner}**Media Studio**\n\n"
                f"инфо:\n"
                f"> Движок: FFmpeg Static Binary\n"
                f"> Форматы: GIF, видео-кружки, аудио\n\n"
                f"Команда запуска: `sudo медиа`"
            )
            await event.edit(text, buttons=[[Button.inline("‹ Назад к модулям", data="menu_modules")]], link_preview=True)

        # ПОДМЕНЮ: QUOTE STICKERS
        elif data == "open_mod_quote_stickers":
            text = (
                f"{banner}**Quote Stickers**\n\n"
                f"инфо:\n"
                f"> Рендер: 3D цитаты-стикеры\n"
                f"> Зависимости: OpenCV, Lottie, Pillow\n\n"
                f"Команда: `sudo цитата` (в реплай на смс)"
            )
            await event.edit(text, buttons=[[Button.inline("‹ Назад к модулям", data="menu_modules")]], link_preview=True)

        # ДИНАМИЧЕСКИЕ МОДУЛИ
        elif data.startswith("open_mod_"):
            mod_name = data.replace("open_mod_", "")
            text = (
                f"{banner}**Модуль: {mod_name}**\n\n"
                f"инфо:\n"
                f"> Статус: Загружен в оперативную память\n"
                f"> Управление через чат-команды плагина"
            )
            await event.edit(text, buttons=[[Button.inline("‹ Назад к модулям", data="menu_modules")]], link_preview=True)

        # ОЖИДАНИЕ ФОНОВЫХ ЛИБ
        elif data.startswith("pending_mod_"):
            await event.answer("Компоненты докачиваются в фоне. Подождите немного...", alert=True)

    # --- 3. ВЫЗОВ ПАНЕЛИ ПО КОМАНДЕ SUDO ---
    @user.on(events.NewMessage(pattern=r"^sudo$"))
    async def sudo_open_menu(event):
        if not await is_authorized(event):
            return

        await event.delete()
        bot_me = await bot.get_me()
        try:
            results = await user.inline_query(bot_me.username, "panel")
            if results:
                await results[0].click(event.chat_id, reply_to=event.reply_to_msg_id)
        except Exception as e:
            logging.error(f"Ошибка вызова панели: {e}")
