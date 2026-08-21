# CODEVER: v4.2 | Sniper Userbot (Zero-Tolerance Ad Engine, Unsolicited URL Trap & Gift Detection)
import os
import re
import sys
import asyncio
import sqlite3
import logging
import subprocess
from collections import deque
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User, MessageEntityTextUrl

# --- БУФЕР ЛОГОВ В ПАМЯТИ ---
class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity=60):
        super().__init__()
        self.buffer = deque(maxlen=capacity)

    def emit(self, record):
        try:
            self.buffer.append(self.format(record))
        except Exception:
            self.handleError(record)

    def get_logs(self, limit=20):
        return list(self.buffer)[-limit:] if self.buffer else []

mem_logs = MemoryLogHandler(capacity=60)
mem_logs.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.addHandler(mem_logs)

OWNER_ID = 5421909121 
LEX_BOT_USERNAME = "my_LEX_superbot"
DB_NAME = "sniper_memory_v3.db"

# --- БЕЛЫЙ СПИСОК НАШИХ БОТОВ ---
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

# --- GIT СИНХРОНИЗАЦИЯ ---
def save_db_to_git():
    try:
        status = subprocess.run(["git", "status", "--porcelain", DB_NAME], capture_output=True, text=True)
        if not status.stdout.strip():
            return
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", DB_NAME], check=True)
        subprocess.run(["git", "commit", "-m", "chore: sync db v4.2 [skip ci]"], check=True)
        subprocess.run(["git", "push"], check=True)
        logging.info("БД сохранена в GitHub.")
    except Exception as e:
        logging.warning(f"Ошибка сохранения в Git: {e}")

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS ad_patterns (id INTEGER PRIMARY KEY AUTOINCREMENT, pattern TEXT UNIQUE)")
    cur.execute("CREATE TABLE IF NOT EXISTS exceptions (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT UNIQUE)")
    cur.execute("CREATE TABLE IF NOT EXISTS banwords (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT UNIQUE, delay INTEGER DEFAULT 0)")
    cur.execute("CREATE TABLE IF NOT EXISTS human_regex (id INTEGER PRIMARY KEY AUTOINCREMENT, pattern TEXT UNIQUE, delay INTEGER DEFAULT 5)")
    cur.execute("CREATE TABLE IF NOT EXISTS trusted_users (user_id INTEGER PRIMARY KEY, username TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS timers (key TEXT PRIMARY KEY, sec INTEGER)")
    
    cur.execute("INSERT OR IGNORE INTO timers (key, sec) VALUES ('rp_delay', 10)")
    cur.execute("INSERT OR IGNORE INTO timers (key, sec) VALUES ('info_delay', 30)")
    
    base_ads = [
        r"t\.me/(\+|joinchat/|addlist/)",
        r"vless|happ\b|shadowsocks|outline",
        r"\b(vpn|впн)\b.*\b(руб|р|₽|мес|месяц|доступ)\b",
        r"\d+\s*(р|₽|руб)[/\s]*(мес|месяц)",
        r"взломали\s+все\s+приложения",
        r"пошл\w+\s+стикер",
        r"(бесплатн\w+|получен|забирай|забери|твой)\s+(подарок|ключ|приз|бонус)",
        r"выбери\s+скорее",
        r"обход\w*\s+(глушилок|белых\s+списков|блокиров)",
        r"чат[,\s]+в\s+который\s+заходят",
        r"добро\s+пожаловать\s+в\s+чат",
    ]
    for ad in base_ads:
        cur.execute("INSERT OR IGNORE INTO ad_patterns (pattern) VALUES (?)", (ad,))

    conn.commit()
    conn.close()

# --- ФУНКЦИИ БД ---
def db_get_timer(key: str, default: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    row = conn.cursor().execute("SELECT sec FROM timers WHERE key = ?", (key,)).fetchone()
    conn.close()
    return int(row[0]) if row else default

def db_set_timer(key: str, sec: int):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT OR REPLACE INTO timers (key, sec) VALUES (?, ?)", (key, sec))
    conn.commit()
    conn.close()
    save_db_to_git()

def db_add_trusted(user_id: int, username: str = ""):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT OR REPLACE INTO trusted_users (user_id, username) VALUES (?, ?)", (user_id, username))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except Exception:
        return False

def db_del_trusted(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM trusted_users WHERE user_id = ?", (user_id,))
    cnt = conn.total_changes
    conn.commit()
    conn.close()
    if cnt: save_db_to_git()
    return cnt > 0

def db_get_trusted():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT user_id, username FROM trusted_users").fetchall()
    conn.close()
    return items

def db_get_trusted_ids() -> set[int]:
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT user_id FROM trusted_users").fetchall()
    conn.close()
    return {i[0] for i in items}

def db_add_ad(pattern: str):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT INTO ad_patterns (pattern) VALUES (?)", (pattern.strip(),))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except Exception:
        return False

def db_get_ads():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT pattern FROM ad_patterns").fetchall()
    conn.close()
    return [i[0] for i in items]

def db_add_exception(phrase: str):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT OR REPLACE INTO exceptions (word) VALUES (?)", (phrase.strip(),))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except Exception as e:
        logging.error(f"Ошибка БД: {e}")
        return False

def db_del_exception(phrase: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM exceptions WHERE LOWER(word) = LOWER(?)", (phrase.strip(),))
    cnt = conn.total_changes
    conn.commit()
    conn.close()
    if cnt: save_db_to_git()
    return cnt > 0

def db_get_exceptions():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT word FROM exceptions").fetchall()
    conn.close()
    return [i[0] for i in items if i[0]]

def db_add_banword(phrase: str, delay: int = 0):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT OR REPLACE INTO banwords (word, delay) VALUES (?, ?)", (phrase.strip(), delay))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except Exception as e:
        logging.error(f"Ошибка БД: {e}")
        return False

def db_del_banword(phrase: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM banwords WHERE LOWER(word) = LOWER(?)", (phrase.strip(),))
    cnt = conn.total_changes
    conn.commit()
    conn.close()
    if cnt: save_db_to_git()
    return cnt > 0

def db_get_banwords():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT word, delay FROM banwords").fetchall()
    conn.close()
    return items

def db_add_human_regex(pattern: str, delay: int = 5):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT OR REPLACE INTO human_regex (pattern, delay) VALUES (?, ?)", (pattern.strip(), delay))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except Exception as e:
        logging.error(f"Ошибка БД: {e}")
        return False

def db_del_human_regex(pattern: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM human_regex WHERE pattern = ?", (pattern.strip(),))
    cnt = conn.total_changes
    conn.commit()
    conn.close()
    if cnt: save_db_to_git()
    return cnt > 0

def db_get_human_regex():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT pattern, delay FROM human_regex").fetchall()
    conn.close()
    return items

def parse_phrase_and_delay(val: str, default_delay: int = 0) -> tuple[str, int]:
    parts = val.strip().split()
    if not parts:
        return "", default_delay
    if len(parts) > 1 and parts[-1].isdigit():
        delay = int(parts[-1])
        phrase = " ".join(parts[:-1]).strip()
        return phrase, delay
    return " ".join(parts).strip(), default_delay

# --- ПРОВЕРКА ПРАВ SUDO ---
async def is_authorized(event) -> bool:
    sender_id = event.sender_id
    if not sender_id:
        return False
    if sender_id == OWNER_ID or sender_id in db_get_trusted_ids():
        return True

    if event.is_group or event.is_channel:
        try:
            perms = await event.client.get_permissions(event.chat_id, sender_id)
            if perms.is_admin or perms.is_creator:
                return True
        except Exception:
            pass
    return False

# --- ОПРЕДЕЛЕНИЕ БОТА ---
def identify_tracked_bot(sender) -> tuple[bool, str]:
    if not sender:
        return False, ""
    
    username = (getattr(sender, 'username', '') or '').lower().lstrip('@')
    first_name = (getattr(sender, 'first_name', '') or '').lower()
    last_name = (getattr(sender, 'last_name', '') or '').lower()
    full_title = f"{first_name} {last_name}".strip()

    if username:
        if username in KNOWN_BOT_USERNAMES or any(b in username for b in KNOWN_BOT_USERNAMES):
            return True, username

    for title_kw in KNOWN_BOT_TITLES:
        if title_kw in full_title:
            return True, username or full_title

    if getattr(sender, 'bot', False):
        for b in KNOWN_BOT_USERNAMES:
            if b in full_title:
                return True, username or full_title

    return False, ""

# --- АНАЛИЗАТОР РЕКЛАМЫ (СВЕРХЧУВСТВИТЕЛЬНЫЙ ДЕТЕКТОР) ---
REGEX_FOREIGN_BOT = re.compile(r"@[a-zA-Z0-9_]{4,}bot\b", re.IGNORECASE)
REGEX_INVITE = re.compile(r"t\.me/(\+|joinchat/|addlist/)", re.IGNORECASE)
REGEX_GIFT_TRAP = re.compile(r"(подарок|подарка|подарки|приз|призы|бонус|выбери\s+скорее|забирай|забери)", re.IGNORECASE)

def is_ad(msg, author_tag: str, is_reply: bool) -> bool:
    text = (msg.raw_text or "") + " " + (msg.message or "")

    # 1. Инвайт-ссылка в любом виде
    if REGEX_INVITE.search(text):
        return True

    # 2. КНОПКИ: Если сообщение БЕЗ реплая и имеет хотя бы одну URL-ссылку (со стрелочкой ↗)
    # В играх/РП кнопки ВСЕГДА callback_data (без url). URL-кнопка без реплая = 100% РЕКЛАМА!
    if msg.buttons:
        for row in msg.buttons:
            for btn in row:
                if btn.url:
                    u = btn.url.lower()
                    # Если без реплая есть любая ссылка в кнопке -> это спам-ловушка
                    if not is_reply:
                        return True
                    # Если есть инвайт или сторонний бот
                    if "t.me/+" in u or "joinchat" in u:
                        return True
                    for bu in REGEX_FOREIGN_BOT.findall(u):
                        b_clean = bu.lstrip('@').lower()
                        if b_clean not in KNOWN_BOT_USERNAMES and b_clean not in author_tag.lower():
                            return True

    # 3. Упоминание чужих ботов в тексте
    for m in REGEX_FOREIGN_BOT.findall(text):
        bot_clean = m.lstrip('@').lower()
        if bot_clean not in KNOWN_BOT_USERNAMES and bot_clean not in author_tag.lower():
            return True

    # 4. Скрытые ссылки под текстом или эмодзи
    if msg.entities:
        for ent in msg.entities:
            if isinstance(ent, MessageEntityTextUrl) and ent.url:
                if not is_reply or REGEX_INVITE.search(ent.url):
                    return True

    # 5. Ловушки подарков/призов без реплая
    if not is_reply and REGEX_GIFT_TRAP.search(text) and (msg.buttons or "👇" in text or "👉" in text):
        return True

    # 6. Спам-паттерны из базы
    for pat in db_get_ads():
        try:
            if re.search(pat, text, re.IGNORECASE):
                return True
        except re.error:
            pass

    return False

def classify_bot_message(event, author_tag: str) -> int | None:
    msg = event.message
    text = (msg.raw_text or "").strip()
    norm_text = " ".join(text.lower().split())
    tag_lower = author_tag.lower()
    is_reply = bool(event.is_reply or getattr(msg, 'reply_to_msg_id', None))

    # =========================================================================
    # ПРИОРИТЕТ 1: АБСОЛЮТНЫЙ ИММУНИТЕТ (МУЗЫКА, СБЕР, БЕЛЫЙ СПИСОК)
    # =========================================================================
    
    # Музыкальный бот (только если это аудио или ответ)
    if "vkmusic" in tag_lower or "музык" in tag_lower or "музон" in tag_lower:
        if msg.audio or is_reply or getattr(msg, 'via_bot_id', None) or "via @" in text.lower():
            return None

    # Любой аудиофайл:
    if msg.audio:
        return None

    # Сбер текстовик (SaluteSpeech):
    if "smartspeech" in tag_lower or "sber" in tag_lower or "salute" in tag_lower:
        return None

    # Белый список исключений
    for exc in db_get_exceptions():
        norm_exc = " ".join(exc.lower().split())
        if norm_exc in norm_text:
            return None

    # =========================================================================
    # ПРИОРИТЕТ 2: ЧИСТАЯ РЕКЛАМА И СПАМ-ЛОВУШКИ (Снос 0 сек)
    # =========================================================================
    if is_ad(msg, author_tag, is_reply):
        return 0

    # =========================================================================
    # ПРИОРИТЕТ 3: БАНВОРДЫ
    # =========================================================================
    for b_phrase, b_delay in db_get_banwords():
        norm_phrase = " ".join(b_phrase.lower().split())
        if norm_phrase in norm_text:
            return b_delay

    # =========================================================================
    # ПРИОРИТЕТ 4: ДЛИННЫЕ МЕНЮ / ТОПЫ / СТАТЬИ (30 сек)
    # =========================================================================
    lines_count = len(text.split('\n'))
    has_large_buttons = bool(msg.buttons and len(msg.buttons) >= 2)
    is_long_text = len(text) > 250 or lines_count >= 5 or "teletype.in" in text.lower()

    if is_long_text or has_large_buttons:
        return db_get_timer('info_delay', 30)

    # =========================================================================
    # ПРИОРИТЕТ 5: РП-КОМАНДЫ И ДЕЙСТВИЯ (10 сек)
    # =========================================================================
    return db_get_timer('rp_delay', 10)

def extract_pattern(msg, author_tag: str) -> str:
    text = (msg.raw_text or "").strip()
    m_inv = re.search(r"(https?://)?t\.me/(\+[a-zA-Z0-9_\-]+)", text)
    if m_inv:
        return re.escape(m_inv.group(0))

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
        if words:
            return r"\s+".join([re.escape(w) for w in words])

    return re.escape(text[:30])

# --- ТЕЛЕТОН КЛИЕНТ ---
API_ID = int(os.getenv("API_ID", 0)) if os.getenv("API_ID") else None
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

async def delete_after(event, delay: int, label: str):
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        await event.delete()
        logging.info(f"🗑 [Успешно удалено: {delay}с] {label}")
    except Exception as e:
        logging.warning(f"Ошибка удаления: {e}")

# --- ОБРАБОТЧИК СООБЩЕНИЙ ЧАТА ---
@client.on(events.NewMessage(incoming=True, func=lambda e: not e.is_private))
async def main_handler(event):
    msg = event.message
    text = (msg.raw_text or "").strip()

    sender = None
    try:
        sender = await event.get_sender()
    except Exception:
        pass

    if not sender and event.sender_id:
        try:
            sender = await client.get_entity(event.sender_id)
        except Exception:
            pass

    sender_username = (getattr(sender, 'username', '') or '').lower()
    if sender_username == LEX_BOT_USERNAME.lower() or text.lower().startswith("sudo"):
        return

    is_bot = getattr(sender, 'bot', False) if isinstance(sender, User) else False

    # 1. ЛЮДИ (Регексы)
    if not is_bot:
        for pattern, delay in db_get_human_regex():
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    logging.info(f"🚨 [Человек] Регекс '{pattern}' -> Удаление через {delay}с")
                    asyncio.create_task(delete_after(event, delay, f"Human reg: {pattern}"))
                    return
            except re.error:
                pass
        return

    # 2. БОТЫ
    is_tracked, bot_tag = identify_tracked_bot(sender)
    if not is_tracked:
        return

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

# --- ОБРАБОТЧИК SUDO КОМАНД ---
@client.on(events.NewMessage(pattern=r"^sudo(\s.*|$)"))
async def sudo_handler(event):
    if not await is_authorized(event):
        return

    parts = event.raw_text.split()
    cmd = parts[1].lower() if len(parts) > 1 else ""
    val = " ".join(parts[2:]) if len(parts) > 2 else ""

    if not cmd:
        rp_t = db_get_timer('rp_delay', 10)
        info_t = db_get_timer('info_delay', 30)
        help_text = (
            "🎯 **Sniper Userbot v4.2 — Панель управления**\n\n"
            "🚨 **Реклама:**\n"
            "• `sudo спам` *(в реплай)* — Снести рекламу и обучить фильтр\n\n"
            "🛡️ **Исключения (Иммунитет):**\n"
            "• `sudo +искл {фраза}` *(или реплай)* — Добавить исключение\n"
            "• `sudo -искл {фраза}` / `sudo исклы` — Удалить / Список\n\n"
            "🚫 **Банворды (Фразы от 1 до 10 слов):**\n"
            "• `sudo +бан {фраза} [сек=0]` — Добавить бан-фразу\n"
            "• `sudo -бан {фраза}` / `sudo баны` — Удалить / Список\n\n"
            "🧹 **Регексы (Люди):**\n"
            "• `sudo +рег {паттерн} [сек=5]` — Добавить регекс\n"
            "• `sudo -рег {паттерн}` / `sudo регексы` — Удалить / Список\n\n"
            "👥 **Доверенные пользователи:**\n"
            "• `sudo +дов` *(реплай/@user/ID)* — Дать доступ к sudo\n"
            "• `sudo -дов` *(реплай/@user/ID)* — Забрать доступ\n"
            "• `sudo доверенные` — Список доверенных\n\n"
            "⏱️ **Таймеры:**\n"
            f"• `sudo рп {rp_t}` *(сейчас: {rp_t}с)* | `sudo инфо {info_t}` *(сейчас: {info_t}с)*\n"
            "• `sudo лог` — Посмотреть последние события"
        )
        return await event.reply(help_text)

    try:
        # 1. СПАМ
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

        # 2. ИСКЛЮЧЕНИЯ
        elif cmd in ["+искл", "+exc"]:
            phrase = val.strip()
            if event.is_reply and not phrase:
                target = await event.get_reply_message()
                phrase = (target.raw_text or "").strip()
            
            if not phrase:
                return await event.reply("❌ Укажи слово/фразу: `sudo +искл {фраза}` или ответь на сообщение.")
            
            if db_add_exception(phrase):
                await event.reply(f"🛡️ Фраза `{phrase}` добавлена в белый список (Вечный иммунитет)!")
            else:
                await event.reply("⚠️ Ошибка сохранения в базу.")

        elif cmd in ["-искл", "-exc"] and val:
            if db_del_exception(val): 
                await event.reply(f"🗑 Исключение `{val}` удалено.")
            else: 
                await event.reply("❌ Исключение не найдено.")

        elif cmd in ["исклы", "excs", "исключения"]:
            items = db_get_exceptions()
            await event.reply("🛡️ **Список исключений:**\n" + ("\n".join([f"• `{i}`" for i in items]) if items else "Пусто."))

        # 3. БАНВОРДЫ
        elif cmd in ["+бан", "+ban"] and val:
            phrase, delay = parse_phrase_and_delay(val, default_delay=0)
            if not phrase:
                return await event.reply("❌ Укажи фразу: `sudo +бан {фраза} [сек]`")
            
            if db_add_banword(phrase, delay):
                await event.reply(f"🚫 Бан-фраза `{phrase}` добавлена (снос через **{delay}с**).")
            else:
                await event.reply("⚠️ Ошибка сохранения.")

        elif cmd in ["-бан", "-ban"] and val:
            phrase, _ = parse_phrase_and_delay(val, default_delay=0)
            if db_del_banword(phrase): 
                await event.reply(f"🗑 Бан-фраза `{phrase}` удалена.")
            else: 
                await event.reply("❌ Бан-фраза не найдена.")

        elif cmd in ["баны", "bans", "банворды"]:
            items = db_get_banwords()
            await event.reply("🚫 **Список бан-фраз:**\n" + ("\n".join([f"• `{w}` ({d}с)" for w, d in items]) if items else "Пусто."))

        # 4. РЕГЕКСЫ
        elif cmd in ["+рег", "+reg"] and val:
            pattern, delay = parse_phrase_and_delay(val, default_delay=5)
            if not pattern:
                return await event.reply("❌ Укажи паттерн: `sudo +рег {паттерн} [сек]`")
            
            if db_add_human_regex(pattern, delay):
                await event.reply(f"🧹 Регекс `{pattern}` добавлен (таймаут: **{delay}с**).")
            else:
                await event.reply("⚠️ Ошибка сохранения.")

        elif cmd in ["-рег", "-reg"] and val:
            pattern, _ = parse_phrase_and_delay(val, default_delay=5)
            if db_del_human_regex(pattern): 
                await event.reply(f"🗑 Регекс `{pattern}` удален.")
            else: 
                await event.reply("❌ Регекс не найден.")

        elif cmd in ["регексы", "regs"]:
            items = db_get_human_regex()
            await event.reply("🧹 **Регексы людей:**\n" + ("\n".join([f"• `{p}` ({d}с)" for p, d in items]) if items else "Пусто."))

        # 5. ДОВЕРЕННЫЕ
        elif cmd in ["+дов", "+trust"]:
            target_user = None
            if event.is_reply:
                rep = await event.get_reply_message()
                target_user = await rep.get_sender()
            elif val:
                try:
                    target_user = await client.get_entity(int(val) if val.isdigit() or val.startswith("-") else val)
                except Exception:
                    pass

            if target_user:
                uid = target_user.id
                u_name = getattr(target_user, 'username', '') or getattr(target_user, 'first_name', str(uid))
                db_add_trusted(uid, u_name)
                await event.reply(f"✅ Пользователь **{u_name}** (`{uid}`) добавлен в доверенные!")
            else:
                await event.reply("❌ Укажи пользователя: ответь на его смс или напиши `sudo +дов @username` / `ID`.")

        elif cmd in ["-дов", "-trust"]:
            uid = None
            if event.is_reply:
                rep = await event.get_reply_message()
                uid = rep.sender_id
            elif val.isdigit():
                uid = int(val)
            elif val:
                try:
                    ent = await client.get_entity(val)
                    uid = ent.id
                except Exception:
                    pass

            if uid and db_del_trusted(uid):
                await event.reply(f"🗑 Пользователь `{uid}` удален из доверенных.")
            else:
                await event.reply("❌ Пользователь не найден.")

        elif cmd in ["доверенные", "дов", "trusted"]:
            items = db_get_trusted()
            text = "👥 **Доверенные пользователи:**\n\n"
            for uid, uname in items:
                text += f"• {uname} (`{uid}`)\n"
            await event.reply(text if items else "Список доверенных пуст (работают владелец и админы группы).")

        # 6. ТАЙМЕРЫ
        elif cmd in ["рп", "rp"] and val.isdigit():
            db_set_timer('rp_delay', int(val))
            await event.reply(f"⏱️ Таймер РП-команд: **{val}** секунд.")

        elif cmd in ["инфо", "info"] and val.isdigit():
            db_set_timer('info_delay', int(val))
            await event.reply(f"⏱️ Таймер длинных инфо/меню: **{val}** секунд.")

        # 7. ЛОГИ
        elif cmd in ["лог", "log", "logs"]:
            logs = mem_logs.get_logs(18)
            text = "\n".join(logs) if logs else "Логи пусты."
            await event.reply(f"📜 **Последние события:**\n\n```text\n{text}\n```")

    except Exception as e:
        await event.reply(f"⚠️ Ошибка: `{e}`")

# --- СТАРТ ---
async def main():
    if not all([API_ID, API_HASH, SESSION_STRING]):
        logging.critical("ОШИБКА: Заполните переменные API_ID, API_HASH, SESSION_STRING!")
        return
    init_db()
    await client.start()
    me = await client.get_me()
    logging.info(f"Sniper v4.2 успешно запущен! Аккаунт: {me.first_name} (@{me.username})")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
