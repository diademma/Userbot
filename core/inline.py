# core/inline.py
import logging
from telethon import events, Button
from core.config import USERBOT_NAME, OWNER_ID
from core.db import is_authorized, mem_logs, db_get_timer, db_get_trusted, db_get_exceptions

def get_main_keyboard():
    return [
        [Button.inline("🛡️ Исключения", data="menu_excs"), Button.inline("⏱️ Таймеры", data="menu_timers")],
        [Button.inline("👥 Доверенные", data="menu_trusted"), Button.inline("📜 Логи", data="menu_logs")],
        [Button.inline("❌ Закрыть", data="menu_close")]
    ]

def init_inline(user, bot):
    if not bot:
        return

    # 1. Формирование карточки по инлайн-запросу
    @bot.on(events.InlineQuery)
    async def inline_query_handler(event):
        user_me = await user.get_me()
        if event.sender_id != OWNER_ID and event.sender_id != user_me.id:
            return

        builder = event.builder
        rp_t = db_get_timer('rp_delay', 10)
        info_t = db_get_timer('info_delay', 30)

        text = (
            f"🪐 **{USERBOT_NAME} — Центр управления**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚡ **Статус:** Онлайн (GitHub Actions)\n"
            f"⏱ **РП:** `{rp_t}с` | **Инфо:** `{info_t}с`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👇 Выберите нужный раздел:"
        )

        result = builder.article(
            title=f"{USERBOT_NAME} Menu",
            text=text,
            buttons=get_main_keyboard()
        )
        await event.answer([result], cache_time=1)

    # 2. Обработка кликов по кнопкам
    @bot.on(events.CallbackQuery)
    async def callback_handler(event):
        if event.sender_id != OWNER_ID:
            return await event.answer("🚫 Это панель управления чужого юзербота!", alert=True)

        data = event.data.decode("utf-8")

        if data == "menu_close":
            await event.delete()

        elif data == "menu_main":
            rp_t = db_get_timer('rp_delay', 10)
            info_t = db_get_timer('info_delay', 30)
            text = (
                f"🪐 **{USERBOT_NAME} — Центр управления**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚡ **Статус:** Онлайн (GitHub Actions)\n"
                f"⏱ **РП:** `{rp_t}с` | **Инфо:** `{info_t}с`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👇 Выберите нужный раздел:"
            )
            await event.edit(text, buttons=get_main_keyboard())

        elif data == "menu_logs":
            logs = mem_logs.get_logs(12)
            log_text = "\n".join(logs) if logs else "Логи пусты."
            await event.edit(
                f"📜 **Последние события:**\n\n```text\n{log_text}\n```",
                buttons=[[Button.inline("🔄 Обновить", data="menu_logs"), Button.inline("⬅️ Назад", data="menu_main")]]
            )

        elif data == "menu_timers":
            rp_t = db_get_timer('rp_delay', 10)
            info_t = db_get_timer('info_delay', 30)
            await event.edit(
                f"⏱️ **Настройки таймеров сноса:**\n\n"
                f"• РП / действия ботов: **{rp_t} сек.**\n"
                f"• Длинные инфо / меню: **{info_t} сек.**\n\n"
                f"Изменить: `sudo рп [сек]` / `sudo инфо [сек]`",
                buttons=[[Button.inline("⬅️ Назад", data="menu_main")]]
            )

        elif data == "menu_trusted":
            items = db_get_trusted()
            text = "👥 **Доверенные пользователи:**\n\n" + ("\n".join([f"• {name} (`{uid}`)" for uid, name in items]) if items else "Пусто.")
            await event.edit(text, buttons=[[Button.inline("⬅️ Назад", data="menu_main")]])

        elif data == "menu_excs":
            items = db_get_exceptions()
            text = "🛡️ **Исключения (Белый список):**\n\n" + ("\n".join([f"• `{w}`" for w in items[:20]]) if items else "Пусто.")
            await event.edit(text, buttons=[[Button.inline("⬅️ Назад", data="menu_main")]])

    # 3. Вызов меню командой sudo меню
    @user.on(events.NewMessage(pattern=r"^sudo\s+(меню|menu)$"))
    async def open_menu_cmd(event):
        if not await is_authorized(event):
            return

        await event.delete()
        bot_me = await bot.get_me()
        try:
            results = await user.inline_query(bot_me.username, "menu")
            if results:
                await results[0].click(event.chat_id, reply_to=event.reply_to_msg_id)
        except Exception as e:
            logging.error(f"Ошибка вызова инлайн-меню: {e}")
