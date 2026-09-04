# modules/sniper.py
import re
import asyncio
import logging
from telethon import events
from telethon.tl.types import User, MessageEntityTextUrl
from core.config import USERBOT_NAME, VERSION
from core.db import (
    db_get_timer, db_set_timer, db_add_trusted, db_del_trusted, 
    db_get_trusted, db_add_ad, db_get_ads, db_add_exception, 
    db_del_exception, db_get_exceptions, db_add_banword, db_del_banword, 
    db_get_banwords, db_add_human_regex, db_del_human_regex, db_get_human_regex,
    is_authorized, mem_logs
)

KNOWN_BOT_USERNAMES = {
    "celya", "smartspeech_sber_bot", "vkmusicalrobot", 
    "vkmusictopbot", "polegamebot", "shmalala_bot", 
    "truemafiabot", "quizbot", "gram_piarbot", 
    "thelacosterobot", "givesharebot", "igravgorodabotbot", 
    "iris_black_bot", "iris_cm_bot", "iris_dp_bot", "iris_bot"
}

KNOWN_BOT_TITLES = [
    "celestiana", "salutespeech", "sber", "vk music", "музыка из вк", 
    "поле чудес", "iris", "ирис", "мафия", "quiz", "музон", "siren"
]

REGEX_FOREIGN_BOT = re.compile(r"@[a-zA-Z0-9_]{4,}bot\b", re.IGNORECASE)
REGEX_INVITE = re.compile(r"t\.me/(\+|joinchat/|addlist/)", re.IGNORECASE)
REGEX_GIFT_TRAP = re.compile(r"(подарок|подарка|подарки|приз|призы|бонус|выбери\s+скорее|забирай|забери)", re.IGNORECASE)

def parse_phrase_and_delay(val: str, default_delay: int = 0) -> tuple[str, int]:
    parts = val.strip().split()
    if not parts:
        return "", default_delay
    if len(parts) > 1 and parts[-1].isdigit():
        return " ".join(parts[:-1]).strip(), int(parts[-1])
    return " ".join(parts).strip(), default_delay

def identify_tracked_bot(sender) -> tuple[bool, str]:
    if not sender:
        return False, ""
    username = (getattr(sender, 'username', '') or '').lower().lstrip('@')
    first_name = (getattr(sender, 'first_name', '') or '').lower()
    last_name = (getattr(sender, 'last_name', '') or '').lower()
    full_title = f"{first_name} {last_name}".strip()

    if username and (username in KNOWN_BOT_USERNAMES or any(b in username for b in KNOWN_BOT_USERNAMES)):
        return True, username
    for title_kw in KNOWN_BOT_TITLES:
        if title_kw in full_title:
            return True, username or full_title
    if getattr(sender, 'bot', False):
        for b in KNOWN_BOT_USERNAMES:
            if b in full_title:
                return True, username or full_title
    return False, ""

def is_ad(msg, author_tag: str, is_reply: bool) -> bool:
    text = (msg.raw_text or "") + " " + (msg.message or "")
    if REGEX_INVITE.search(text): return True
    if msg.buttons:
        for row in msg.buttons:
            for btn in row:
                if btn.url:
                    u = btn.url.lower()
                    if not is_reply: return True
                    if "t.me/+" in u or "joinchat" in u: return True
                    for bu in REGEX_FOREIGN_BOT.findall(u):
                        b_clean = bu.lstrip('@').lower()
                        if b_clean not in KNOWN_BOT_USERNAMES and b_clean not in author_tag.lower():
                            return True
    for m in REGEX_FOREIGN_BOT.findall(text):
        bot_clean = m.lstrip('@').lower()
        if bot_clean not in KNOWN_BOT_USERNAMES and bot_clean not in author_tag.lower():
            return True
    if msg.entities:
        for ent in msg.entities:
            if isinstance(ent, MessageEntityTextUrl) and ent.url:
                if not is_reply or REGEX_INVITE.search(ent.url): return True
    if not is_reply and REGEX_GIFT_TRAP.search(text) and (msg.buttons or "👇" in text or "👉" in text):
        return True
    for pat in db_get_ads():
        try:
            if re.search(pat, text, re.IGNORECASE): return True
        except re.error: pass
    return False

def classify_bot_message(event, author_tag: str) -> int | None:
    msg = event.message
    text = (msg.raw_text or "").strip()
    norm_text = " ".join(text.lower().split())
    tag_lower = author_tag.lower()
    is_reply = bool(event.is_reply or getattr(msg, 'reply_to_msg_id', None))

    for exc in db_get_exceptions():
        if " ".join(exc.lower().split()) in norm_text: return None

    if "iris" in tag_lower or "ирис" in tag_lower:
        lines_count = len(text.split('\n'))
        has_large_buttons = bool(msg.buttons and len(msg.buttons) >= 2)
        if len(text) > 250 or lines_count >= 5 or has_large_buttons:
            return db_get_timer('info_delay', 30)
        return db_get_timer('rp_delay', 10)

    if ("vkmusic" in tag_lower or "музык" in tag_lower or "музон" in tag_lower) and (msg.audio or is_reply or getattr(msg, 'via_bot_id', None) or "via @" in text.lower()):
        return None
    if msg.audio or "smartspeech" in tag_lower or "sber" in tag_lower or "salute" in tag_lower:
        return None

    if is_ad(msg, author_tag, is_reply): return 0

    for b_phrase, b_delay in db_get_banwords():
        if " ".join(b_phrase.lower().split()) in norm_text: return b_delay

    lines_count = len(text.split('\n'))
    has_large_buttons = bool(msg.buttons and len(msg.buttons) >= 2)
    if len(text) > 250 or lines_count >= 5 or "teletype.in" in text.lower() or has_large_buttons:
        return db_get_timer('info_delay', 30)

    return db_get_timer('rp_delay', 10)

def extract_pattern(msg, author_tag: str) -> str:
    text = (msg.raw_text or "").strip()
    m_inv = re.search(r"(https?://)?t\.me/(\+[a-zA-Z0-9_\-]+)", text)
    if m_inv: return re.escape(m_inv.group(0))

    mentions = REGEX_FOREIGN_BOT.findall(text)
    for m in mentions:
        if m.lstrip('@').lower() not in KNOWN_BOT_USERNAMES and m.lstrip('@').lower() not in author_tag.lower():
            return rf"{re.escape(m)}\b"

    if msg.buttons:
        for row in msg.buttons:
            for btn in row:
                if btn.url and ("t.me/" in btn.url or "http" in btn.url):
                    return re.escape(btn.url)

    lines = [l.strip() for l in text.split('\n') if len(l.strip().split()) >= 2]
    if lines:
        clean = re.sub(r"[^\w\sа-яА-ЯёЁa-zA-Z0-9]", "", lines[0]).strip()
        words = clean.split()[:4]
        if words: return r"\s+".join([re.escape(w) for w in words])

    return re.escape(text[:30])

async def delete_after(event, delay: int, label: str):
    try:
        if delay > 0: await asyncio.sleep(delay)
        await event.delete()
        logging.info(f"🗑 [Успешно удалено: {delay}с] {label}")
    except Exception as e:
        logging.warning(f"Ошибка удаления: {e}")

# --- ФУНКЦИЯ РЕГИСТРАЦИИ МОДУЛЯ ---
def register(client):
    @client.on(events.NewMessage(incoming=True, func=lambda e: not e.is_private))
    async def sniper_chat_handler(event):
        msg = event.message
        text = (msg.raw_text or "").strip()
        
        # Игнорируем команды sudo и точки
        if text.lower().startswith("sudo") or text.startswith("."):
            return

        sender = None
        try: sender = await event.get_sender()
        except Exception: pass
        if not sender and event.sender_id:
            try: sender = await client.get_entity(event.sender_id)
            except Exception: pass

        is_bot = getattr(sender, 'bot', False) if isinstance(sender, User) else False

        # 1. Люди
        if not is_bot:
            for pattern, delay in db_get_human_regex():
                try:
                    if re.search(pattern, text, re.IGNORECASE):
                        logging.info(f"🚨 [Человек] Регекс '{pattern}' -> Удаление через {delay}с")
                        asyncio.create_task(delete_after(event, delay, f"Human reg: {pattern}"))
                        return
                except re.error: pass
            return

        # 2. Боты
        is_tracked, bot_tag = identify_tracked_bot(sender)
        if not is_tracked: return

        logging.info(f"🤖 Сообщение от бота [{bot_tag}]: '{text[:35].replace(chr(10), ' ')}...'")
        delay = classify_bot_message(event, bot_tag)

        if delay is None:
            logging.info(f"🛡️ [{bot_tag}] Сообщение с вечным иммунитетом.")
            return

        if delay == 0:
            logging.info(f"💥 [{bot_tag}] РЕКЛАМА/ЛОВУШКА -> Мгновенный снос (0с)")
            asyncio.create_task(delete_after(event, 0, f"Реклама [{bot_tag}]"))
        elif delay <= 15:
            logging.info(f"🎭 [{bot_tag}] РП / действие -> Удаление через {delay}с")
            asyncio.create_task(delete_after(event, delay, f"РП [{bot_tag}]"))
        else:
            logging.info(f"📋 [{bot_tag}] Инфо / меню / топ -> Удаление через {delay}с")
            asyncio.create_task(delete_after(event, delay, f"Инфо [{bot_tag}]"))

    @client.on(events.NewMessage(pattern=r"^sudo(\s.*|$)"))
    async def sniper_sudo_handler(event):
        if not await is_authorized(event): return

        parts = event.raw_text.split()
        cmd = parts[1].lower() if len(parts) > 1 else ""
        val = " ".join(parts[2:]) if len(parts) > 2 else ""

        if not cmd:
            rp_t = db_get_timer('rp_delay', 10)
            info_t = db_get_timer('info_delay', 30)
            help_text = (
                f"🪐 **{USERBOT_NAME} {VERSION} — Панель управления**\n\n"
                "🎛️ **Медиа и Стикеры:**\n"
                "• `sudo медиа` — Меню Media Studio\n"
                "• `sudo цитата` — Создать 3D-стикер цитату\n\n"
                "🚨 **Реклама:**\n"
                "• `sudo спам` *(в реплай)* — Снести рекламу и обучить фильтр\n\n"
                "🛡️ **Исключения:**\n"
                "• `sudo +искл {фраза}` / `sudo -искл {фраза}` / `sudo исклы`\n\n"
                "🚫 **Банворды:**\n"
                "• `sudo +бан {фраза} [сек]` / `sudo -бан {фраза}` / `sudo баны`\n\n"
                "🧹 **Регексы (Люди):**\n"
                "• `sudo +рег {паттерн} [сек]` / `sudo -рег {паттерн}` / `sudo регексы`\n\n"
                "👥 **Доверенные:**\n"
                "• `sudo +дов` / `sudo -дов` / `sudo доверенные`\n\n"
                "⏱️ **Таймеры:**\n"
                f"• `sudo рп {rp_t}` *(сейчас: {rp_t}с)* | `sudo инфо {info_t}` *(сейчас: {info_t}с)*\n"
                "• `sudo лог` — Посмотреть последние события"
            )
            return await event.reply(help_text)

        try:
            if cmd in ["спам", "ad", "реклама", "бан"]:
                if not event.is_reply:
                    return await event.reply("⚠️ Ответь командой `sudo спам` **на сообщение с рекламой**.")
                target = await event.get_reply_message()
                t_sender = await target.get_sender()
                _, b_tag = identify_tracked_bot(t_sender)
                pat = extract_pattern(target, b_tag or "bot")
                db_add_ad(pat)
                await target.delete()
                await event.delete()
                c = await event.respond(f"🎯 **Реклама уничтожена!** Паттерн:\n`{pat}`")
                await asyncio.sleep(4)
                await c.delete()

            elif cmd in ["+искл", "+exc"]:
                phrase = val.strip() or ((await event.get_reply_message()).raw_text if event.is_reply else "")
                if not phrase: return await event.reply("❌ Укажи слово/фразу.")
                if db_add_exception(phrase): await event.reply(f"🛡️ `{phrase}` в белом списке!")

            elif cmd in ["-искл", "-exc"] and val:
                if db_del_exception(val): await event.reply(f"🗑 Исключение `{val}` удалено.")
                else: await event.reply("❌ Не найдено.")

            elif cmd in ["исклы", "excs", "исключения"]:
                items = db_get_exceptions()
                await event.reply("🛡️ **Список исключений:**\n" + ("\n".join([f"• `{i}`" for i in items]) if items else "Пусто."))

            elif cmd in ["+бан", "+ban"] and val:
                phrase, delay = parse_phrase_and_delay(val, default_delay=0)
                if db_add_banword(phrase, delay): await event.reply(f"🚫 Бан-фраза `{phrase}` добавлена ({delay}с).")

            elif cmd in ["-бан", "-ban"] and val:
                phrase, _ = parse_phrase_and_delay(val, default_delay=0)
                if db_del_banword(phrase): await event.reply(f"🗑 Бан-фраза `{phrase}` удалена.")

            elif cmd in ["баны", "bans", "банворды"]:
                items = db_get_banwords()
                await event.reply("🚫 **Список бан-фраз:**\n" + ("\n".join([f"• `{w}` ({d}с)" for w, d in items]) if items else "Пусто."))

            elif cmd in ["+рег", "+reg"] and val:
                pattern, delay = parse_phrase_and_delay(val, default_delay=5)
                if db_add_human_regex(pattern, delay): await event.reply(f"🧹 Регекс `{pattern}` добавлен ({delay}с).")

            elif cmd in ["-рег", "-reg"] and val:
                pattern, _ = parse_phrase_and_delay(val, default_delay=5)
                if db_del_human_regex(pattern): await event.reply(f"🗑 Регекс `{pattern}` удален.")

            elif cmd in ["регексы", "regs"]:
                items = db_get_human_regex()
                await event.reply("🧹 **Регексы людей:**\n" + ("\n".join([f"• `{p}` ({d}с)" for p, d in items]) if items else "Пусто."))

            elif cmd in ["+дов", "+trust"]:
                target_user = (await (await event.get_reply_message()).get_sender()) if event.is_reply else None
                if not target_user and val:
                    try: target_user = await client.get_entity(int(val) if val.isdigit() or val.startswith("-") else val)
                    except Exception: pass
                if target_user:
                    uid = target_user.id
                    u_name = getattr(target_user, 'username', '') or getattr(target_user, 'first_name', str(uid))
                    db_add_trusted(uid, u_name)
                    await event.reply(f"✅ Пользователь **{u_name}** (`{uid}`) добавлен в доверенные!")
                else: await event.reply("❌ Укажи пользователя.")

            elif cmd in ["-дов", "-trust"]:
                uid = (await event.get_reply_message()).sender_id if event.is_reply else (int(val) if val.isdigit() else None)
                if uid and db_del_trusted(uid): await event.reply(f"🗑 Пользователь `{uid}` удален.")
                else: await event.reply("❌ Пользователь не найден.")

            elif cmd in ["доверенные", "дов", "trusted"]:
                items = db_get_trusted()
                text = "👥 **Доверенные пользователи:**\n\n" + "\n".join([f"• {uname} (`{uid}`)" for uid, uname in items])
                await event.reply(text if items else "Список пуст.")

            elif cmd in ["рп", "rp"] and val.isdigit():
                db_set_timer('rp_delay', int(val))
                await event.reply(f"⏱️ Таймер РП: **{val}**с.")

            elif cmd in ["инфо", "info"] and val.isdigit():
                db_set_timer('info_delay', int(val))
                await event.reply(f"⏱️ Таймер инфо: **{val}**с.")

            elif cmd in ["лог", "log", "logs"]:
                logs = mem_logs.get_logs(18)
                await event.reply(f"📜 **Последние события:**\n\n```text\n" + ("\n".join(logs) if logs else "Пусто.") + "\n```")

        except Exception as e:
            await event.reply(f"⚠️ Ошибка: `{e}`")
