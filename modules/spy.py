# modules/spy.py
from telethon import events

def register(user, bot=None):
    @user.on(events.NewMessage(pattern=r"^sudo\s+spy$"))
    async def spy_handler(event):
        if not event.is_reply:
            return await event.reply("❌ Ответь на сообщение Хероку-бота командой `sudo spy`")

        msg = await event.get_reply_message()
        
        out = f"🔍 **АНАЛИЗ СООБЩЕНИЯ**\n\n"
        out += f"**ID:** `{msg.id}`\n"
        out += f"**Invert Media (Фотка сверху?):** `{getattr(msg, 'invert_media', False)}`\n"
        out += f"**Тип медиа:** `{type(msg.media).__name__ if msg.media else 'Нет'}`\n\n"
        
        if msg.entities:
            out += "**Entities:**\n"
            for ent in msg.entities:
                out += f"- `{type(ent).__name__}` (offset: {ent.offset}, len: {ent.length})\n"
        
        # Сохраняем ВЕСЬ сырой код сообщения из MTProto
        raw_tl = msg.stringify()
        with open("spy_dump.txt", "w", encoding="utf-8") as f:
            f.write(raw_tl)
        
        await event.reply(out, file="spy_dump.txt")