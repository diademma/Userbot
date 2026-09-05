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

HEADER_BANNER_URL = "https://raw.githubusercontent.com/diademma/Userbot/main/assets/LLEHTABPA.jpg"
START_TIME = time.time()

# Временное хранилище избранных модулей (в будущем можно перенести в БД)
FAVORITE_MODULES = set()

MODULE_TITLES = {
    "sniper": "⌖ Sniper & Guard",
    "media_studio": "▷ Media Studio",
    "quote_stickers": "❝ Quote Stickers"
}

# Тексты команд для модулей
MODULE_INFO = {
    "sniper": "• Фильтрация рекламы\n• sudo рп [сек] — Таймер РП\n• sudo инфо [сек] — Таймер инфо\n• sudo +искл / -искл [фраза]\n• sudo +бан / -бан [фраза]",
    "media_studio": "• sudo медиа — Запуск редактора\n• Поддержка GIF, кружочков, аудио",
    "quote_stickers": "• sudo цитата — Генерация 3D стикера\n(Использовать в реплай на сообщение)"
}

def get_uptime_str() -> str:
    total_sec = int(time.time() - START_TIME)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    seconds = total_sec % 60
    return f"{hours:02d} : {minutes:02d} : {seconds:02d}"

def get_all_warehouse_modules():
    """Получает список всех .py файлов со склада (папка modules)"""
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
    """Сборка клавиатуры склада с табами и пагинацией"""
    loaded = list(get_loaded_modules().keys())
    all_mods = get_all_warehouse_modules()
    favs = list(FAVORITE_MODULES)

    if tab == "fav": source_list = favs
    elif tab == "wh": source_list = all_mods
    else: source_list = loaded

    per_page = 5
    total_pages = max(1, (len(source_list) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    current_items = source_list[page * per_page : (page + 1) * per_page]

    buttons = []
    
    # 1. Ряд табов (✮ Избранное | ⛬ Активные | ᪬ Склад)
    buttons.append([
        Button.inline("✮" if tab=="fav" else "✩", data="mod_tab_fav_0"),
        Button.inline("⛬" if tab=="act" else "⛶", data="mod_tab_act_0"),
        Button.inline("᪬" if tab=="wh" else "⬚", data="mod_tab_wh_0")
    ])

    # 2. Модули текущей страницы
    for mod in current_items:
        title = MODULE_TITLES.get(mod, f"⊞ {mod.capitalize()}")
        # Добавляем индикатор, если модуль загружается
        if mod in get_pending_modules():
            title = f"◷ {mod} (загрузка)"
        buttons.append([Button.inline(title, data=f"open_mod_{mod}")])

    # 3. Пагинация
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
    """Жесткое редактирование через API, гарантирующее invert_media=True"""
    parsed_text, entities = await bot._parse_message_text(text, 'html')
    try:
        await bot(EditInlineBotMessageRequest(
            id=event.inline_message_id,
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

        # РАЗДЕЛ: СКЛАД МОДУЛЕЙ (С ТАБАМИ)
        elif data.startswith("mod_tab_"):
            parts = data.split("_")
            tab = parts[2]
            page = int(parts[3]) if len(parts) > 3 else 0
            
            loaded = list(get_loaded_modules().keys())
            all_mods = get_all_warehouse_modules()
            favs = list(FAVORITE_MODULES)

            if tab == "fav":
                lst = favs
                tab_name = "Избранные"
            elif tab == "wh":
                lst = all_mods
                tab_name = "Склад"
            else:
                lst = loaded
                tab_name = "Активные"

            list_str = "\n".join([f"• {m}" for m in lst]) if lst else "• Пусто"

            text = (
                f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB</b>\n\n'
                f'• Загружено модулей: {len(loaded)}\n'
                f'<blockquote expandable>Вкладка: {tab_name}\n\n{list_str}</blockquote>'
            )
            await safe_edit(event, bot, text, build_modules_keyboard(tab, page))

        # ОТКРЫТИЕ КОНКРЕТНОГО МОДУЛЯ
        elif data.startswith("open_mod_"):
            mod_name = data.replace("open_mod_", "")
            is_active = mod_name in get_loaded_modules()
            is_fav = mod_name in FAVORITE_MODULES

            info_text = MODULE_INFO.get(mod_name, "• Команды не задокументированы\n• Модуль функционирует штатно")
            status = "АКТИВЕН ⛬" if is_active else "ВЫГРУЖЕН ᪬"

            text = (
                f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB</b>\n\n'
                f'• Модуль: {mod_name} [{status}]\n'
                f'<blockquote expandable>{info_text}</blockquote>'
            )

            btns = [
                [
                    Button.inline("Выгрузить" if is_active else "Загрузить", data=f"toggle_act_{mod_name}"),
                    Button.inline("Убрать ✮" if is_fav else "В Избранное ✮", data=f"toggle_fav_{mod_name}")
                ],
                [Button.inline("« Назад", data="mod_tab_act_0")]
            ]
            await safe_edit(event, bot, text, btns)

        # ЗАГРУЗКА / ВЫГРУЗКА МОДУЛЯ ИЗ ОЗУ
        elif data.startswith("toggle_act_"):
            mod_name = data.replace("toggle_act_", "")
            from core.loader import LOADED_MODULES
            
            if mod_name in LOADED_MODULES:
                # ВЫГРУЖАЕМ
                del LOADED_MODULES[mod_name]
                if mod_name in sys.modules:
                    del sys.modules[mod_name]
                await event.answer(f"Модуль {mod_name} выгружен из ОЗУ", alert=True)
            else:
                # ЗАГРУЖАЕМ
                file_path = os.path.join(MODULES_DIR, f"{mod_name}.py")
                if os.path.exists(file_path):
                    if load_single_module(file_path, user, bot, silent=True):
                        await event.answer(f"Модуль {mod_name} загружен в ядро", alert=True)
                    else:
                        await event.answer(f"Ошибка загрузки {mod_name}", alert=True)
                else:
                    await event.answer(f"Файл {mod_name}.py не найден на складе", alert=True)

            # Обновляем карточку
            is_active = mod_name in get_loaded_modules()
            is_fav = mod_name in FAVORITE_MODULES
            info_text = MODULE_INFO.get(mod_name, "• Команды не задокументированы")
            status = "АКТИВЕН ⛬" if is_active else "ВЫГРУЖЕН ᪬"

            text = (
                f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB</b>\n\n'
                f'• Модуль: {mod_name} [{status}]\n'
                f'<blockquote expandable>{info_text}</blockquote>'
            )
            btns = [
                [
                    Button.inline("Выгрузить" if is_active else "Загрузить", data=f"toggle_act_{mod_name}"),
                    Button.inline("Убрать ✮" if is_fav else "В Избранное ✮", data=f"toggle_fav_{mod_name}")
                ],
                [Button.inline("« Назад", data="mod_tab_act_0")]
            ]
            await safe_edit(event, bot, text, btns)

        # ДОБАВЛЕНИЕ В ИЗБРАННОЕ
        elif data.startswith("toggle_fav_"):
            mod_name = data.replace("toggle_fav_", "")
            if mod_name in FAVORITE_MODULES:
                FAVORITE_MODULES.remove(mod_name)
                await event.answer(f"Удалено из избранного", alert=False)
            else:
                FAVORITE_MODULES.add(mod_name)
                await event.answer(f"Добавлено в избранное ✮", alert=False)
            
            # Обновляем карточку
            is_active = mod_name in get_loaded_modules()
            is_fav = mod_name in FAVORITE_MODULES
            info_text = MODULE_INFO.get(mod_name, "• Команды не задокументированы")
            status = "АКТИВЕН ⛬" if is_active else "ВЫГРУЖЕН ᪬"

            text = (
                f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB</b>\n\n'
                f'• Модуль: {mod_name} [{status}]\n'
                f'<blockquote expandable>{info_text}</blockquote>'
            )
            btns = [
                [
                    Button.inline("Выгрузить" if is_active else "Загрузить", data=f"toggle_act_{mod_name}"),
                    Button.inline("Убрать ✮" if is_fav else "В Избранное ✮", data=f"toggle_fav_{mod_name}")
                ],
                [Button.inline("« Назад", data="mod_tab_act_0")]
            ]
            await safe_edit(event, bot, text, btns)

        # РАЗДЕЛ: СИСТЕМА
        elif data == "menu_system":
            text = (
                f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB</b>\n\n'
                f'<blockquote expandable>• Сервер: GitHub Actions (Ubuntu)\n'
                f'• Занято RAM: {get_ram_usage()}\n'
                f'• Нагрузка CPU: {get_cpu_load()}</blockquote>'
            )
            await safe_edit(event, bot, text, [[Button.inline("« Назад", data="menu_main")]])

        # РАЗДЕЛ: НАСТРОЙКИ
        elif data == "menu_settings":
            text = (
                f'{banner}<b>𝗣𝗿𝗼𝘅𝗶𝗺𝗮 UB</b>\n\n'
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
