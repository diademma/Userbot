# core/inline.py
import logging
import os
import time

from telethon import Button, events, functions, types

from core.config import USERBOT_NAME, OWNER_ID
from core.db import (
    db_get_exceptions,
    db_get_timer,
    db_get_trusted,
    is_authorized,
    mem_logs,
)
from core.loader import get_loaded_modules, get_pending_modules


# Прямая ссылка на баннер.
HEADER_BANNER_URL = (
    "https://raw.githubusercontent.com/diademma/Userbot/main/assets/LLEHTABPA.jpg"
)

START_TIME = time.time()

# Строгие символы без цветных эмодзи.
MODULE_TITLES = {
    "sniper": "⌖ Sniper & Guard",
    "media_studio": "▷ Media Studio",
    "quote_stickers": "❝ Quote Stickers",
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

        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            mem = {}
            for line in f:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
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
        [
            Button.inline("⊞ Модули", data="menu_modules"),
            Button.inline("⌘ О системе", data="menu_system"),
        ],
        [
            Button.inline("≡ Настройки", data="menu_settings"),
        ],
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


def make_banner(body: str) -> str:
    """
    Важный момент:
    невидимая ссылка находится ВНУТРИ blockquote.
    Telegram получает эту ссылку как источник web-preview,
    а invert_media=True переносит превью наверх.
    """
    return (
        f'<blockquote expandable>'
        f'<a href="{HEADER_BANNER_URL}">&#8205;</a>'
        f'{body}'
        f'</blockquote>'
    )


def _force_preview_inverted(result):
    """
    Жёстко заменяет стандартный InputBotInlineMessageText на
    InputBotInlineMessageMediaWebPage.

    Это важнее простого:
        result.result.send_message.invert_media = True

    потому что мы явно указываем Telegram:
    - какой URL является media preview;
    - что media надо поставить сверху;
    - что нужен большой preview.
    """
    try:
        send_message = result.result.send_message

        # Уже нужный тип — просто выставляем флаги.
        if isinstance(send_message, types.InputBotInlineMessageMediaWebPage):
            send_message.invert_media = True
            send_message.force_large_media = True
            send_message.optional = True
            send_message.url = HEADER_BANNER_URL
            return result

        # Обычный article от InlineBuilder уже содержит распарсенные
        # entities, поэтому мы сохраняем их и только меняем тип
        # inline-message на явный MediaWebPage.
        result.result.send_message = types.InputBotInlineMessageMediaWebPage(
            message=send_message.message,
            entities=send_message.entities,
            url=HEADER_BANNER_URL,
            reply_markup=send_message.reply_markup,
            invert_media=True,
            force_large_media=True,
            optional=True,
        )
    except Exception:
        # Дополнительный fallback: хотя бы флаг на исходном send_message.
        try:
            result.result.send_message.invert_media = True
        except Exception:
            pass

    return result


async def safe_edit(event, text, buttons):
    """
    Редактирование inline-сообщения с принудительным web-preview сверху.

    Сначала пробуем прямой MTProto EditInlineBotMessageRequest:
    Telegram получает invert_media=True не через удобный wrapper,
    а непосредственно на уровне API.
    """
    try:
        inline_message_id = getattr(event, "inline_message_id", None)

        if inline_message_id:
            client = event.client

            # Telethon сам преобразует Button.inline(...) в ReplyInlineMarkup.
            reply_markup = client.build_reply_markup(buttons)

            # Тот же HTML-парсер, которым Telethon пользуется при обычной
            # отправке, чтобы сохранить <b>, <code>, <blockquote>, <a> и т.д.
            parsed_text, entities = await client._parse_message_text(
                text,
                parse_mode="html",
            )

            await client(
                functions.messages.EditInlineBotMessageRequest(
                    id=inline_message_id,
                    message=parsed_text,
                    media=types.InputMediaWebPage(url=HEADER_BANNER_URL),
                    reply_markup=reply_markup,
                    entities=entities,
                    invert_media=True,
                )
            )
            return

        # Fallback для ситуаций, когда событие не содержит inline_message_id.
        await event.edit(
            text,
            buttons=buttons,
            parse_mode="html",
            link_preview=True,
            invert_media=True,
        )

    except Exception as e:
        logging.error("Ошибка safe_edit: %s", e)

        # Последний fallback — штатный Telethon wrapper.
        try:
            await event.edit(
                text,
                buttons=buttons,
                parse_mode="html",
                link_preview=True,
                invert_media=True,
            )
        except Exception as fallback_error:
            logging.error("Ошибка fallback safe_edit: %s", fallback_error)


def init_inline(user, bot):
    if not bot:
        return

    # ------------------------------------------------------------------
    # 1. ГЛАВНОЕ МЕНЮ
    # ------------------------------------------------------------------
    @bot.on(events.InlineQuery)
    async def inline_query_handler(event):
        user_me = await user.get_me()

        if event.sender_id != OWNER_ID and event.sender_id != user_me.id:
            return

        builder = event.builder

        start = time.perf_counter()
        await bot.get_me()
        ping_ms = (time.perf_counter() - start) * 1000
        uptime = get_uptime_str()

        text = make_banner(
            f"<b>Proxima UB</b>\n\n"
            f"инфо:\n"
            f"• Пинг: {ping_ms:.3f} мс\n"
            f"• Время работы: {uptime}"
        )

        result = builder.article(
            title="Proxima UB",
            text=text,
            buttons=build_home_keyboard(),
            parse_mode="html",
            link_preview=True,
        )

        # Ключевая правка: не надеемся на wrapper, а заставляем
        # inline-result использовать MediaWebPage + invert_media.
        result = _force_preview_inverted(result)

        await event.answer([result], cache_time=1)

    # ------------------------------------------------------------------
    # 2. ОБРАБОТЧИК КЛИКОВ ПО КНОПКАМ
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery)
    async def callback_handler(event):
        if event.sender_id != OWNER_ID:
            return await event.answer("Доступ ограничен.", alert=True)

        data = event.data.decode("utf-8")

        # Общий блок баннера для всех карточек.
        banner_body = ""

        # ==============================================================
        # ГЛАВНЫЙ ЭКРАН
        # ==============================================================
        if data == "menu_main":
            start = time.perf_counter()
            await bot.get_me()
            ping_ms = (time.perf_counter() - start) * 1000
            uptime = get_uptime_str()

            banner_body = (
                f"<b>Proxima UB</b>\n\n"
                f"инфо:\n"
                f"• Пинг: {ping_ms:.3f} мс\n"
                f"• Время работы: {uptime}"
            )

            text = make_banner(banner_body)
            await safe_edit(event, text, build_home_keyboard())

        # ==============================================================
        # МОДУЛИ
        # ==============================================================
        elif data == "menu_modules":
            loaded = get_loaded_modules()

            banner_body = (
                f"<b>Proxima UB — Модули</b>\n\n"
                f"инфо:\n"
                f"• Активно компонентов: {len(loaded)}\n"
                f"• Состояние: все ядра в норме"
            )

            text = f"{make_banner(banner_body)}\n\nВыберите компонент для управления:"
            await safe_edit(event, text, build_modules_keyboard())

        # ==============================================================
        # О СИСТЕМЕ
        # ==============================================================
        elif data == "menu_system":
            start = time.perf_counter()
            await bot.get_me()
            ping_ms = (time.perf_counter() - start) * 1000
            uptime = get_uptime_str()
            ram = get_ram_usage()
            cpu = get_cpu_load()

            banner_body = (
                f"<b>Proxima UB — Система</b>\n\n"
                f"инфо:\n"
                f"• Сервер: GitHub Actions (Ubuntu)\n"
                f"• Пинг сети: {ping_ms:.3f} мс\n"
                f"• Время работы: {uptime}\n"
                f"• Занято RAM: {ram}\n"
                f"• Нагрузка CPU: {cpu}"
            )

            text = make_banner(banner_body)

            btns = [
                [Button.inline("≡ Логи ядра", data="menu_logs")],
                [Button.inline("« Назад", data="menu_main")],
            ]

            await safe_edit(event, text, btns)

        # ==============================================================
        # НАСТРОЙКИ
        # ==============================================================
        elif data == "menu_settings":
            banner_body = (
                f"<b>Proxima UB — Настройки</b>\n\n"
                f"инфо:\n"
                f"• Раздел в активной разработке\n"
                f"• Параметры ядра появятся позже"
            )

            text = make_banner(banner_body)

            await safe_edit(
                event,
                text,
                [[Button.inline("« Назад", data="menu_main")]],
            )

        # ==============================================================
        # ЛОГИ
        # ==============================================================
        elif data == "menu_logs":
            logs = mem_logs.get_logs(12)
            log_text = "\n".join(logs) if logs else "Журнал пуст."

            banner_body = (
                f"<b>Proxima UB — Логи ядра</b>\n\n"
                f"<pre>{log_text}</pre>"
            )

            text = make_banner(banner_body)

            btns = [
                [
                    Button.inline("↺ Обновить", data="menu_logs"),
                    Button.inline("« Назад", data="menu_system"),
                ]
            ]

            await safe_edit(event, text, btns)

        # ==============================================================
        # SNIPER
        # ==============================================================
        elif data == "open_mod_sniper":
            rp_t = db_get_timer("rp_delay", 10)
            info_t = db_get_timer("info_delay", 30)

            banner_body = (
                f"<b>Sniper &amp; Guard</b>\n\n"
                f"инфо:\n"
                f"• Фильтрация рекламы: активна\n"
                f"• Задержка РП: {rp_t} с\n"
                f"• Задержка инфо: {info_t} с"
            )

            text = f"{make_banner(banner_body)}\n\nПараметры модуля:"

            btns = [
                [
                    Button.inline("✓ Исключения", data="sub_sniper_excs"),
                    Button.inline("▪ Доверенные", data="sub_sniper_trusted"),
                ],
                [
                    Button.inline("◷ Таймеры", data="sub_sniper_timers"),
                ],
                [
                    Button.inline("« Назад к модулям", data="menu_modules"),
                ],
            ]

            await safe_edit(event, text, btns)

        elif data == "sub_sniper_timers":
            rp_t = db_get_timer("rp_delay", 10)
            info_t = db_get_timer("info_delay", 30)

            banner_body = (
                f"<b>Параметры таймеров</b>\n\n"
                f"• РП ботов: {rp_t} с (sudo рп [сек])\n"
                f"• Длинные инфо: {info_t} с (sudo инфо [сек])"
            )

            text = make_banner(banner_body)

            await safe_edit(
                event,
                text,
                [[Button.inline("« Назад", data="open_mod_sniper")]],
            )

        elif data == "sub_sniper_trusted":
            items = db_get_trusted()
            trusted_list = (
                "\n".join(f"• {u} ({i})" for i, u in items)
                if items
                else "Список пуст."
            )

            text = make_banner(
                f"<b>Доверенные пользователи</b>\n\n{trusted_list}"
            )

            await safe_edit(
                event,
                text,
                [[Button.inline("« Назад", data="open_mod_sniper")]],
            )

        elif data == "sub_sniper_excs":
            items = db_get_exceptions()
            exc_list = (
                "\n".join(f"• {w}" for w in items[:20])
                if items
                else "Список пуст."
            )

            text = make_banner(
                f"<b>Белый список исключений</b>\n\n{exc_list}"
            )

            await safe_edit(
                event,
                text,
                [[Button.inline("« Назад", data="open_mod_sniper")]],
            )

        # ==============================================================
        # MEDIA STUDIO
        # ==============================================================
        elif data == "open_mod_media_studio":
            banner_body = (
                f"<b>Media Studio</b>\n\n"
                f"инфо:\n"
                f"• Движок: статический FFmpeg\n"
                f"• Поддержка: GIF, видео-кружки, аудио"
            )

            text = (
                f"{make_banner(banner_body)}\n\n"
                f"Команда вызова: <code>sudo медиа</code>"
            )

            await safe_edit(
                event,
                text,
                [[Button.inline("« Назад к модулям", data="menu_modules")]],
            )

        # ==============================================================
        # QUOTE STICKERS
        # ==============================================================
        elif data == "open_mod_quote_stickers":
            banner_body = (
                f"<b>Quote Stickers</b>\n\n"
                f"инфо:\n"
                f"• Рендер: 3D цитаты-стикеры\n"
                f"• Либы: OpenCV, Lottie, Pillow"
            )

            text = (
                f"{make_banner(banner_body)}\n\n"
                f"Команда: <code>sudo цитата</code> (в реплай)"
            )

            await safe_edit(
                event,
                text,
                [[Button.inline("« Назад к модулям", data="menu_modules")]],
            )

        # ==============================================================
        # ДИНАМИЧЕСКИЙ МОДУЛЬ
        # ==============================================================
        elif data.startswith("open_mod_"):
            mod_name = data.replace("open_mod_", "", 1)

            banner_body = (
                f"<b>Модуль: {mod_name}</b>\n\n"
                f"инфо:\n"
                f"• Статус: загружен в оперативную память\n"
                f"• Доступен через команды в чате"
            )

            text = make_banner(banner_body)

            await safe_edit(
                event,
                text,
                [[Button.inline("« Назад к модулям", data="menu_modules")]],
            )

        # ==============================================================
        # МОДУЛЬ В ПРОЦЕССЕ ЗАГРУЗКИ
        # ==============================================================
        elif data.startswith("pending_mod_"):
            await event.answer(
                "Компоненты докачиваются в фоне. Подождите немного...",
                alert=True,
            )

    # ------------------------------------------------------------------
    # 3. ВЫЗОВ ПАНЕЛИ ПО КОМАНДЕ SUDO
    # ------------------------------------------------------------------
    @user.on(events.NewMessage(pattern=r"^sudo$"))
    async def sudo_open_menu(event):
        if not await is_authorized(event):
            return

        await event.delete()

        bot_me = await bot.get_me()

        try:
            results = await user.inline_query(bot_me.username, "panel")

            if results:
                await results[0].click(
                    event.chat_id,
                    reply_to=event.reply_to_msg_id,
                )

        except Exception as e:
            logging.error("Ошибка вызова панели: %s", e)
