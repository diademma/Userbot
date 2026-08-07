# CODEVER: v3.7 | Sniper Userbot for LEX (Verbose Join Diagnostics & Entity Resolver)
import os
import re
import sys
import io
import time
import random
import asyncio
import sqlite3
import logging
import subprocess
from datetime import datetime
from collections import deque
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import (
    User, 
    MessageService,
    ChatBannedRights,
    MessageActionChatJoinedByLink,
    MessageActionChatAddUser,
    MessageActionChatJoinedByRequest,
    UpdateNewChannelMessage,
    UpdateNewMessage,
    UpdateChannelParticipant,
    ChannelParticipantSelf
)
from telethon.tl.functions.channels import EditBannedRequest

# --- БУФЕР ЛОГОВ В ПАМЯТИ ДЛЯ КОМАНДЫ 'sudo log' ---
class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity=50):
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

# Настройка логирования в консоль и в оперативную память
memory_log_handler = MemoryLogHandler(capacity=50)
memory_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.addHandler(memory_log_handler)

OWNER_ID = 5421909121 
LEX_BOT_USERNAME = "my_LEX_superbot"
DB_NAME = "userbot_memory.db"

# --- ГЛОБАЛЬНЫЕ СТРУКТУРЫ ДЛЯ АНТИРЕЙДА И КАПЧИ ---
PENDING_CAPTCHAS = {}  # {(chat_id, user_id): {"answer": int, "attempts_left": int, "captcha_msg_id": int, "warn_msg_ids": list, "join_time": float, "task": Task}}
RECENT_JOINS = deque(maxlen=200)  # [(timestamp, chat_id, user_id)]
JOIN_DEDUP = deque(maxlen=300)    # [(chat_id, user_id)] - защита от повторных срабатываний
RAID_MODE_ACTIVE = {}  # {chat_id: bool}
RAID_RESET_TASKS = {}  # {chat_id: Task}

# --- СОХРАНЕНИЕ БД В GITHUB ---
def save_db_to_git():
    try:
        status = subprocess.run(["git", "status", "--porcelain", DB_NAME], capture_output=True, text=True)
        if not status.stdout.strip():
            return

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
        
        subprocess.run(["git", "add", DB_NAME], check=True)
        subprocess.run(["git", "commit", "-m", "chore: update userbot memory [skip ci]"], check=True)
        subprocess.run(["git", "push"], check=True)
        logging.info("База данных успешно закоммичена и отправлена в репозиторий GitHub.")
    except Exception as e:
        logging.warning(f"Не удалось сохранить базу данных в Git: {e}")

# --- ИНИЦИАЛИЗАЦИЯ БД ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS regex_patterns (id INTEGER PRIMARY KEY, pattern TEXT UNIQUE)")
    cur.execute("CREATE TABLE IF NOT EXISTS bot_banwords (id INTEGER PRIMARY KEY, word TEXT UNIQUE, delay INTEGER DEFAULT 45)")
    cur.execute("CREATE TABLE IF NOT EXISTS bot_exceptions (id INTEGER PRIMARY KEY, word TEXT UNIQUE)")
    cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    
    # Таблица логов анти-рейд активности
    cur.execute("""
        CREATE TABLE IF NOT EXISTS raid_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            chat_id INTEGER,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("DELETE FROM bot_banwords WHERE word IS NULL OR TRIM(word) = '' OR word LIKE '%(д|н|м%'")
    cur.execute("DELETE FROM regex_patterns WHERE pattern IS NULL OR TRIM(pattern) = ''")
    cur.execute("DELETE FROM bot_exceptions WHERE word IS NULL OR TRIM(word) = ''")
    
    try:
        cur.execute("ALTER TABLE bot_banwords ADD COLUMN delay INTEGER DEFAULT 45")
    except sqlite3.OperationalError:
        pass
        
    # Дефолтные настройки
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('delay_user_command', '5')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('shield_enabled', '1')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('captcha_enabled', '1')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('raid_threshold_count', '5')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('raid_threshold_seconds', '10')")
    conn.commit()
    conn.close()
    logging.info("База данных инициализирована и защищена.")

def log_raid_event(event_type, user_id, username, full_name, chat_id, details=""):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("""
            INSERT INTO raid_logs (event_type, user_id, username, full_name, chat_id, details)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (event_type, user_id, username, full_name, chat_id, details))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Ошибка логирования анти-рейда в БД: {e}")

# --- ФУНКЦИИ БД ---
def db_add_regex(pattern):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT INTO regex_patterns (pattern) VALUES (?)", (pattern.strip(),))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except sqlite3.IntegrityError:
        return False

def db_add_banword(word, delay=45):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT OR REPLACE INTO bot_banwords (word, delay) VALUES (?, ?)", (word.strip(), delay))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except Exception as e:
        logging.error(f"Ошибка БД при сохранении бан-слова: {e}")
        return False

def db_add_exception(word):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT INTO bot_exceptions (word) VALUES (?)", (word.strip(),))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except sqlite3.IntegrityError:
        return False

def db_del_regex(pattern):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM regex_patterns WHERE pattern = ?", (pattern.strip(),))
    changes = conn.total_changes
    conn.commit()
    conn.close()
    if changes > 0: save_db_to_git()
    return changes > 0

def db_del_banword(word):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM bot_banwords WHERE word = ?", (word.strip(),))
    changes = conn.total_changes
    conn.commit()
    conn.close()
    if changes > 0: save_db_to_git()
    return changes > 0

def db_del_exception(word):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM bot_exceptions WHERE word = ?", (word.strip(),))
    changes = conn.total_changes
    conn.commit()
    conn.close()
    if changes > 0: save_db_to_git()
    return changes > 0

def db_list_regex():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT pattern FROM regex_patterns").fetchall()
    conn.close()
    return [item[0] for item in items if item[0] and item[0].strip()]

def db_list_banwords():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT word, delay FROM bot_banwords").fetchall()
    conn.close()
    return [(item[0], item[1]) for item in items if item[0] and item[0].strip()]

def db_list_exceptions():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT word FROM bot_exceptions").fetchall()
    conn.close()
    return [item[0] for item in items if item[0] and item[0].strip()]

def db_set(key, value):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()
    save_db_to_git()

def db_get_int(key, default):
    conn = sqlite3.connect(DB_NAME)
    row = conn.cursor().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return int(row[0]) if (row and str(row[0]).isdigit()) else default

# --- КЛИЕНТ TELETHON ---
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")

if API_ID:
    API_ID = int(API_ID)

client = TelegramClient(
    session=StringSession(SESSION_STRING),
    api_id=API_ID,
    api_hash=API_HASH
)

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ БЕЗОПАСНОЙ ОТПРАВКИ ---
async def safe_reply(event, text_content, filename="report.txt"):
    if len(text_content) > 3500:
        file_data = io.BytesIO(text_content.encode('utf-8'))
        file_data.name = filename
        caption = text_content[:300] + f"\n\n⚠️ **Отчёт слишком длинный ({len(text_content)} симв.), прикреплён полным файлом {filename}!**"
        await event.reply(caption, file=file_data)
    else:
        await event.reply(text_content)

# --- ИСПОЛНИТЕЛИ НАКАЗАНИЙ ---
async def penalty_kick(chat_id, user_id):
    try:
        await client.kick_participant(chat_id, user_id)
    except Exception as e:
        logging.error(f"Не удалось кикнуть пользователя {user_id}: {e}")

async def penalty_mute(chat_id, user_id):
    try:
        mute_rights = ChatBannedRights(
            until_date=None,
            send_messages=True,
            send_media=True,
            send_stickers=True,
            send_gifs=True,
            send_games=True,
            send_inline=True,
            embed_link_previews=True
        )
        await client(EditBannedRequest(channel=chat_id, participant=user_id, banned_rights=mute_rights))
    except Exception as e:
        logging.error(f"Не удалось замьютить пользователя {user_id}: {e}")

async def penalty_ban(chat_id, user_id):
    try:
        ban_rights = ChatBannedRights(until_date=None, view_messages=True)
        await client(EditBannedRequest(channel=chat_id, participant=user_id, banned_rights=ban_rights))
    except Exception as e:
        logging.error(f"Не удалось забанить пользователя {user_id}: {e}")

# --- ТАЙМАУТ КАПЧИ (3 МИНУТЫ) ---
async def captcha_timeout_worker(chat_id, user_id, user_name, captcha_msg_id):
    await asyncio.sleep(180)  # 3 минуты
    
    key = (chat_id, user_id)
    if key in PENDING_CAPTCHAS:
        captcha_data = PENDING_CAPTCHAS.pop(key)
        
        await penalty_mute(chat_id, user_id)  # За таймаут — МЬЮТ
        log_raid_event("CAPTCHA_TIMEOUT", user_id, "", user_name, chat_id, "Наказание: МЬЮТ (не ответил за 3 минуты)")
        logging.info(f"🛡️ [Щит] Пользователь {user_id} ({user_name}) замьючен за таймаут капчи.")
        
        try:
            msgs_to_del = [captcha_msg_id] + captcha_data.get("warn_msg_ids", [])
            await client.delete_messages(chat_id, msgs_to_del)
        except Exception:
            pass

# --- ТАЙМЕР АВТО-ОТКЛЮЧЕНИЯ РЕЖИМА РЕЙДА ---
async def raid_reset_worker(chat_id):
    await asyncio.sleep(120)
    RAID_MODE_ACTIVE[chat_id] = False
    log_raid_event("RAID_STOPPED", 0, "", "", chat_id, "Анти-рейд режим авто-выключен (тишина 2 мин)")
    logging.info(f"🛡️ [Щит] Режим рейда для чата {chat_id} автоматически отключён.")

# --- ЕДИНАЯ ТОЧКА ОБРАБОТКИ ВХОДА УЧАСТНИКОВ ---
async def trigger_join_pipeline(chat_id, user, is_test=False):
    if not user:
        return

    logging.info(f"📥 [ВХОД ОБНАРУЖЕН] user_id={user.id} ({user.first_name}) in chat={chat_id}")

    if not is_test:
        if user.is_self:
            logging.info(f"ℹ️ Пропуск своего аккаунта юзербота: {user.id}")
            return
        if getattr(user, 'bot', False):
            logging.info(f"ℹ️ Пропуск бота: {user.id}")
            return
        if user.id == OWNER_ID:
            logging.info(f"👑 [ПРОПУСК] Владелец зашел в чат (user_id={user.id}). Капча Владельцу не выдается!")
            return

        # Защита от дублирующих срабатываний (в пределах 10 сек)
        dedup_key = (chat_id, user.id)
        if dedup_key in JOIN_DEDUP:
            logging.info(f"ℹ️ Повторное событие за 10с для user_id={user.id}, пропущено.")
            return
        JOIN_DEDUP.append(dedup_key)

    now = time.time()
    user_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Участник"
    user_un = getattr(user, 'username', '') or ''

    # 1. ПРОВЕРКА СКОРОСТИ ВХОДА (ДЕТЕКЦИЯ РЕЙДА)
    if not is_test:
        RECENT_JOINS.append((now, chat_id, user.id))
        thresh_cnt = max(2, db_get_int('raid_threshold_count', 5))
        thresh_sec = max(1, db_get_int('raid_threshold_seconds', 10))

        recent_in_this_chat = [t for t, c, u in RECENT_JOINS if c == chat_id and now - t <= thresh_sec]

        if len(recent_in_this_chat) >= thresh_cnt:
            if not RAID_MODE_ACTIVE.get(chat_id, False):
                RAID_MODE_ACTIVE[chat_id] = True
                log_raid_event("RAID_TRIGGERED", user.id, user_un, user_name, chat_id, f"ВКЛЮЧЁН РЕЖИМ РЕЙДА (>{thresh_cnt} входов за {thresh_sec}с)")
                logging.warning(f"🚨 [Щит] Обнаружен рейд в чате {chat_id}! Включен массовый авто-бан!")

            if chat_id in RAID_RESET_TASKS:
                RAID_RESET_TASKS[chat_id].cancel()
            RAID_RESET_TASKS[chat_id] = asyncio.create_task(raid_reset_worker(chat_id))

        # 2. ЕСЛИ РЕЖИМ РЕЙДА АКТИВЕН -> МГНОВЕННЫЙ БАН (БЕЗ КАПЧИ)
        if RAID_MODE_ACTIVE.get(chat_id, False):
            try:
                await penalty_ban(chat_id, user.id)
                log_raid_event("RAID_AUTO_BAN", user.id, user_un, user_name, chat_id, "Мгновенный авто-бан во время рейда")
                logging.info(f"🔨 [Щит] Рейд-бот {user.id} ({user_name}) авто-забанен!")
            except Exception as e:
                logging.error(f"Ошибка авто-бана рейдера {user.id}: {e}")
            return

    # 3. ПРОВЕРКА: ВКЛЮЧЕНА ЛИ КАПЧА
    if db_get_int('captcha_enabled', 1) == 0 and not is_test:
        logging.info("ℹ️ Капча выключена в настройках, пропуск выдачи.")
        return

    # 4. ОБЫЧНЫЙ ВХОД -> МАТЕМАТИЧЕСКАЯ КАПЧА (ДО 30 ЕДИНИЦ)
    op = random.choice(['+', '-'])
    if op == '+':
        a = random.randint(1, 15)
        b = random.randint(1, 15)
        ans = a + b
    else:
        a = random.randint(10, 30)
        b = random.randint(1, a)
        ans = a - b

    mention = f"[{user_name}](tg://user?id={user.id})"
    prefix = "🧪 **[ТЕСТОВЫЙ РЕЖИМ]**\n" if is_test else ""
    captcha_text = (
        f"{prefix}👋 Привет, {mention}!\n\n"
        f"🛡️ **Для защиты реши пример за 3 минуты:**\n"
        f"👉 **`{a} {op} {b} = ?`**\n\n"
        f"• Напиши только **число** ответом в чат (у вас **2** попытки).\n"
        f"• Напишешь **текстом** вместо числа — КИК!\n"
        f"• Ошибся числом 2 раза или таймаут — МЬЮТ!"
    )

    try:
        # Гарантированное получение сущности чата перед отправкой
        chat_entity = await client.get_entity(chat_id)
        msg = await client.send_message(chat_entity, captcha_text)
        task = asyncio.create_task(captcha_timeout_worker(chat_id, user.id, user_name, msg.id))
        
        PENDING_CAPTCHAS[(chat_id, user.id)] = {
            "answer": ans,
            "attempts_left": 2,
            "captcha_msg_id": msg.id,
            "warn_msg_ids": [],
            "join_time": now,
            "task": task
        }
        log_raid_event("CAPTCHA_SENT", user.id, user_un, user_name, chat_id, f"Пример: {a} {op} {b} = {ans}")
        logging.info(f"✅ [КАПЧА ОТПРАВЛЕНА] Сообщение #{msg.id} успешно отправлено пользователю {user.id}")
    except Exception as e:
        logging.error(f"❌ [ОШИБКА ОТПРАВКИ КАПЧИ] chat={chat_id}, user={user.id}: {e}")

async def handle_raw_user_join(chat_id, user_id):
    if db_get_int('shield_enabled', 1) == 0:
        return
    try:
        u = await client.get_entity(user_id)
        if u:
            await trigger_join_pipeline(chat_id, u)
    except Exception as e:
        logging.error(f"[RAW Join Fetch Error] user_id={user_id}: {e}")

# --- ГЛУБОКИЙ RAW MTPROTO ПЕРЕХВАТЧИК ВСЕХ ТИПОВ ВХОДОВ ---
@client.on(events.Raw)
async def on_raw_telegram_update(update):
    if db_get_int('shield_enabled', 1) == 0:
        return

    try:
        # 1. Перехват сообщений со служебными действиями (вход по ссылке, заявке, добавление)
        if isinstance(update, (UpdateNewChannelMessage, UpdateNewMessage)):
            msg = getattr(update, 'message', None)
            if isinstance(msg, MessageService):
                action = getattr(msg, 'action', None)
                peer = getattr(msg, 'peer_id', None)
                
                chat_id = None
                if hasattr(peer, 'channel_id'):
                    chat_id = int(f"-100{peer.channel_id}")
                elif hasattr(peer, 'chat_id'):
                    chat_id = -peer.chat_id

                if not chat_id:
                    return

                # Вход по инвайт-ссылке или заявке в приватную группу
                if isinstance(action, (MessageActionChatJoinedByLink, MessageActionChatJoinedByRequest)):
                    from_id = getattr(msg, 'from_id', None)
                    uid = getattr(from_id, 'user_id', None) if from_id else None
                    if uid:
                        asyncio.create_task(handle_raw_user_join(chat_id, uid))

                # Добавление участников
                elif isinstance(action, MessageActionChatAddUser):
                    users_added = getattr(action, 'users', [])
                    for uid in users_added:
                        asyncio.create_task(handle_raw_user_join(chat_id, uid))

        # 2. Перехват системных обновлений участников супергрупп (для админов)
        elif isinstance(update, UpdateChannelParticipant):
            cid = getattr(update, 'channel_id', None)
            uid = getattr(update, 'user_id', None)
            new_p = getattr(update, 'new_participant', None)

            if cid and uid and new_p and not isinstance(new_p, ChannelParticipantSelf):
                chat_id = int(f"-100{cid}")
                asyncio.create_task(handle_raw_user_join(chat_id, uid))
    except Exception:
        pass

# --- ГЛАВНАЯ ЛОГИКА ОБРАБОТКИ ОБЫЧНЫХ СООБЩЕНИЙ ---
async def delete_after(event, delay, reason=""):
    try:
        await asyncio.sleep(delay)
        await event.delete()
        logging.info(f"🗑 Сообщение {event.id} удалено. Причина: [{reason}]")
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение {event.id}: {e}")

@client.on(events.NewMessage(incoming=True, func=lambda e: not e.is_private))
async def message_handler(event):
    text = event.raw_text.strip() if event.raw_text else None
    if not text:
        return

    sender = await event.get_sender()
    if not sender:
        return

    chat_id = event.chat_id
    user_id = sender.id
    is_bot = getattr(sender, 'bot', False) if isinstance(sender, User) else False
    sender_username = getattr(sender, 'username', '') or 'unknown'
    user_name = f"{getattr(sender, 'first_name', '') or ''} {getattr(sender, 'last_name', '') or ''}".strip() or "Участник"

    # Игнорируем Лекса и sudo-команды
    if sender_username.lower() == LEX_BOT_USERNAME.lower() or text.lower().startswith("sudo"):
        return

    # === ПРОВЕРКА ОТВЕТА НА КАПЧУ ===
    captcha_key = (chat_id, user_id)
    if captcha_key in PENDING_CAPTCHAS:
        captcha_data = PENDING_CAPTCHAS[captcha_key]
        expected_ans = captcha_data["answer"]

        # 1. ВАРИАНТ: НАПИСАЛ ТЕКСТОМ ВМЕСТО ЧИСЛА -> КИК
        if not text.isdigit():
            captcha_data["task"].cancel()
            del PENDING_CAPTCHAS[captcha_key]
            
            log_raid_event("CAPTCHA_TEXT_FAIL", user_id, sender_username, user_name, chat_id, f"Наказание: КИК (написал текст: '{text[:20]}')")
            await penalty_kick(chat_id, user_id)
            
            try:
                msgs_to_del = [captcha_data["captcha_msg_id"], event.id] + captcha_data.get("warn_msg_ids", [])
                await client.delete_messages(chat_id, msgs_to_del)
            except Exception:
                pass
            return

        # 2. ВАРИАНТ: НАПИСАЛ ЧИСЛО
        val = int(text)
        if val == expected_ans:
            # 2.1 ВЕРНЫЙ ОТВЕТ -> СНИМАЕМ КАПЧУ И ЧИСТИМ ЧАТ
            captcha_data["task"].cancel()
            del PENDING_CAPTCHAS[captcha_key]
            
            log_raid_event("CAPTCHA_PASSED", user_id, sender_username, user_name, chat_id, f"Верный ответ: {expected_ans}")
            
            try:
                msgs_to_del = [captcha_data["captcha_msg_id"], event.id] + captcha_data.get("warn_msg_ids", [])
                await client.delete_messages(chat_id, msgs_to_del)
            except Exception:
                pass
            return
        else:
            # 2.2 ОШИБСЯ ЧИСЛОМ -> ДАЕМ ПОПЫТКУ ИЛИ МЬЮТИМ
            captcha_data["attempts_left"] -= 1
            log_raid_event("CAPTCHA_WRONG_NUMBER", user_id, sender_username, user_name, chat_id, f"Ошибся числом (ввел {val}, осталось: {captcha_data['attempts_left']})")
            
            if captcha_data["attempts_left"] > 0:
                # ВТОРАЯ ПОПЫТКА
                try:
                    warn_msg = await client.send_message(
                        chat_id, 
                        f"⚠️ [{user_name}](tg://user?id={user_id}), неправильный ответ! У вас осталась еще **{captcha_data['attempts_left']}** попытка."
                    )
                    captcha_data.setdefault("warn_msg_ids", []).append(warn_msg.id)
                    await client.delete_messages(chat_id, event.id)
                except Exception:
                    pass
                return
            else:
                # ПОПЫТКИ ИСЧЕРПАНЫ -> МЬЮТ
                captcha_data["task"].cancel()
                del PENDING_CAPTCHAS[captcha_key]
                
                log_raid_event("CAPTCHA_FAIL_NUMBERS", user_id, sender_username, user_name, chat_id, "Наказание: МЬЮТ (исчерпал 2 попытки чисел)")
                await penalty_mute(chat_id, user_id)
                
                try:
                    msgs_to_del = [captcha_data["captcha_msg_id"], event.id] + captcha_data.get("warn_msg_ids", [])
                    await client.delete_messages(chat_id, msgs_to_del)
                except Exception:
                    pass
                return

    if is_bot:
        short_text = text[:35].replace('\n', ' ')
        logging.info(f"📩 Бот @{sender_username} прислал: '{short_text}...'")

    # --- СЦЕНАРИЙ 1: Сообщение от ЧЕЛОВЕКА ---
    if not is_bot:
        patterns = db_list_regex()
        for pattern in patterns:
            pattern_clean = pattern.strip()
            if not pattern_clean:
                continue
            try:
                if re.match(pattern_clean, text, re.IGNORECASE):
                    delay = db_get_int('delay_user_command', 5)
                    reason = f"Регекс человека: '{pattern_clean}'"
                    logging.info(f"🚨 [ЧЕЛОВЕК @{sender_username}] {reason}. Удаление через {delay} сек.")
                    asyncio.create_task(delete_after(event, delay, reason))
                    return
            except re.error as e:
                logging.error(f"Ошибка в регексе '{pattern_clean}': {e}")

    # --- СЦЕНАРИЙ 2: Сообщение от БОТА ---
    else:
        exceptions = db_list_exceptions()
        for exc_word in exceptions:
            exc_clean = exc_word.strip()
            if not exc_clean:
                continue

            exc_matched = False
            if exc_clean.lower() in text.lower():
                exc_matched = True
            elif any(c in exc_clean for c in r".*+?^$[]{}()|\\"):
                try:
                    m = re.search(exc_clean, text, re.IGNORECASE)
                    if m and len(m.group(0)) > 1:
                        exc_matched = True
                except re.error:
                    pass

            if exc_matched:
                logging.info(f"🛡️ [БОТ @{sender_username}] Найдено исключение: '{exc_clean}'. Сообщение НЕ будет удалено!")
                return

        banwords = db_list_banwords()
        for word, delay in banwords:
            word_clean = word.strip()
            if not word_clean:
                continue

            matched = False
            if word_clean.lower() in text.lower():
                matched = True
            elif any(c in word_clean for c in r".*+?^$[]{}()|\\"):
                try:
                    m = re.search(word_clean, text, re.IGNORECASE)
                    if m and len(m.group(0)) > 1:
                        matched = True
                except re.error:
                    pass

            if matched:
                reason = f"Бан-слово бота: '{word_clean}'"
                logging.info(f"🚨 [БОТ @{sender_username}] {reason}. Удаление через {delay} сек.")
                asyncio.create_task(delete_after(event, delay, reason))
                return

# --- ОБРАБОТЧИК КОМАНД SUDO ---
@client.on(events.NewMessage(from_users=OWNER_ID, pattern=r"^sudo.*"))
async def owner_commands_handler(event):
    parts = event.raw_text.split()
    command = parts[0]
    subcommand = parts[1] if len(parts) > 1 else None
    value = " ".join(parts[2:]) if len(parts) > 2 else None

    # --- ЛАКОНИЧНОЕ ГЛАВНОЕ МЕНЮ SUDO ---
    if command == "sudo" and not subcommand:
        delay_user = db_get_int('delay_user_command', 5)
        help_text = (
            "🛡️ **LEX Sniper Userbot v3.7 активен!**\n\n"
            "**🛡️ Система Щита (Анти-Рейд & Капча):**\n"
            "• `sudo shield` — Настройки Щита, Капчи, Порога рейда и Отчёты\n\n"
            "**Просмотр логов:**\n"
            "• `sudo log` — Показать консольные логи юзербота\n\n"
            "**Триггеры для людей (Regex):**\n"
            "• `sudo add_regex {выражение}` | `sudo del_regex` | `sudo list_regex`\n\n"
            "**Бан-слова для ботов (Banwords):**\n"
            "• `sudo add_banword {слово} {сек}` | `sudo del_banword` | `sudo list_banwords`\n\n"
            "**🛡️ Исключения для ботов (Whitelist):**\n"
            "• `sudo add_exc {слово}` | `sudo del_exc` | `sudo list_exc`\n\n"
            "**Задержки:**\n"
            f"• `sudo set_delay_user {delay_user}` (для людей)"
        )
        return await safe_reply(event, help_text)

    try:
        # --- ЕДИНАЯ КОМАНДА УПРАВЛЕНИЯ ЩИТОМ (SUDO SHIELD) ---
        if subcommand == "shield":
            sub2 = parts[2].lower() if len(parts) > 2 else None
            val2 = " ".join(parts[3:]).strip() if len(parts) > 3 else None

            # 1. Показ статуса и меню
            if not sub2:
                shield_on = db_get_int('shield_enabled', 1) == 1
                captcha_on = db_get_int('captcha_enabled', 1) == 1
                thresh_cnt = max(2, db_get_int('raid_threshold_count', 5))
                thresh_sec = max(1, db_get_int('raid_threshold_seconds', 10))

                shield_status = "ВКЛЮЧЁН ✅" if shield_on else "ВЫКЛЮЧЕН ❌"
                captcha_status = "ВКЛЮЧЕНА ✅" if captcha_on else "ВЫКЛЮЧЕНА ❌"

                shield_help = (
                    "🛡️ **УПРАВЛЕНИЕ СИСТЕМОЙ ЩИТА (SUDO SHIELD)**\n\n"
                    "⚙️ **Текущий статус:**\n"
                    f"• Главный Щит: **{shield_status}**\n"
                    f"• Математическая Капча: **{captcha_status}**\n"
                    f"• Порог авто-рейда: `{thresh_cnt}` входов / `{thresh_sec}` сек\n\n"
                    "📊 **Отчёты и Логи:**\n"
                    "• `sudo shield report` — Статистика капчи и авто-рейдов\n"
                    "• `sudo shield logs` — Последние 50 событий защиты\n\n"
                    "🔧 **Команды Настройки:**\n"
                    "• `sudo shield on` | `sudo shield off` — Вкл/выкл весь Щит\n"
                    "• `sudo shield captcha on` | `sudo shield captcha off` — Вкл/выкл капчу\n"
                    "• `sudo shield rate 2/10` — Порог авто-рейда (2 входа за 10 сек)\n"
                    "• `sudo shield test` — Запустить тестовую капчу для себя"
                )
                return await safe_reply(event, shield_help)

            # Мгновенный тест работы капчи
            elif sub2 == "test":
                me = await client.get_me()
                await event.reply("🧪 **Запускаю тестовую капчу...**")
                return await trigger_join_pipeline(event.chat_id, me, is_test=True)

            # 2. Вкл/выкл весь Щит
            elif sub2 in ["on", "1", "true", "вкл"]:
                db_set('shield_enabled', '1')
                return await event.reply("✅ Главный Щит **ВКЛЮЧЁН**.")

            elif sub2 in ["off", "0", "false", "выкл"]:
                db_set('shield_enabled', '0')
                return await event.reply("❌ Главный Щит полностью **ВЫКЛЮЧЁН**.")

            # 3. Вкл/выкл Капчу (Анти-Рейд остаётся активным)
            elif sub2 == "captcha" and val2:
                v = val2.lower()
                if v in ["on", "1", "true", "вкл"]:
                    db_set('captcha_enabled', '1')
                    return await event.reply("✅ Математическая капча **ВКЛЮЧЕНА**.")
                elif v in ["off", "0", "false", "выкл"]:
                    db_set('captcha_enabled', '0')
                    return await event.reply("❌ Математическая капча **ВЫКЛЮЧЕНА** (Анти-рейд защита остаётся активной!).")

            # 4. Порог скорости авто-рейда (2/10 или 2 10)
            elif sub2 in ["rate", "threshold"] and val2:
                match = re.match(r"^(\d+)[/\s,]+(\d+)$", val2)
                if match:
                    cnt = int(match.group(1))
                    sec = int(match.group(2))
                    if cnt >= 2 and sec >= 1:
                        db_set('raid_threshold_count', cnt)
                        db_set('raid_threshold_seconds', sec)
                        return await event.reply(f"✅ Порог авто-рейда изменён: **{cnt}** входов за **{sec}** сек.")
                    else:
                        return await event.reply("❌ Количество входов должно быть от 2, а секунды от 1!")
                elif val2.isdigit() and int(val2) >= 2:
                    db_set('raid_threshold_count', val2)
                    curr_sec = db_get_int('raid_threshold_seconds', 10)
                    return await event.reply(f"✅ Порог авто-рейда изменён: **{val2}** входов за **{curr_sec}** сек.")
                else:
                    return await event.reply("❌ Укажите значение в формате `2/10` (2 входа за 10 сек)!")

            # 5. Отчёт
            elif sub2 in ["report", "stat", "stats"]:
                conn = sqlite3.connect(DB_NAME)
                cur = conn.cursor()
                
                sent_cnt = cur.execute("SELECT COUNT(*) FROM raid_logs WHERE event_type = 'CAPTCHA_SENT'").fetchone()[0]
                pass_cnt = cur.execute("SELECT COUNT(*) FROM raid_logs WHERE event_type = 'CAPTCHA_PASSED'").fetchone()[0]
                text_fail_cnt = cur.execute("SELECT COUNT(*) FROM raid_logs WHERE event_type = 'CAPTCHA_TEXT_FAIL'").fetchone()[0]
                num_fail_cnt = cur.execute("SELECT COUNT(*) FROM raid_logs WHERE event_type = 'CAPTCHA_FAIL_NUMBERS'").fetchone()[0]
                timeout_cnt = cur.execute("SELECT COUNT(*) FROM raid_logs WHERE event_type = 'CAPTCHA_TIMEOUT'").fetchone()[0]
                ban_cnt = cur.execute("SELECT COUNT(*) FROM raid_logs WHERE event_type = 'RAID_AUTO_BAN'").fetchone()[0]
                raid_cnt = cur.execute("SELECT COUNT(*) FROM raid_logs WHERE event_type = 'RAID_TRIGGERED'").fetchone()[0]
                conn.close()

                is_raid_active = RAID_MODE_ACTIVE.get(event.chat_id, False)
                status_str = "🚨 **РЕЖИМ РЕЙДА АКТИВЕН (АВТО-БАН)!**" if is_raid_active else "🟢 **Штатный режим**"

                report = (
                    "📊 **ОТЧЁТ СИСТЕМЫ ЩИТА И КАПЧИ**\n\n"
                    f"**Статус чата:** {status_str}\n\n"
                    f"🧩 **Математические капчи:**\n"
                    f"• Выдано при входе: `{sent_cnt}`\n"
                    f"• Успешно решено: `{pass_cnt}`\n"
                    f"• Кикнуто за ВВОД ТЕКСТА: `{text_fail_cnt}`\n"
                    f"• Мьютов за ошибки чисел: `{num_fail_cnt}`\n"
                    f"• Мьютов за ТАЙМАУТ (3 мин): `{timeout_cnt}`\n\n"
                    f"⚡ **Анти-Рейд Защита:**\n"
                    f"• Срабатываний рейда: `{raid_cnt}`\n"
                    f"• Авто-забанено рейдеров: `{ban_cnt}`"
                )
                return await safe_reply(event, report, filename="shield_report.txt")

            # 6. Логи
            elif sub2 in ["logs", "log"]:
                conn = sqlite3.connect(DB_NAME)
                cur = conn.cursor()
                rows = cur.execute("""
                    SELECT timestamp, event_type, user_id, full_name, details 
                    FROM raid_logs 
                    ORDER BY id DESC LIMIT 50
                """).fetchall()
                conn.close()

                if not rows:
                    return await event.reply("📄 **Логи системы Щита пусты.**")

                lines = ["📜 **ПОСЛЕДНИЕ 50 СОБЫТИЙ СИСТЕМЫ ЩИТА:**\n"]
                for r in rows:
                    ts, ev, uid, name, det = r
                    lines.append(f"• [{ts}] **{ev}** | {name} (`{uid}`) — {det}")

                full_log_text = "\n".join(lines)
                return await safe_reply(event, full_log_text, filename="shield_events_log.txt")

            else:
                return await event.reply("❌ Неизвестная подкоманда. Напишите `sudo shield` для справки.")

        elif subcommand in ["log", "logs"]:
            logs = memory_log_handler.get_logs(limit=20)
            if not logs:
                return await event.reply("📄 **Консольные логи пока пусты.**")

            log_text = "\n".join(logs)
            reply_text = f"📜 **Последние логи юзербота:**\n\n```text\n{log_text}\n```"
            await safe_reply(event, reply_text, filename="userbot_console_log.txt")

        elif subcommand == "add_regex" and value:
            if db_add_regex(value): await event.reply(f"✅ Регекс `{value}` добавлен.")
            else: await event.reply(f"⚠️ Регекс `{value}` уже есть в базе.")
        
        elif subcommand == "del_regex" and value:
            if db_del_regex(value): await event.reply(f"🗑 Регекс `{value}` удален.")
            else: await event.reply(f"❌ Регекс `{value}` не найден.")
        
        elif subcommand == "list_regex":
            items = db_list_regex()
            text = "🧹 **Список регексов (люди):**\n" + ("\n".join([f"• `{item}`" for item in items]) if items else "Пусто.")
            await safe_reply(event, text, filename="regex_list.txt")

        elif subcommand == "add_banword" and value:
            subparts = value.split()
            word = subparts[0]
            delay = 45
            if len(subparts) > 1 and subparts[1].isdigit():
                delay = int(subparts[1])
                
            db_add_banword(word, delay)
            await event.reply(f"✅ Бан-слово `{word}` сохранено (задержка: **{delay}** сек).")

        elif subcommand == "del_banword" and value:
            if db_del_banword(value.strip()): await event.reply(f"🗑 Бан-слово `{value}` удалено.")
            else: await event.reply(f"❌ Бан-слово `{value}` не найдено.")

        elif subcommand == "list_banwords":
            items = db_list_banwords()
            text = "🚫 **Список бан-слов для ботов:**\n" + (
                "\n".join([f"• `{word}` | задержка: **{delay}** сек" for word, delay in items]) 
                if items else "Пусто."
            )
            await safe_reply(event, text, filename="banwords_list.txt")

        elif subcommand in ["add_exc", "add_exception"] and value:
            if db_add_exception(value): await event.reply(f"🛡️ Слово-исключение `{value}` добавлено.")
            else: await event.reply(f"⚠️ Слово-исключение `{value}` уже есть в базе.")

        elif subcommand in ["del_exc", "del_exception"] and value:
            if db_del_exception(value.strip()): await event.reply(f"🗑 Слово-исключение `{value}` удалено.")
            else: await event.reply(f"❌ Слово-исключение `{value}` не найдено.")

        elif subcommand in ["list_exc", "list_exceptions"]:
            items = db_list_exceptions()
            text = "🛡️ **Список слов-исключений (защита от удаления):**\n" + (
                "\n".join([f"• `{item}`" for item in items]) if items else "Пусто."
            )
            await safe_reply(event, text, filename="exceptions_list.txt")
            
        elif subcommand == "set_delay_user" and value:
            if value.isdigit():
                db_set('delay_user_command', value)
                await event.reply(f"⏱ Задержка для команд людей изменена на **{value}** сек.")
            else: await event.reply("❌ Значение должно быть числом.")

        else:
            await event.reply("❌ Неизвестная команда. Напишите `sudo` для справки.")
            
    except Exception as e:
        await event.reply(f"**Ошибка выполнения:**\n`{e}`")

async def main():
    if not all([API_ID, API_HASH, SESSION_STRING]):
        logging.critical("ОШИБКА: Не найдены переменные окружения!")
        return

    init_db()
    await client.start()
    me = await client.get_me()
    logging.info(f"Юзербот-снайпер запущен как: {me.first_name} (@{me.username})")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
