# CODEVER: v2.7 | Sniper Userbot for LEX (Full File with JSON Raid Generator)
import os
import re
import sys
import io
import json
import asyncio
import sqlite3
import logging
import subprocess
from datetime import datetime
from collections import deque
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User

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

    def get_logs(self, limit=15):
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

# --- ПОДКЛЮЧЕНИЕ И АВТО-ОЧИСТКА БД SQLITE ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS regex_patterns (id INTEGER PRIMARY KEY, pattern TEXT UNIQUE)")
    cur.execute("CREATE TABLE IF NOT EXISTS bot_banwords (id INTEGER PRIMARY KEY, word TEXT UNIQUE, delay INTEGER DEFAULT 45)")
    cur.execute("CREATE TABLE IF NOT EXISTS bot_exceptions (id INTEGER PRIMARY KEY, word TEXT UNIQUE)")
    cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    
    cur.execute("DELETE FROM bot_banwords WHERE word IS NULL OR TRIM(word) = '' OR word LIKE '%(д|н|м%'")
    cur.execute("DELETE FROM regex_patterns WHERE pattern IS NULL OR TRIM(pattern) = ''")
    cur.execute("DELETE FROM bot_exceptions WHERE word IS NULL OR TRIM(word) = ''")
    
    try:
        cur.execute("ALTER TABLE bot_banwords ADD COLUMN delay INTEGER DEFAULT 45")
    except sqlite3.OperationalError:
        pass
        
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('delay_user_command', '5')")
    conn.commit()
    conn.close()
    logging.info("База данных инициализирована и защищена.")

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
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
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

    is_bot = getattr(sender, 'bot', False) if isinstance(sender, User) else False
    sender_username = getattr(sender, 'username', '') or 'unknown'

    if sender_username.lower() == LEX_BOT_USERNAME.lower() or text.lower().startswith("sudo"):
        return

    if is_bot:
        short_text = text[:35].replace('\n', ' ')
        logging.info(f"📩 Бот @{sender_username} прислал: '{short_text}...'")

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

    if command == "sudo" and not subcommand:
        delay_user = db_get_int('delay_user_command', 5)
        help_text = (
            "🛡️ **LEX Sniper Userbot v2.7 активен!**\n\n"
            "**⚡ Очистка Ботнета (Anti-Raid):**\n"
            "• `sudo raid` — Собрать ботов за СЕГОДНЯ и отправить .json файл\n"
            "• `sudo raid YYYY-MM-DD` — Собрать ботов за конкретную дату\n\n"
            "**Просмотр логов:**\n"
            "• `sudo log` — Показать последние логи юзербота\n\n"
            "**Триггеры для людей (Regex):**\n"
            "• `sudo add_regex {выражение}` | `sudo del_regex` | `sudo list_regex`\n\n"
            "**Бан-слова для ботов (Banwords):**\n"
            "• `sudo add_banword {слово} {сек}` | `sudo del_banword` | `sudo list_banwords`\n\n"
            "**🛡️ Исключения для ботов (Whitelist):**\n"
            "• `sudo add_exc {слово}` | `sudo del_exc` | `sudo list_exc`"
        )
        return await event.reply(help_text)

    try:
        # 🎯 СКОРОСТНОЕ СКАНИРОВАНИЕ И ГЕНЕРАЦИЯ JSON ДЛЯ ЛЕКСА
        if subcommand in ["raid", "scan_raid"]:
            target_date_str = datetime.now().strftime("%Y-%m-%d")
            
            if value and re.match(r"^\d{4}-\d{2}-\d{2}$", value.strip()):
                target_date_str = value.strip()

            status_msg = await event.reply(f"⏳ **Юзербот сканирует Журнал недавних действий сервера за {target_date_str}...**")

            try:
                chat = await event.get_chat()
                collected_bots = []

                async for log_event in client.iter_admin_log(chat, join=True):
                    user = log_event.user
                    if not user or user.bot:
                        continue

                    event_date_str = log_event.date.strftime("%Y-%m-%d")
                    if event_date_str == target_date_str:
                        user_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Участник"
                        time_str = log_event.date.strftime("%H:%M:%S")
                        collected_bots.append({
                            "id": user.id,
                            "username": user.username or "",
                            "name": user_name,
                            "time": time_str
                        })

                if not collected_bots:
                    return await status_msg.edit(f"🔍 **В Журнале недавних действий за дату `{target_date_str}` зашедших не найдено.**")

                # Сортируем от первого зашедшего к последнему
                collected_bots.reverse()
                total_count = len(collected_bots)
                first_bot = collected_bots[0]
                last_bot = collected_bots[-1]

                # Упаковываем структуру в JSON-файл в памяти
                payload_data = {
                    "target_date": target_date_str,
                    "total_count": total_count,
                    "first_bot": first_bot,
                    "last_bot": last_bot,
                    "bots": collected_bots
                }

                json_bytes = json.dumps(payload_data, ensure_ascii=False, indent=2).encode("utf-8")
                json_file = io.BytesIO(json_bytes)
                json_file.name = f"raid_{target_date_str}.json"

                report_text = (
                    f"🎯 **СНАЙПЕР СОБРАЛ РЕЙД-БОТНЕТ!**\n\n"
                    f"📊 **Всего найдено:** `{total_count}` ботов\n"
                    f"📅 **Дата:** `{target_date_str}`\n\n"
                    f"🥇 **Первый:** {first_bot['name']} (@{first_bot['username'] or 'нет'}) | `{first_bot['id']}` в {first_bot['time']}\n"
                    f"🏁 **Последний:** {last_bot['name']} (@{last_bot['username'] or 'нет'}) | `{last_bot['id']}` в {last_bot['time']}\n\n"
                    f"📄 **Сформирован файл `raid_{target_date_str}.json`!**\n"
                    f"Сделайте **реплай (ответить)** на этот файл командой `/raid` для передачи Лексу."
                )

                await status_msg.delete()
                await client.send_file(event.chat_id, file=json_file, caption=report_text)

            except Exception as e:
                await status_msg.edit(f"❌ **Ошибка сканирования юзерботом:**\n`{e}`")

        elif subcommand in ["log", "logs"]:
            logs = memory_log_handler.get_logs(limit=20)
            if not logs:
                return await event.reply("📄 **Логи пока пусты.**")

            log_text = "\n".join(logs)
            if len(log_text) > 3900:
                log_text = log_text[-3900:]

            reply_text = f"📜 **Последние логи юзербота:**\n\n```text\n{log_text}\n```"
            await event.reply(reply_text)

        elif subcommand == "add_regex" and value:
            if db_add_regex(value): await event.reply(f"✅ Регекс `{value}` добавлен.")
            else: await event.reply(f"⚠️ Регекс `{value}` уже есть в базе.")
        
        elif subcommand == "del_regex" and value:
            if db_del_regex(value): await event.reply(f"🗑 Регекс `{value}` удален.")
            else: await event.reply(f"❌ Регекс `{value}` не найден.")
        
        elif subcommand == "list_regex":
            items = db_list_regex()
            text = "🧹 **Список регексов (люди):**\n" + ("\n".join([f"• `{item}`" for item in items]) if items else "Пусто.")
            await event.reply(text)

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
            await event.reply(text)

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
            await event.reply(text)
            
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
