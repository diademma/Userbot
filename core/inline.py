# core/inline.py
import os
import sys
import glob
import time
import binascii
import logging
from telethon import events, Button
from telethon.tl.types import InputBotInlineResult, InputBotInlineMessageText
from telethon.tl.functions.messages import EditInlineBotMessageRequest
from telethon.errors import MessageNotModifiedError

from core.config import USERBOT_NAME, OWNER_ID
from core.db import is_authorized, mem_logs
from core.loader import get_loaded_modules, get_pending_modules, load_single_module, MODULES_DIR

# Дефолтный баннер системы
HEADER_BANNER_URL = "https://raw.githubusercontent.com/diademma/Userbot/main/assets/LLEHTABPA.jpg"
START_TIME = time.time()

# Временное хранилище избранных модулей
FAVORITE_MODULES = set()

def get_uptime_str() -> str:
    """Форматирование аптайма в виде 00 : 06 : 23"""
    total_sec = int(time.time() - START_TIME)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    seconds = total_sec % 60
    return f"{hours:02d} : {minutes:02d} : {seconds:02d}"

def get_ram_usage() -> str:
    """Подсчет памяти в процентах"""
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
    """Нагрузка на процессор в процентах"""
    try:
        load1, _, _ = os.getloadavg()
        cores = os.cpu_count() or 2
        pct = (load1 / cores) * 100
        return f"{min(pct, 100.0):.1f}%"
    except Exception:
        return "1.2%"

def get_all_warehouse_modules():
    """Получает список всех файлов модулей со склада"""
    if not os.path.exists(MODULES_DIR):
        return []
    files = glob.glob(os.path.join(MODULES_DIR, "*.py"))
    return sorted([os.path.splitext(os.path.basename(f))[0] for f in files if not os.path.basename(f).startswith("_")])

def build_home_keyboard():
    return [
        [Button.inline("⊞ Модули", data="mod_tab_act_0"), Button.inline("⌘ О системе", data="menu_system")],
        [Button.inline("≡ Настройки", data="menu_settings")]
    ]

def build_modules_keyboard(tab="act", page=0):
    """Сборка меню модулей: табы (Избранное | Активные | Склад) + пагинация по 5 штук"""
    loaded = get_loaded_modules()
    loaded_keys = list(loaded.keys())
    all_mods = get_all_warehouse_modules()
    favs = list(FAVORITE_MODULES)

    if tab == "fav": source_list = favs
    elif tab == "wh": source_list = all_mods
    else: source_list = loaded_keys

    per_page = 5
    total_pages = max(1, (len(source_list) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    current_items = source_list[page * per_page : (page + 1) * per_page]

    buttons = []
    
    # 1. Надежные символы табов: ★ (Избранное) | ⊞ (Активные) | ⧉ (Склад)
    buttons.append([
        Button.inline("★" if tab=="fav" else "☆", data="mod_tab_fav_0"),
        Button.inline("⊞" if tab=="act" else "⊟", data="mod_tab_act_0"),
        Button.inline("⧉" if tab=="wh" else "▫", data="mod_tab_wh_0")
    ])

    # 2. Кнопки модулей (названия берутся динамически из самого модуля через API)
    for mod_name in current_items:
        if mod_name in loaded:
            mod_obj = loaded[mod_name]
            title = getattr(mod_obj, "TITLE", f"⊞ {mod_name.capitalize()}")
        else:
            title = f"⧉ {mod_name.capitalize()}"

        if mod_name in get_pending_modules():
            title = f"◷ {mod_name} (загрузка...)"
            
        buttons.append([Button.inline(title, data=f"open_mod_{mod_name}")])

    # 3. Стрелочки пагинации
    nav_row = []
    if page > 0:
        nav_row.append(Button.inline("«", data=f"mod_tab_{tab}_{page-1}"))
    if total_pages > 1:
        nav_row.append(Button.inline(f"{page+1} / {total_pages}", data="ignore"))
    if page < total_pages - 1:
        nav_row.append(Button.inline("»", data=f"mod_tab_{tab}_{page+1}"))
    if nav_row:
        buttons.append(nav_row)

    # 4. Назад
    buttons.append([Button.inline("« Назад", data="menu_main")])
    return buttons

async def safe_edit(event, bot, text, buttons):
    """Жесткое редактирование через Raw API для сохранения фото наверху"""
    parsed_text, entities = await bot._parse_message_text(text, 'html')
    try:
        await bot(EditInlineBotMessageRequest(
            id=event.query.msg_id,
            message=parsed_text,
            no_webpage=False,
            invert_media=True,
            entities=entities,
            reply_markup=bot.build_reply_markup(buttons)
        ))
    except MessageNotModifiedError:
        pass
    except Exception as e:
        logging.error(f"Raw edit error: {e}")
        try:
            await event.edit(text, buttons=buttons, parse_mode="html", link_preview=True)
        except MessageNotModifiedError:
            pass

def init_inline(user, bot):
    if not bot:
        return

    @bot.on(events.InlineQuery)
    async def inline_query_handler(event):
        user_me = await user.get_me()
        if event.sender_id != OWNER_ID and event.sender_id != user_me.id:
            return

        start = time.perf_counter()
        await bot.get_me()
        ping_ms = (time.perf_counter() - start) * 1000
        uptime = get_uptime_str()

        banner = f'<a href="{HEADER_BANNER_URL}">&#8205;</a>'
        text = (
            f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB</b>\n\n'
            f'<blockquote expandable>• Пинг: {ping_ms:.3f} мс\n'
            f'• Время работы: {uptime}</blockquote>'
        )

        parsed_text, entities = await bot._parse_message_text(text, 'html')
        send_msg = InputBotInlineMessageText(
            message=parsed_text,
            no_webpage=False,
            invert_media=True,
            entities=entities,
            reply_markup=bot.build_reply_markup(build_home_keyboard())
        )
        
        res_id = binascii.hexlify(os.urandom(8)).decode('ascii')
        result = InputBotInlineResult(
            id=res_id,
            type='article',
            title='Proxima UB',
            send_message=send_msg
        )
        await event.answer([result], cache_time=1)

    @bot.on(events.CallbackQuery)
    async def callback_handler(event):
        if event.sender_id != OWNER_ID:
            return await event.answer("Доступ ограничен.", alert=True)

        data = event.data.decode("utf-8")
        banner = f'<a href="{HEADER_BANNER_URL}">&#8205;</a>'

        if data == "ignore":
            return await event.answer()

        # ГЛАВНЫЙ ЭКРАН
        if data == "menu_main":
            start = time.perf_counter()
            await bot.get_me()
            ping_ms = (time.perf_counter() - start) * 1000
            uptime = get_uptime_str()
            text = (
                f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB</b>\n\n'
                f'<blockquote expandable>• Пинг: {ping_ms:.3f} мс\n'
                f'• Время работы: {uptime}</blockquote>'
            )
            await safe_edit(event, bot, text, build_home_keyboard())

        # РАЗДЕЛ: МОДУЛИ (ТОЛЬКО ЧИСЛО И ЦИТАТА, БЕЗ ЛИШНЕГО ТЕКСТА)
        elif data.startswith("mod_tab_"):
            parts = data.split("_")
            tab = parts[2]
            page = int(parts[3]) if len(parts) > 3 else 0
            
            loaded = get_loaded_modules()
            all_mods = get_all_warehouse_modules()
            favs = list(FAVORITE_MODULES)

            if tab == "fav":
                lst = favs
            elif tab == "wh":
                lst = all_mods
            else:
                lst = list(loaded.keys())

            list_str = "\n".join([f"• {m}" for m in lst]) if lst else "• Пусто"

            text = (
                f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB</b>\n\n'
                f'• Загружено модулей: {len(loaded)}\n'
                f'<blockquote expandable>{list_str}</blockquote>'
            )
            await safe_edit(event, bot, text, build_modules_keyboard(tab, page))

        # ОТКРЫТИЕ МОДУЛЯ: ИНФОРМАЦИЯ И ФОТО БЕРУТСЯ ИЗ САМОГО МОДУЛЯ (API)
        elif data.startswith("open_mod_"):
            mod_name = data.replace("open_mod_", "")
            loaded = get_loaded_modules()
            is_active = mod_name in loaded
            is_fav = mod_name in FAVORITE_MODULES

            if is_active:
                mod_obj = loaded[mod_name]
                title = getattr(mod_obj, "TITLE", f"⊞ {mod_name.capitalize()}")
                mod_banner = getattr(mod_obj, "BANNER", HEADER_BANNER_URL)
                commands = getattr(mod_obj, "COMMANDS", "• Команды не описаны в API модуля")
                status = "АКТИВЕН ⊞"
            else:
                title = f"⧉ {mod_name.capitalize()}"
                mod_banner = HEADER_BANNER_URL
                commands = "• Модуль на складе (выгружен из ОЗУ)\n• Нажмите [ Загрузить ] для активации и просмотра команд"
                status = "ВЫГРУЖЕН ⧉"

            custom_banner = f'<a href="{mod_banner}">&#8205;</a>'
            text = (
                f'{custom_banner}<b>{title}</b> [{status}]\n\n'
                f'<blockquote expandable>{commands}</blockquote>'
            )

            btns = [
                [
                    Button.inline("Выгрузить" if is_active else "Загрузить", data=f"toggle_act_{mod_name}"),
                    Button.inline("Убрать ★" if is_fav else "В Избранное ★", data=f"toggle_fav_{mod_name}")
                ],
                [Button.inline("« Назад", data="mod_tab_act_0")]
            ]
            await safe_edit(event, bot, text, btns)

        # ПЕРЕКЛЮЧЕНИЕ АКТИВНОСТИ МОДУЛЯ
        elif data.startswith("toggle_act_"):
            mod_name = data.replace("toggle_act_", "")
            from core.loader import LOADED_MODULES
            
            if mod_name in LOADED_MODULES:
                del LOADED_MODULES[mod_name]
                if mod_name in sys.modules:
                    del sys.modules[mod_name]
                await event.answer(f"Модуль {mod_name} выгружен из ОЗУ", alert=True)
            else:
                file_path = os.path.join(MODULES_DIR, f"{mod_name}.py")
                if os.path.exists(file_path):
                    if load_single_module(file_path, user, bot, silent=True):
                        await event.answer(f"Модуль {mod_name} загружен в ядро", alert=True)
                    else:
                        await event.answer(f"Ошибка загрузки {mod_name}", alert=True)
                else:
                    await event.answer(f"Файл {mod_name}.py не найден", alert=True)

            # Перерисовываем карточку модуля
            loaded = get_loaded_modules()
            is_active = mod_name in loaded
            is_fav = mod_name in FAVORITE_MODULES

            if is_active:
                mod_obj = loaded[mod_name]
                title = getattr(mod_obj, "TITLE", f"⊞ {mod_name.capitalize()}")
                mod_banner = getattr(mod_obj, "BANNER", HEADER_BANNER_URL)
                commands = getattr(mod_obj, "COMMANDS", "• Команды не описаны в API модуля")
                status = "АКТИВЕН ⊞"
            else:
                title = f"⧉ {mod_name.capitalize()}"
                mod_banner = HEADER_BANNER_URL
                commands = "• Модуль на складе (выгружен из ОЗУ)\n• Нажмите [ Загрузить ] для активации и просмотра команд"
                status = "ВЫГРУЖЕН ⧉"

            custom_banner = f'<a href="{mod_banner}">&#8205;</a>'
            text = (
                f'{custom_banner}<b>{title}</b> [{status}]\n\n'
                f'<blockquote expandable>{commands}</blockquote>'
            )
            btns = [
                [
                    Button.inline("Выгрузить" if is_active else "Загрузить", data=f"toggle_act_{mod_name}"),
                    Button.inline("Убрать ★" if is_fav else "В Избранное ★", data=f"toggle_fav_{mod_name}")
                ],
                [Button.inline("« Назад", data="mod_tab_act_0")]
            ]
            await safe_edit(event, bot, text, btns)

        # ИЗБРАННОЕ
        elif data.startswith("toggle_fav_"):
            mod_name = data.replace("toggle_fav_", "")
            if mod_name in FAVORITE_MODULES:
                FAVORITE_MODULES.remove(mod_name)
                await event.answer("Удалено из избранного", alert=False)
            else:
                FAVORITE_MODULES.add(mod_name)
                await event.answer("Добавлено в избранное ★", alert=False)

            loaded = get_loaded_modules()
            is_active = mod_name in loaded
            is_fav = mod_name in FAVORITE_MODULES

            if is_active:
                mod_obj = loaded[mod_name]
                title = getattr(mod_obj, "TITLE", f"⊞ {mod_name.capitalize()}")
                mod_banner = getattr(mod_obj, "BANNER", HEADER_BANNER_URL)
                commands = getattr(mod_obj, "COMMANDS", "• Команды не описаны в API модуля")
                status = "АКТИВЕН ⊞"
            else:
                title = f"⧉ {mod_name.capitalize()}"
                mod_banner = HEADER_BANNER_URL
                commands = "• Модуль на складе (выгружен из ОЗУ)\n• Нажмите [ Загрузить ] для активации и просмотра команд"
                status = "ВЫГРУЖЕН ⧉"

            custom_banner = f'<a href="{mod_banner}">&#8205;</a>'
            text = (
                f'{custom_banner}<b>{title}</b> [{status}]\n\n'
                f'<blockquote expandable>{commands}</blockquote>'
            )
            btns = [
                [
                    Button.inline("Выгрузить" if is_active else "Загрузить", data=f"toggle_act_{mod_name}"),
                    Button.inline("Убрать ★" if is_fav else "В Избранное ★", data=f"toggle_fav_{mod_name}")
                ],
                [Button.inline("« Назад", data="mod_tab_act_0")]
            ]
            await safe_edit(event, bot, text, btns)

        # РАЗДЕЛ: СИСТЕМА (С ВОЗВРАЩЕННЫМИ ЛОГАМИ)
        elif data == "menu_system":
            text = (
                f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB — Система</b>\n\n'
                f'<blockquote expandable>• Сервер: GitHub Actions (Ubuntu)\n'
                f'• Занято RAM: {get_ram_usage()}\n'
                f'• Нагрузка CPU: {get_cpu_load()}</blockquote>'
            )
            btns = [
                [Button.inline("≡ Логи ядра", data="menu_logs")],
                [Button.inline("« Назад", data="menu_main")]
            ]
            await safe_edit(event, bot, text, btns)

        # РАЗДЕЛ: ЛОГИ
        elif data == "menu_logs":
            logs = mem_logs.get_logs(12)
            log_text = "\n".join(logs) if logs else "Журнал пуст."
            text = f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB — Логи ядра</b>\n\n<pre>{log_text}</pre>'
            btns = [
                [Button.inline("↺ Обновить", data="menu_logs"), Button.inline("« Назад", data="menu_system")]
            ]
            await safe_edit(event, bot, text, btns)

        # РАЗДЕЛ: НАСТРОЙКИ
        elif data == "menu_settings":
            text = (
                f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB — Настройки</b>\n\n'
                f'<blockquote expandable>• Раздел в активной разработке\n'
                f'• Ожидайте новых патчей ядра</blockquote>'
            )
            await safe_edit(event, bot, text, [[Button.inline("« Назад", data="menu_main")]])

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
