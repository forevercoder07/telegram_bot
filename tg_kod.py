import json
import os
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "TOKENINGIZNI_BUYERGA_QOYING")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")  # Render URL
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}" if BASE_WEBHOOK_URL else None

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

MOVIES_FILE = "movies.json"
SETTINGS_FILE = "settings.json"

# --- User state ---
user_waiting_code = {}
user_waiting_part = {}

# --- Helper functions ---
def load_movies():
    try:
        with open(MOVIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_movies(movies):
    with open(MOVIES_FILE, "w", encoding="utf-8") as f:
        json.dump(movies, f, ensure_ascii=False, indent=2)

def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"channels": []}

def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

def get_channels():
    return load_settings().get("channels", [])

def main_menu(is_admin=False):
    buttons = [
        [KeyboardButton("🎬 Kino topish")],
        [KeyboardButton("📊 Statistika")],
        [KeyboardButton("📽 Tavsiya")],
        [KeyboardButton("📩 Admin")]
    ]
    if is_admin:
        admin_buttons = [
            [KeyboardButton("➕ Kino qo‘shish")],
            [KeyboardButton("📚 Barcha kinolar")],
            [KeyboardButton("⚙️ Kanallar")],
            [KeyboardButton("🛠 Repair")],
            [KeyboardButton("🔁 Migratsiya")]
        ]
        buttons.extend(admin_buttons)
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

async def is_subscribed_all(user_id):
    channels = get_channels()
    if not channels:
        return True
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=f"@{ch.lstrip('@')}", user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except:
            return False
    return True

async def send_subscription_panel(message: Message):
    channels = get_channels()
    if not channels:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(f"{i+1}-kanal ↗", url=f"https://t.me/{ch.lstrip('@')}")]
        for i, ch in enumerate(channels)
    ])
    kb.add(InlineKeyboardButton("✅ Tasdiqlash", callback_data="check_sub"))
    await message.answer("❗ Botdan foydalanish uchun kanallarga obuna bo‘ling:", reply_markup=kb)

# --- Handlers ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    kb = main_menu(is_admin=(user_id == ADMIN_ID))
    if not await is_subscribed_all(user_id):
        await send_subscription_panel(message)
        return
    await message.answer("👋 Assalomu alaykum!\n🎬 Kino olamiga xush kelibsiz!", reply_markup=kb)

@dp.message(lambda m: m.text == "🎬 Kino topish")
async def btn_search(message: Message):
    user_id = message.from_user.id
    if not await is_subscribed_all(user_id):
        await send_subscription_panel(message)
        return
    user_waiting_code[user_id] = True
    await message.answer("Kino kodini kiriting:")

@dp.message(lambda m: m.text == "🔙 Asosiy menyu")
async def back_main(message: Message):
    user_id = message.from_user.id
    kb = main_menu(is_admin=(user_id == ADMIN_ID))
    user_waiting_code.pop(user_id, None)
    await message.answer("Asosiy menyu:", reply_markup=kb)

# --- Webhook / Health ---
async def on_startup(dispatcher: Dispatcher, bot: Bot):
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        print(f"Webhook o'rnatildi: {WEBHOOK_URL}")

async def on_shutdown(dispatcher: Dispatcher, bot: Bot):
    await bot.delete_webhook()
    print("Webhook o'chirildi")

async def health_check(request):
    return web.Response(text="OK")

def main():
    if not WEBHOOK_URL:
        print("❌ Webhook URL yo'q, polling ishlatiladi.")
        asyncio.run(dp.start_polling(bot))
        return

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    handler.register(app, WEBHOOK_PATH)
    app.router.add_get("/", health_check)

    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
