import os
from telethon import TelegramClient, events

# Данные берём из Railway (Variables)
api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]
session = os.environ.get("SESSION", "anon")

client = TelegramClient(session, api_id, api_hash)

@client.on(events.NewMessage(pattern="!привет"))
async def handler(event):
    await event.respond("Привет! Я твой юзербот 🚀")

print("Юзербот запущен...")
client.start()
client.run_until_disconnected()
