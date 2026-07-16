# CODEVER: v2.0 | Sniper Userbot for LEX (Direct Admin Deletion Version)
import os
import re
import asyncio
import sqlite3
import logging
import subprocess
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# --- НАСТРОЙКИ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ID вашего ОСНОВНОГО аккаунта (с которого вы будете отправлять команды sudo)
OWNER_ID = 5421909121 
# Юзернейм вашего основного бота LEX (без @)
LEX_BOT_USERNAME = "my_LEX_superbot"
DB_NAME = "userbot_memory.db"

# --- СОХРАНЕНИЕ БД В GITHUB ---
def save_db_to_git():
    """Автоматически коммитит и пушит файл базы данных в ваш репозиторий GitHub"""
    try:
        status = subprocess.run(["git", "status", "--porcelain", DB_NAME], capture_output=True, text=True)
        if not status.stdout.strip():
            logging.info("Изменений в базе данных нет, пуш отменен.")
            return

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
        
        subprocess.run(["git", "add", DB_NAME], check=True)
        subprocess.run(["git", "commit", "-m", "chore: update userbot memory [skip ci]"], check=True)
        subprocess.run(["git", "push"], check=True)
        logging.info("База данных успешно закоммичена и отправлена в репозиторий GitHub.")
    except Exception as e:
        logging.warning(f"Не удалось сохранить базу данных в Git: {e}")

# --- ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ SQLITE ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    # Таблица для регулярных выражений (для сообщений людей)
    cur.execute("CREATE TABLE IF NOT EXISTS regex_patterns (id INTEGER PRIMARY KEY, pattern TEXT UNIQUE)")
    # Таблица для бан-слов с колонкой delay (время задержки для каждого слова)
    cur.execute("CREATE TABLE IF NOT EXISTS bot_banwords (id INTEGER PRIMARY KEY, word TEXT UNIQUE, delay INTEGER DEFAULT 45)")
    # Таблица для настроек
    cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    
    # Автомиграция для старой базы данных (добавление колонки delay, если её нет)
    try:
        cur.execute("ALTER TABLE bot_banwords ADD COLUMN delay INTEGER DEFAULT 45")
        logging.info("Успешно выполнена миграция базы данных (добавлена колонка delay).")
    except sqlite3.OperationalError:
        pass # Колонка уже существует
        
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('delay_user_command', '5')")
    
    conn.commit()
    conn.close()
    logging.info("База данных успешно инициализирована.")

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С БД ---
def db_add_regex(pattern):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT INTO regex_patterns (pattern) VALUES (?)", (pattern,))
        conn.commit()
        conn.close()
        save_db_to_git()
        return True
    except sqlite3.IntegrityError:
        return False

def db_add_banword(word, delay=45):
    try:
        conn = sqlite3.connect(DB_NAME)
        # INSERT OR REPLACE обновит время задержки (delay), если слово уже существует в базе
        conn.execute("INSERT OR REPLACE INTO bot_banwords (word, delay) VALUES (?, ?)", (word, delay))
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
    cur.execute("DELETE FROM regex_patterns WHERE pattern = ?", (pattern,))
    changes = conn.total_changes
    conn.commit()
    conn.close()
    if changes > 0:
        save_db_to_git()
    return changes > 0

def db_del_banword(word):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM bot_banwords WHERE word = ?", (word,))
    changes = conn.total_changes
    conn.commit()
    conn.close()
    if changes > 0:
        save_db_to_git()
    return changes > 0

def db_list_regex():
    conn = sqlite3.connect(DB_NAME)
    items = conn.execute("SELECT pattern FROM regex_patterns").fetchall()
    conn.close()
    return [item[0] for item in items]

def db_list_banwords():
    conn = sqlite3.connect(DB_NAME)
    # Возвращает список кортежей (слово, время_задержки)
    items = conn.execute("SELECT word, delay FROM bot_banwords").fetchall()
    conn.close()
    return items

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
    return int(row[0]) if (row and row[0].isdigit()) else default

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

async def delete_after(event, delay):
    """Напрямую удаляет сообщение в группе без отправки сервисных сообщений"""
    try:
        await asyncio.sleep(delay)
        await event.delete()
        logging.info(f"Сообщение {event.id} успешно удалено напрямую.")
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение {event.id}: {e}")

@client.on(events.NewMessage(incoming=True, func=lambda e: not e.is_private))
async def message_handler(event):
    """Перехватывает все сообщения в группах и каналах"""
    text = event.raw_text.strip() if event.raw_text else None
    if not text:
        return

    # Игнорируем только сообщения самого Лекса и управляющие команды владельца (начинающиеся с "sudo")
    if (event.sender and event.sender.username == LEX_BOT_USERNAME) or text.lower().startswith("sudo"):
        return

    # --- СЦЕНАРИЙ 1: Сообщение от ЧЕЛОВЕКА ---
    if event.sender and not event.sender.bot:
        patterns = db_list_regex()
        for pattern in patterns:
            try:
                # Проверяем совпадение СТРОГО С НАЧАЛА строки
                if re.match(pattern, text, re.IGNORECASE):
                    logging.info(f"Найдено совпадение с регексом '{pattern}' в начале сообщения человека. Удаляю напрямую.")
                    delay = db_get_int('delay_user_command', 5)
                    asyncio.create_task(delete_after(event, delay))
                    return
            except re.error:
                logging.error(f"Ошибка в регулярном выражении: {pattern}")

    # --- СЦЕНАРИЙ 2: Сообщение от БОТА ---
    elif event.sender and event.sender.bot:
        banwords = db_list_banwords()
        for word, delay in banwords:
            if word.lower() in text.lower():
                logging.info(f"Найдено бан-слово '{word}' у бота. Удаляю напрямую через {delay} сек.")
                asyncio.create_task(delete_after(event, delay))
                return

@client.on(events.NewMessage(from_users=OWNER_ID, pattern=r"^sudo.*"))
async def owner_commands_handler(event):
    """Обрабатывает все команды от владельца"""
    parts = event.raw_text.split()
    command = parts[0]
    subcommand = parts[1] if len(parts) > 1 else None
    value = " ".join(parts[2:]) if len(parts) > 2 else None

    # --- Главная команда sudo ---
    if command == "sudo" and not subcommand:
        delay_user = db_get_int('delay_user_command', 5)
        
        help_text = (
            "🛡️ **LEX Sniper Userbot (Админ-режим) активен!**\n\n"
            "Я управляю чатом Onyx напрямую с правами администратора.\n\n"
            "**Настройка триггеров для людей (Regex):**\n"
            "• `sudo add_regex {выражение}`\n"
            "• `sudo del_regex {выражение}`\n"
            "• `sudo list_regex`\n\n"
            "**Настройка бан-слов для ботов (Banwords):**\n"
            "• `sudo add_banword {слово} {секунды}` (без секунд = 45с)\n"
            "• `sudo del_banword {слово}`\n"
            "• `sudo list_banwords`\n\n"
            "**Настройка задержек (в секундах):**\n"
            f"• `sudo set_delay_user {delay_user}` (для команд людей)"
        )
        return await event.reply(help_text)

    # --- Обработка субкоманд ---
    try:
        if subcommand == "add_regex" and value:
            if db_add_regex(value): await event.reply(f"✅ Регекс `{value}` добавлен.")
            else: await event.reply(f"⚠️ Регекс `{value}` уже есть в базе.")
        
        elif subcommand == "del_regex" and value:
            if db_del_regex(value): await event.reply(f"🗑 Регекс `{value}` удален.")
            else: await event.reply(f"❌ Регекс `{value}` не найден.")
        
        elif subcommand == "list_regex":
            items = db_list_regex()
            text = "🧹 **Список регексов:**\n" + ("\n".join([f"• `{item}`" for item in items]) if items else "Пусто.")
            await event.reply(text)

        elif subcommand == "add_banword" and value:
            subparts = value.split()
            word = subparts[0]
            delay = 45  # По умолчанию
            if len(subparts) > 1 and subparts[1].isdigit():
                delay = int(subparts[1])
                
            db_add_banword(word, delay)
            await event.reply(f"✅ Бан-слово `{word}` сохранено (задержка удаления: **{delay}** сек).")

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
