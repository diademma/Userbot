# CODEVER: v3.0 | Sniper Userbot (Smart Heuristic, Structural Ad-Detection & Self-Learning)
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
from telethon.tl.types import User, MessageEntityTextUrl, MessageEntityMentionName

# --- БУФЕР ЛОГОВ В ПАМЯТИ ---
class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity=60):
        super().__init__()
        self.buffer = deque(maxlen=capacity)

    def emit(self, record):
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            self.handleError(record)

    def get_logs(self, limit=20):
        logs = list(self.buffer)
        return logs[-limit:] if logs else []

memory_log_handler = MemoryLogHandler(capacity=60)
memory_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.addHandler(memory_log_handler)

OWNER_ID = 5421909121 
LEX_BOT_USERNAME = "my_LEX_superbot"
DB_NAME = "sniper_memory_v3.db"

# --- СОХРАНЕНИЕ БД В GITHUB ---
def save_db_to_git():
    try:
        status = subprocess.run(["git", "status", "--porcelain", DB_NAME], capture_output=True, text=True)
        if not status.stdout.strip():
            return

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
        
        subprocess.run(["git", "add", DB_NAME], check=True)
        subprocess.run(["git", "commit", "-m", "chore: update sniper v3 memory [skip ci]"], check=True)
        subprocess.run(["git", "push"], check=True)
        logging.info("База данных v3 успешно закоммичена в GitHub.")
    except Exception as e:
        logging.warning(f"Не удалось сохранить БД в Git: {e}")

# --- ИНИЦИАЛИЗАЦИЯ И ДЕФОЛТНЫЙ ПРЕСЕТ ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    # Таблица правил для ботов (mode: 'ad_scan', 'ttl_all', 'ignore')
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_profiles (
            username TEXT PRIMARY KEY,
            mode TEXT DEFAULT 'ad_scan',
            default_ttl INTEGER DEFAULT 0
        )
    """)

    # Таблица сигнатур рекламы (регексы, фразы, ссылки)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ad_signatures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT UNIQUE,
            description TEXT
        )
    """)

    # Таблица регексов для людей (команды чистки)
    cur.execute("CREATE TABLE IF NOT EXISTS human_regex (id INTEGER PRIMARY KEY AUTOINCREMENT, pattern TEXT UNIQUE, delay INTEGER DEFAULT 5)")
    
    # Таблица белого списка (слов-исключений)
    cur.execute("CREATE TABLE IF NOT EXISTS exceptions (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT UNIQUE)")
    
    # Настройки
    cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('delay_user_command', '5')")

    # --- ПРЕДУСТАНОВКА ВАШИХ БОТОВ ---
    default_bots = [
        ("celya", "ad_scan", 0),
        ("smartspeech_sber_bot", "ad_scan", 0),
        ("VKMusicalRobot", "ad_scan", 0),
        ("VKmusicTopbot", "ad_scan", 0),
        ("PoleGameBot", "ad_scan", 180),       # чистит старые ходы игры через 3 мин
        ("shmalala_bot", "ad_scan", 0),
        ("TrueMafiaBot", "ad_scan", 0),
        ("QuizBot", "ad_scan", 0),
        ("gram_piarbot", "ad_scan", 0),
        ("THELACOSTEROBOT", "ad_scan", 0),
        ("GiveShareBot", "ad_scan", 0),
        ("igravgorodabotbot", "ad_scan", 0),
        ("iris_black_bot", "ttl_all", 60),     # ИРИС: удалять всё через 60 сек
    ]
    for b_user, b_mode, b_ttl in default_bots:
        cur.execute("INSERT OR IGNORE INTO bot_profiles (username, mode, default_ttl) VALUES (?, ?, ?)", 
                    (b_user.lower(), b_mode, b_ttl))

    # --- ПРЕДУСТАНОВКА БАЗОВЫХ СИГНАТУР РЕКЛАМЫ (ИЗ ВАШИХ СКРИНОВ) ---
    default_signatures = [
        (r"t\.me/(\+|joinchat/|addlist/)", "Инвайт-ссылка на канал/чат"),
        (r"vless|happ\b|shadowsocks|outline", "VPN протоколы/клиенты"),
        (r"\b(vpn|впн)\b.*\b(руб|р|₽|мес|месяц|доступ|ловит)\b", "VPN коммерция"),
        (r"\d+\s*(р|₽|руб)[/\s]*(мес|месяц)", "Ценник за месяц подписки"),
        (r"взломали\s+все\s+приложения", "Спам взлома приложений"),
        (r"пошл\w+\s+стикер", "Спам 18+ стикеров"),
        (r"бесплатн\w+\s+(подарок|ключ)", "Ловушки подарков и ключей"),
        (r"обход\w*\s+(глушилок|белых\s+списков|блокиров)", "Схемы обхода глушилок"),
        (r"чат[,\s]+в\s+который\s+заходят\s+«на\s+минутку»", "Рекламный шаблон чата"),
        (r"добро\s+пожаловать\s+в\s+чат\s+«берег", "Рекламный шаблон Берег Мечты"),
    ]
    for pattern, desc in default_signatures:
        cur.execute("INSERT OR IGNORE INTO ad_signatures (pattern, description) VALUES (?, ?)", (pattern, desc))

    conn.commit()
    conn.close()
    logging.info("База данных v3 инициализирована.")

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С БД ---
def db_get_bot_profile(username: str):
    conn = sqlite3.connect(DB_NAME)
    row = conn.cursor().execute("SELECT mode, default_ttl FROM bot_profiles WHERE username = ?", (username.lower(),)).fetchone()
    conn.close()
    return (row[0], row[1]) if row else (None, None)

def db_set_bot_profile(username: str, mode: str, ttl: int):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT OR REPLACE INTO bot_profiles (username, mode, default_ttl) VALUES (?, ?, ?)",
                 (username.lower().lstrip('@'), mode, ttl))
    conn.commit()
    conn.close()
    save_db_to_git()

def db_del_bot_profile(username: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM bot_profiles WHERE username = ?", (username.lower().lstrip('@'),))
    cnt = conn.total_changes
    conn.commit()
    conn.close()
    if cnt: save_db_to_git()
    return cnt > 0

def db_list_bots():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT username, mode, default_ttl FROM bot_profiles").fetchall()
    conn.close()
    return items

def db_add_ad_signature(pattern: str, desc: str = "Ручное/Авто добавление"):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT INTO ad_signatures (pattern, description) VALUES (?, ?)", (pattern.strip(), desc))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except sqlite3.IntegrityError:
        return False

def db_del_ad_signature(sig_id_or_pattern: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if sig_id_or_pattern.isdigit():
        cur.execute("DELETE FROM ad_signatures WHERE id = ?", (int(sig_id_or_pattern),))
    else:
        cur.execute("DELETE FROM ad_signatures WHERE pattern = ?", (sig_id_or_pattern.strip(),))
    cnt = conn.total_changes
    conn.commit()
    conn.close()
    if cnt: save_db_to_git()
    return cnt > 0

def db_list_ad_signatures():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT id, pattern, description FROM ad_signatures").fetchall()
    conn.close()
    return items

def db_get_exceptions():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT word FROM exceptions").fetchall()
    conn.close()
    return [i[0] for i in items if i[0]]

def db_add_exception(word: str):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT INTO exceptions (word) VALUES (?, ?)", (word.strip(),))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except Exception:
        return False

def db_del_exception(word: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM exceptions WHERE word = ?", (word.strip(),))
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

def db_add_human_regex(pattern: str, delay: int = 5):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT OR REPLACE INTO human_regex (pattern, delay) VALUES (?, ?)", (pattern.strip(), delay))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except Exception:
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

# --- СТРУКТУРНЫЙ АНАЛИЗАТОР РЕКЛАМЫ (ДЕТЕРМИНИРОВАННЫЙ ДВИЖОК) ---

REGEX_FOREIGN_BOT = re.compile(r"@[a-zA-Z0-9_]{4,}bot\b", re.IGNORECASE)
REGEX_INVITE = re.compile(r"t\.me/(\+|joinchat/|addlist/)", re.IGNORECASE)

def detect_ad_reason(message, author_username: str) -> str | None:
    """
    Возвращает строку с причиной, если сообщение — реклама. Иначе None.
    """
    text = (message.raw_text or "") + " " + (message.message or "")
    author_clean = author_username.lower().lstrip('@')

    # 1. Проверка исключений (Whitelist)
    for exc in db_get_exceptions():
        if exc.lower() in text.lower():
            return None

    # 2. Инвайт-ссылки в закрытые каналы/папки (100% спам)
    if REGEX_INVITE.search(text):
        return "Инвайт-ссылка t.me/+"

    # 3. Упоминание ЧУЖИХ ботов (своего упоминать можно, например: Скачано в @VKmusicTopbot)
    mentions = REGEX_FOREIGN_BOT.findall(text)
    for m in mentions:
        m_clean = m.lstrip('@').lower()
        if m_clean != author_clean:
            return f"Упоминание чужого бота {m}"

    # 4. Анализ Inline-кнопок (URL кнопки со стрелочками ↗)
    if message.buttons:
        for row in message.buttons:
            for btn in row:
                if btn.url:
                    url_lower = btn.url.lower()
                    # Если кнопка ведет на инвайт или на чужого бота
                    if "t.me/+" in url_lower or "joinchat" in url_lower:
                        return f"Кнопка с инвайтом: {btn.text}"
                    bot_in_url = REGEX_FOREIGN_BOT.findall(url_lower)
                    for bu in bot_in_url:
                        if bu.lstrip('@').lower() != author_clean:
                            return f"Кнопка на чужого бота: {btn.text}"

    # 5. Анализ скрытых ссылок в тексте (Text-URL и эмодзи со ссылками)
    if message.entities:
        for ent in message.entities:
            if isinstance(ent, MessageEntityTextUrl) and ent.url:
                if REGEX_INVITE.search(ent.url):
                    return "Скрытая инвайт-ссылка под текстом/эмодзи"

    # 6. Проверка по сигнатурам и паттернам из базы
    signatures = db_list_ad_signatures()
    for _, pattern, desc in signatures:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return f"Сигнатура [{desc}]: {pattern}"
        except re.error:
            pass

    return None

# --- ИЗВЛЕКАТЕЛЬ СИГНАТУР ДЛЯ КОМАНДЫ 'sudo ad' (АВТООБУЧЕНИЕ) ---

def auto_extract_signature(msg, author_username: str) -> tuple[str, str]:
    """
    Интеллектуально извлекает паттерн из рекламного сообщения:
    1. Ищет инвайт-ссылку.
    2. Ищет чужого рекламируемого бота.
    3. Ищет характерную фразу.
    """
    text = (msg.raw_text or "").strip()
    author_clean = author_username.lower().lstrip('@')

    # Приоритет 1: Инвайт-ссылка
    m_inv = re.search(r"(https?://)?t\.me/(\+[a-zA-Z0-9_\-]+)", text)
    if m_inv:
        return re.escape(m_inv.group(0)), f"Авто-линк {m_inv.group(0)}"

    # Приоритет 2: Чужой бот
    mentions = REGEX_FOREIGN_BOT.findall(text)
    for m in mentions:
        if m.lstrip('@').lower() != author_clean:
            return rf"{re.escape(m)}\b", f"Авто-бот {m}"

    # Приоритет 3: URL в кнопках
    if msg.buttons:
        for row in msg.buttons:
            for btn in row:
                if btn.url and ("t.me/" in btn.url or "http" in btn.url):
                    return re.escape(btn.url), f"Авто-кнопка URL: {btn.text}"

    # Приоритет 4: Характерная длинная фраза (>3 слов)
    lines = [line.strip() for line in text.split('\n') if len(line.strip().split()) >= 3]
    if lines:
        # Берем первую содержательную строчку, очищая от эмодзи в начале
        phrase = re.sub(r"^[^\wа-яА-Яa-zA-Z0-9]+", "", lines[0]).strip()
        phrase = re.sub(r"[^\w\sа-яА-ЯёЁa-zA-Z0-9]", "", phrase)
        if len(phrase) > 10:
            words = phrase.split()[:5] # первые 4-5 ключевых слов
            pattern = r"\s+".join([re.escape(w) for w in words])
            return pattern, f"Авто-фраза: {' '.join(words)}"

    # Если текст короткий
    safe_text = re.escape(text[:40])
    return safe_text, "Авто-текст"

# --- КЛИЕНТ TELETHON ---
API_ID = int(os.getenv("API_ID", 0)) if os.getenv("API_ID") else None
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")

client = TelegramClient(
    session=StringSession(SESSION_STRING),
    api_id=API_ID,
    api_hash=API_HASH
)

async def delete_after(event, delay: int, reason: str = ""):
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        await event.delete()
        logging.info(f"🗑 [СНОС {event.id}] ({delay}s) Причина: {reason}")
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение {event.id}: {e}")

# --- ОБРАБОТЧИК СООБЩЕНИЙ ЧАТА ---

@client.on(events.NewMessage(incoming=True, func=lambda e: not e.is_private))
async def chat_message_dispatcher(event):
    msg = event.message
    sender = await event.get_sender()
    if not sender:
        return

    is_bot = getattr(sender, 'bot', False) if isinstance(sender, User) else False
    sender_username = (getattr(sender, 'username', '') or '').lower()
    text = msg.raw_text or ""

    # Пропуск системных ботов и sudo-команд
    if sender_username == LEX_BOT_USERNAME.lower() or text.lower().startswith("sudo"):
        return

    # === СЦЕНАРИЙ А: СООБЩЕНИЕ ОТ ЧЕЛОВЕКА ===
    if not is_bot:
        human_rules = db_get_human_regex()
        for pattern, delay in human_rules:
            try:
                if re.match(pattern, text.strip(), re.IGNORECASE):
                    logging.info(f"🚨 [Человек @{sender_username}] Регекс '{pattern}'. Снос через {delay}s")
                    asyncio.create_task(delete_after(event, delay, f"Human regex: {pattern}"))
                    return
            except re.error:
                pass
        return

    # === СЦЕНАРИЙ Б: СООБЩЕНИЕ ОТ БОТА ===
    bot_mode, bot_ttl = db_get_bot_profile(sender_username)
    if not bot_mode:
        # Бот не в списке отслеживаемых — игнорируем
        return

    # --- 1. Режим 'ttl_all' (например @iris_black_bot) ---
    if bot_mode == "ttl_all":
        logging.info(f"⏳ [ИРИС/TTL @{sender_username}] Снос сообщения через {bot_ttl}s")
        asyncio.create_task(delete_after(event, bot_ttl, f"Бот в режиме ttl_all ({bot_ttl}s)"))
        return

    # --- 2. Проверка полезных сообщений с вечным иммунитетом ---
    
    # 2.1 SaluteSpeech: реплай на голос/аудио -> ОСТАВЛЯЕМ НАВСЕГДА
    if sender_username == "smartspeech_sber_bot" and event.is_reply:
        logging.info(f"🎙️ [SaluteSpeech] Расшифровка войса — сохранено навсегда.")
        return

    # 2.2 VK Music: отправленный аудио-трек -> ОСТАВЛЯЕМ НАВСЕГДА
    if msg.audio:
        logging.info(f"🎵 [@{sender_username}] Аудиозапись по запросу — сохранено навсегда.")
        return

    # --- 3. Проверка на рекламу ---
    ad_reason = detect_ad_reason(msg, sender_username)
    
    if ad_reason:
        # РЕКЛАМА ОТ БОТА -> МГНОВЕННЫЙ СНОС (0 секунд)
        logging.info(f"💥 [РЕКЛАМА @{sender_username}] ОБНАРУЖЕН СПАМ: {ad_reason} -> СНОС 0s")
        asyncio.create_task(delete_after(event, 0, f"Реклама: {ad_reason}"))
        return

    # --- 4. Если НЕ реклама, но у бота настроен дефолтный TTL (игры, викторины) ---
    if bot_ttl > 0:
        logging.info(f"🎮 [ИГРА/ПОЛЬЗА @{sender_username}] Полезное смс бота. Удаление по таймеру {bot_ttl}s")
        asyncio.create_task(delete_after(event, bot_ttl, f"Полезный TTL бота {bot_ttl}s"))
        return

    # Иначе — полезное сообщение остается навсегда
    logging.info(f"✅ [@{sender_username}] Полезное сообщение одобрено (Вечный иммунитет).")

# --- КОМАНДЫ ВЛАДЕЛЬЦА (SUDO) ---

@client.on(events.NewMessage(from_users=OWNER_ID, pattern=r"^sudo.*"))
async def sudo_commands_handler(event):
    parts = event.raw_text.split()
    cmd = parts[1].lower() if len(parts) > 1 else None
    val = " ".join(parts[2:]) if len(parts) > 2 else ""

    # Главное меню помощи
    if not cmd:
        help_text = (
            "🎯 **Sniper Userbot v3.0 (Smart Anti-Ad Engine)**\n\n"
            "🧠 **Авто-обучение на рекламе:**\n"
            "• `sudo ad` *(в реплай на рекламу)* — Сносит спам и **автоматически обучает** фильтр!\n\n"
            "🤖 **Управление ботами:**\n"
            "• `sudo set_bot @username {ad_scan|ttl_all} {ttl_сек}`\n"
            "• `sudo del_bot @username`\n"
            "• `sudo list_bots` — Список ботов и их режимов\n\n"
            "🛡️ **Сигнатуры рекламы:**\n"
            "• `sudo add_ad {регекс}` — Добавить паттерн вручную\n"
            "• `sudo del_ad {id_или_регекс}`\n"
            "• `sudo list_ad` — Список рекламных триггеров\n\n"
            "⚙️ **Исключения и люди:**\n"
            "• `sudo add_exc {слово}` / `sudo del_exc` / `sudo list_exc`\n"
            "• `sudo add_regex {регекс} {сек}` / `sudo list_regex`\n"
            "• `sudo log` — Последние логи системы"
        )
        return await event.reply(help_text)

    try:
        # === 1. КОМАНДА СУПЕР-ОБУЧЕНИЯ В РЕПЛАЙ ===
        if cmd in ["ad", "реклама", "спам", "бан"]:
            if not event.is_reply:
                return await event.reply("⚠️ Ответь этой командой (`sudo ad`) **на сообщение с рекламой**.")

            reply_msg = await event.get_reply_message()
            reply_sender = await reply_msg.get_sender()
            bot_user = getattr(reply_sender, 'username', '') or 'unknown_bot'

            # Извлекаем уникальный паттерн
            pattern, desc = auto_extract_signature(reply_msg, bot_user)
            
            # Сохраняем в базу
            db_add_ad_signature(pattern, f"Выучено из @{bot_user}: {desc}")
            
            # Удаляем рекламное сообщение и саму команду
            await reply_msg.delete()
            await event.delete()
            
            confirm = await event.respond(
                f"🎯 **Реклама уничтожена и выучена!**\n"
                f"• Бот: `@{bot_user}`\n"
                f"• Выученный паттерн: `{pattern}`\n"
                f"• Описание: _{desc}_"
            )
            await asyncio.sleep(5)
            await confirm.delete()

        # === 2. ЛОГИ ===
        elif cmd in ["log", "logs"]:
            logs = memory_log_handler.get_logs(limit=18)
            if not logs:
                return await event.reply("📄 Логи пусты.")
            log_text = "\n".join(logs)
            if len(log_text) > 3800:
                log_text = log_text[-3800:]
            await event.reply(f"📜 **Логи юзербота v3:**\n\n```text\n{log_text}\n```")

        # === 3. УПРАВЛЕНИЕ БОТАМИ ===
        elif cmd == "set_bot" and val:
            sp = val.split()
            username = sp[0].lstrip('@')
            mode = sp[1] if len(sp) > 1 else "ad_scan"
            ttl = int(sp[2]) if len(sp) > 2 and sp[2].isdigit() else 0
            
            if mode not in ["ad_scan", "ttl_all", "ignore"]:
                return await event.reply("❌ Режим может быть только: `ad_scan`, `ttl_all`, `ignore`")
            
            db_set_bot_profile(username, mode, ttl)
            await event.reply(f"✅ Бот `@{username}` настроен: Режим = **{mode}**, Дефолтный TTL = **{ttl}s**")

        elif cmd == "del_bot" and val:
            if db_del_bot_profile(val):
                await event.reply(f"🗑 Бот `@{val.lstrip('@')}` удален из списка отслеживания.")
            else:
                await event.reply("❌ Бот не найден.")

        elif cmd == "list_bots":
            bots = db_list_bots()
            text = "🤖 **Отслеживаемые боты:**\n\n"
            for u, m, t in bots:
                text += f"• `@{u}` | Режим: **{m}** | TTL: **{t}s**\n"
            await event.reply(text if bots else "Список пуст.")

        # === 4. УПРАВЛЕНИЕ СИГНАТУРАМИ РЕКЛАМЫ ===
        elif cmd == "add_ad" and val:
            if db_add_ad_signature(val, "Добавлено вручную через sudo"):
                await event.reply(f"✅ Сигнатура рекламы добавлена:\n`{val}`")
            else:
                await event.reply("⚠️ Такая сигнатура уже есть.")

        elif cmd == "del_ad" and val:
            if db_del_ad_signature(val):
                await event.reply(f"🗑 Сигнатура `{val}` удалена.")
            else:
                await event.reply("❌ Сигнатура не найдена.")

        elif cmd == "list_ad":
            sigs = db_list_ad_signatures()
            text = "🚫 **Активные сигнатуры рекламы:**\n\n"
            for sid, pat, desc in sigs:
                text += f"**[{sid}]** `{pat}`\n_{desc}_\n\n"
            if len(text) > 4000:
                text = text[:3950] + "\n...(обрезано)"
            await event.reply(text if sigs else "Сигнатур нет.")

        # === 5. ИСКЛЮЧЕНИЯ (WHITELIST) ===
        elif cmd in ["add_exc", "add_exception"] and val:
            if db_add_exception(val): await event.reply(f"🛡️ Исключение `{val}` добавлено.")
            else: await event.reply("⚠️ Уже в базе.")

        elif cmd in ["del_exc", "del_exception"] and val:
            if db_del_exception(val): await event.reply(f"🗑 Исключение `{val}` удалено.")
            else: await event.reply("❌ Не найдено.")

        elif cmd in ["list_exc", "list_exceptions"]:
            excs = db_get_exceptions()
            text = "🛡️ **Белый список слов:**\n" + ("\n".join([f"• `{e}`" for e in excs]) if excs else "Пусто.")
            await event.reply(text)

        # === 6. РЕГЕКСЫ ЛЮДЕЙ ===
        elif cmd == "add_regex" and val:
            sp = val.split()
            pat = sp[0]
            delay = int(sp[1]) if len(sp) > 1 and sp[1].isdigit() else 5
            db_add_human_regex(pat, delay)
            await event.reply(f"✅ Регекс человека `{pat}` добавлен (задержка: **{delay}s**).")

        elif cmd == "del_regex" and val:
            if db_del_human_regex(val.strip()): await event.reply(f"🗑 Регекс `{val}` удален.")
            else: await event.reply("❌ Не найден.")

        elif cmd == "list_regex":
            items = db_get_human_regex()
            text = "🧹 **Регексы для людей:**\n" + ("\n".join([f"• `{p}` (таймаут: {d}s)" for p, d in items]) if items else "Пусто.")
            await event.reply(text)

        else:
            await event.reply("❌ Неизвестная команда. Напиши `sudo` для справки.")

    except Exception as e:
        await event.reply(f"⚠️ **Ошибка при выполнении:**\n`{e}`")

# --- ТОЧКА ВХОДА ---
async def main():
    if not all([API_ID, API_HASH, SESSION_STRING]):
        logging.critical("ОШИБКА: Проверьте переменные API_ID, API_HASH, SESSION_STRING в окружении!")
        return

    init_db()
    await client.start()
    me = await client.get_me()
    logging.info(f"Sniper v3.0 успешно запущен на аккаунте: {me.first_name} (@{me.username})")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
