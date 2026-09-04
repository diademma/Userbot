# core/inline.py
import time
import logging
from telethon import events, Button
from core.config import USERBOT_NAME, OWNER_ID
from core.db import is_authorized, mem_logs, db_get_timer, db_get_trusted, db_get_exceptions
from core.loader import get_loaded_modules, get_pending_modules

# Ссылка на фото-баннер для текстовой карточки
HEADER_BANNER_URL = "" 

MODULE_ICONS = {
    "sniper": "🎯 Sniper & Антиспам",
    "media_studio": "🎛️ Media Studio",
    "quote_stickers": "✨ 3D-Стикеры Цитаты"
}

def build_main_keyboard():
    """Генерация кнопок с учетом активных и ожидающих модулей"""
    loaded = get_loaded_modules()
    pending = get_pending_modules()
    buttons = []
    
    row = []
    # 1. Готовые модули
    for mod_name in loaded.keys():
        title = MODULE_ICONS.get(mod_name, f"🧩 {mod_name.capitalize()}")
        row.append(Button.inline(title, data=f"open_mod_{mod_name}"))
        if len(row) == 2:
            buttons.append(row)
            row = []

    # 2. Модули в процессе загрузки библиотек
    for mod_name in pending:
        title = f"⏳ {mod_name.capitalize()} (Загрузка...)"
        row.append(Button.inline(title, data=f"pending_mod_{mod_name}"))
        if len(row) == 2:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    # 3. Системные кнопки
    buttons.append([
        Button.inline("📜 Логи системы", data="menu_logs"),
        Button.inline("❌ Закрыть", data="menu_close")
    ])
    return buttons

def init_inline(user, bot):
    if not bot:
        return

    # --- 1. ГЕНЕРАТОР КАРТОЧКИ ПО ИНЛАЙНУ ---
    @bot.on(events.InlineQuery)
    async def inline_query_handler(event):
        user_me = await user.get_me()
        if event.sender_id != OWNER_ID and event.sender_id != user_me.id:
            return

        builder = event.builder
        loaded = get_loaded_modules()

        # Замер пинга
        start = time.perf_counter()
        await bot.get_me()
        ping_ms = round((time.perf_counter() - start) * 1000)

        banner = f"[​​​​​​​​​​​]({HEADER_BANNER_URL})" if HEADER_BANNER_URL else ""

        text = (
            f"{banner}🪐 **{USERBOT_NAME} — Центр управления**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚡ **Пинг:** `{ping_ms} ms` | 🛰 **Модулей:** `{len(loaded)}`\n"
            f"⏱ **GitHub Actions:** Активен\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👇 **Выберите модуль для управления:**"
        )

        result = builder.article(
            title=f"{USERBOT_NAME} Control Panel",
            text=text,
            buttons=build_main_keyboard(),
            link_preview=bool(HEADER_BANNER_URL)
        )
        await event.answer([result], cache_time=1)

    # --- 2. ОБРАБОТЧИК НАЖАТИЙ НА КНОПКИ ---
    @bot.on(events.CallbackQuery)
    async def callback_handler(event):
        if event.sender_id != OWNER_ID:
            return await event.answer("🚫 Это панель управления чужого юзербота!", alert=True)

        data = event.data.decode("utf-8")

        # КЛИК ПО МОДУЛЮ В ПРОЦЕССЕ ЗАГРУЗКИ
        if data.startswith("pending_mod_"):
            return await event.answer("⏳ Модуль докачивает библиотеки в фоне, подождите секунд 15...", alert=True)

        # ЗАКРЫТИЕ ПАНЕЛИ
        elif data == "menu_close":
            start = time.perf_counter()
            await bot.get_me()
            ping_ms = round((time.perf_counter() - start) * 1000)
            
            banner = f"[​​​​​​​​​​​]({HEADER_BANNER_URL})" if HEADER_BANNER_URL else ""
            close_text = (
                f"{banner}🪐 **{USERBOT_NAME}**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📡 **Статус:** Панель свернута\n"
                f"⚡ **Пинг сети:** `{ping_ms} ms`\n"
                f"🛰 **Система:** GitHub Actions Core v5.0\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
            return await event.edit(close_text, buttons=None, link_preview=bool(HEADER_BANNER_URL))

        # ВОЗВРАТ В ГЛАВНОЕ МЕНЮ
        elif data == "menu_main":
            start = time.perf_counter()
            await bot.get_me()
            ping_ms = round((time.perf_counter() - start) * 1000)
            loaded = get_loaded_modules()

            banner = f"[​​​​​​​​​​​]({HEADER_BANNER_URL})" if HEADER_BANNER_URL else ""
            text = (
                f"{banner}🪐 **{USERBOT_NAME} — Центр управления**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚡ **Пинг:** `{ping_ms} ms` | 🛰 **Модулей:** `{len(loaded)}`\n"
                f"⏱ **GitHub Actions:** Активен\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👇 **Выберите модуль для управления:**"
            )
            await event.edit(text, buttons=build_main_keyboard(), link_preview=bool(HEADER_BANNER_URL))

        # ПРОСМОТР МОДУЛЯ SNIPER
        elif data == "open_mod_sniper":
            rp_t = db_get_timer('rp_delay', 10)
            info_t = db_get_timer('info_delay', 30)
            text = (
                f"🎯 **Управление модулем: Sniper & Антиспам**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• Фильтрация спама и ботов: **Активна**\n"
                f"• Таймер РП: **{rp_t}с** | Инфо: **{info_t}с**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Выберите параметр для просмотра:"
            )
            btns = [
                [Button.inline("🛡️ Исключения", data="sub_sniper_excs"), Button.inline("👥 Доверенные", data="sub_sniper_trusted")],
                [Button.inline("⏱️ Настройки таймеров", data="sub_sniper_timers")],
                [Button.inline("⬅️ Назад в меню", data="menu_main")]
            ]
            await event.edit(text, buttons=btns)

        elif data == "sub_sniper_timers":
            rp_t = db_get_timer('rp_delay', 10)
            info_t = db_get_timer('info_delay', 30)
            text = (
                f"⏱️ **Настройка таймеров сноса:**\n\n"
                f"• РП ботов: **{rp_t}с** (`sudo рп [сек]`)\n"
                f"• Длинные инфо: **{info_t}с** (`sudo инфо [сек]`)"
            )
            await event.edit(text, buttons=[[Button.inline("⬅️ Назад к Sniper", data="open_mod_sniper")]])

        elif data == "sub_sniper_trusted":
            items = db_get_trusted()
            text = "👥 **Доверенные пользователи:**\n\n" + ("\n".join([f"• {u} (`{i}`)" for i, u in items]) if items else "Список пуст.")
            await event.edit(text, buttons=[[Button.inline("⬅️ Назад к Sniper", data="open_mod_sniper")]])

        elif data == "sub_sniper_excs":
            items = db_get_exceptions()
            text = "🛡️ **Белый список исключений:**\n\n" + ("\n".join([f"• `{w}`" for w in items[:20]]) if items else "Список пуст.")
            await event.edit(text, buttons=[[Button.inline("⬅️ Назад к Sniper", data="open_mod_sniper")]])

        # ПРОСМОТР МОДУЛЯ MEDIA STUDIO
        elif data == "open_mod_media_studio":
            text = (
                f"🎛️ **Управление модулем: Media Studio**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Обработка медиа через статический FFmpeg.\n\n"
                f"**Команды:**\n"
                f"• `sudo медиа` — Интерактивный редактор медиа"
            )
            await event.edit(text, buttons=[[Button.inline("⬅️ Назад в меню", data="menu_main")]])

        # ПРОСМОТР МОДУЛЯ QUOTE STICKERS
        elif data == "open_mod_quote_stickers":
            text = (
                f"✨ **Управление модулем: Quote Stickers**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Генерация 3D цитат-стикеров из сообщений.\n\n"
                f"**Команды:**\n"
                f"• `sudo цитата` *(в реплай)* — Создать стикер"
            )
            await event.edit(text, buttons=[[Button.inline("⬅️ Назад в меню", data="menu_main")]])

        # ДИНАМИЧЕСКИЙ МОДУЛЬ
        elif data.startswith("open_mod_"):
            mod_name = data.replace("open_mod_", "")
            text = (
                f"🧩 **Модуль:** `{mod_name}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Статус: Активен в памяти ядра.\n"
                f"Используйте его команды в чате."
            )
            await event.edit(text, buttons=[[Button.inline("⬅️ Назад в меню", data="menu_main")]])

        # ЛОГИ
        elif data == "menu_logs":
            logs = mem_logs.get_logs(12)
            log_text = "\n".join(logs) if logs else "Логи пусты."
            await event.edit(
                f"📜 **События ядра:**\n\n```text\n{log_text}\n```",
                buttons=[[Button.inline("🔄 Обновить", data="menu_logs"), Button.inline("⬅️ Назад", data="menu_main")]]
            )

    # --- 3. ВЫЗОВ ПО СЛОВУ SUDO ---
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
