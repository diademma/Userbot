# CODEVER: v2.2 | Sniper Userbot for LEX (Auto-Clean DB & Detailed Logs)
import os
import re
import asyncio
import sqlite3
import logging
import subprocess
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User

# --- НАСТРОЙКИ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
    cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    
    # Автоматическое удаление фантомных/пустых записей, если они попали в БД
    cur.execute("DELETE FROM bot_banwords WHERE word IS NULL OR TRIM(word) = ''")
    cur.execute("DELETE FROM regex_patterns WHERE pattern IS NULL OR TRIM(pattern) = ''")
    
    try:
        cur.execute("ALTER TABLE bot_banwords ADD COLUMN delay INTEGER DEFAULT 45")
    except sqlite3.OperationalError:
        pass
        
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('delay_user_command', '5')")
    conn.commit()
    conn.close()
    logging.info("База данных инициализирована и очищена от фантомных записей.")

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С БД ---
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

# --- ГЛАВНАЯ ЛОГИКА ЮЗЕРБОТА ---

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

    # НАДЕЖНОЕ ПОЛУЧЕНИЕ ОТПРАВИТЕЛЯ (запрос к Telegram, если нет в кэше)
    sender = await event.get_sender()
    if not sender:
        return

    is_bot = getattr(sender, 'bot', False) if isinstance(sender, User) else False
    sender_username = getattr(sender, 'username', '') or 'unknown'

    # Игнорируем Лекса и команды sudo
    if sender_username.lower() == LEX_BOT_USERNAME.lower() or text.lower().startswith("sudo"):
        return

    # --- СЦЕНАРИЙ 1: Сообщение от ЧЕЛОВЕКА ---
    if not is_bot:
        patterns = db_list_regex()
        for pattern in patterns:
            try:
                if re.match(pattern, text, re.IGNORECASE):
                    delay = db_get_int('delay_user_command', 5)
                    reason = f"Регекс человека: '{pattern}'"
                    logging.info(f"🚨 [ЧЕЛОВЕК @{sender_username}] {reason}. Удаление через {delay} сек.")
                    asyncio.create_task(delete_after(event, delay, reason))
                    return
            except re.error as e:
                logging.error(f"Ошибка в регулярном выражении '{pattern}': {e}")

    # --- СЦЕНАРИЙ 2: Сообщение от БОТА ---
    else:
        banwords = db_list_banwords()
        for word, delay in banwords:
            word_clean = word.strip()
            if not word_clean:
                continue

            matched = False
            try:
                # Проверяем как регекс и как подстроку
                if re.search(word_clean, text, re.IGNORECASE):
                    matched = True
            except re.error:
                if word_clean.lower() in text.lower():
                    matched = True

            if matched:
                reason = f"Бан-слово бота: '{word_clean}'"
                logging.info(f"🚨 [БОТ @{sender_username}] {reason}. Удаление через {delay} сек.")
                asyncio.create_task(delete_after(event, delay, reason))
                return

@client.on(events.NewMessage(from_users=OWNER_ID, pattern=r"^sudo.*"))
async def owner_commands_handler(event):
    parts = event.raw_text.split()
    command = parts[0]
    subcommand = parts[1] if len(parts) > 1 else None
    value = " ".join(parts[2:]) if len(parts) > 2 else None

    if command == "sudo" and not subcommand:
        delay_user = db_get_int('delay_user_command', 5)
        help_text = (
            "🛡️ **LEX Sniper Userbot v2.2 активен!**\n\n"
            "**Триггеры для людей (Regex):**\n"
            "• `sudo add_regex {выражение}`\n"
            "• `sudo del_regex {выражение}`\n"
            "• `sudo list_regex`\n\n"
            "**Бан-слова для ботов (Banwords / Regex):**\n"
            "• `sudo add_banword {слово} {секунды}`\n"
            "• `sudo del_banword {слово}`\n"
            "• `sudo list_banwords`\n\n"
            "**Задержки:**\n"
            f"• `sudo set_delay_user {delay_user}` (для людей)"
        )
        return await event.reply(help_text)

    try:
        if subcommand == "add_regex" and value:
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
