# core/db.py
import sqlite3
import logging
import subprocess
from collections import deque
from core.config import DB_NAME, OWNER_ID

# --- ЛОГГЕР В ПАМЯТИ ---
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

# --- GIT СИНХРОНИЗАЦИЯ БАЗЫ ---
def save_db_to_git():
    try:
        status = subprocess.run(["git", "status", "--porcelain", DB_NAME], capture_output=True, text=True)
        if not status.stdout.strip():
            return
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", DB_NAME], check=True)
        subprocess.run(["git", "commit", "-m", "chore: sync db [skip ci]"], check=True)
        subprocess.run(["git", "push"], check=True)
        logging.info("БД сохранена в GitHub.")
    except Exception as e:
        logging.warning(f"Ошибка сохранения БД в Git: {e}")

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ---
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

# --- МЕТОДЫ БАЗЫ ---
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

# --- АВТОРИЗАЦИЯ SUDO ---
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
