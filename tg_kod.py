import os
import psycopg2
from aiogram import Bot, Dispatcher
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 8080))

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# ===== DATABASE =====
def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", "5432")
    )

def q(sql: str):
    return sql.replace("?", "%s")

# ===== MENUS =====
USER_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎬 Kino topish")],
        [KeyboardButton(text="📊 Kinolar statistikasi")],
        [KeyboardButton(text="📩 Adminga murojaat")]
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Kino qo'shish")],
        [KeyboardButton(text="📊 User statistikasi")],
        [KeyboardButton(text="📢 Xabar yuborish")],
        [KeyboardButton(text="🗑 Kino o'chirish")],
        [KeyboardButton(text="📺 Channels")],
        [KeyboardButton(text="🎞 All films")],
        [KeyboardButton(text="🔙 Asosiy menyu")]
    ],
    resize_keyboard=True
)

# ===== STATES =====
wait_code = {}
wait_part = {}
current_code = {}

# ===== USER SAVE =====
def save_user(user):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(q("""
        insert into users (user_id, first_name, username)
        values (?, ?, ?)
        on conflict (user_id) do nothing
    """), (user.id, user.first_name, user.username))
    conn.commit()
    conn.close()

# ===== START =====
@dp.message(Command("start"))
async def start(msg: Message):
    save_user(msg.from_user)
    kb = ADMIN_MENU if msg.from_user.id == ADMIN_ID else USER_MENU
    await msg.answer("👋 Kino botga xush kelibsiz", reply_markup=kb)

# ===== USER FLOW =====
@dp.message(lambda m: m.text == "🎬 Kino topish")
async def ask_code(msg: Message):
    wait_code[msg.from_user.id] = True
    await msg.answer("🎬 Kino kodini kiriting:")

@dp.message(lambda m: wait_code.get(m.from_user.id))
async def get_movie(msg: Message):
    code = msg.text.strip()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(q("select title from movies where code=?"), (code,))
    movie = cur.fetchone()
    if not movie:
        await msg.answer("❌ Bunday kod topilmadi")
        return

    cur.execute(q("""
        select title, description, video, views
        from parts
        where movie_code=?
        order by id
    """), (code,))
    parts = cur.fetchall()
    conn.close()

    wait_code.pop(msg.from_user.id)
    current_code[msg.from_user.id] = code

    text = f"🎬 {movie[0]}\n\n"
    for idx, part in enumerate(parts):
        text += f"{idx+1}-qism: {part[3]} marta ko‘rilgan\n"
    await msg.answer(text)

# ===== USER MOVIES STAT =====
@dp.message(lambda m: m.text == "📊 Kinolar statistikasi")
async def user_movies_stats(msg: Message):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(q("""
        select m.title, sum(p.views) as total_views
        from movies m
        join parts p on m.code = p.movie_code
        group by m.title
        order by total_views desc
    """))
    data = cur.fetchall()
    conn.close()

    if not data:
        await msg.answer("🎬 Hozircha kinolar mavjud emas.")
        return

    text = "🎬 Kinolar statistikasi:\n\n"
    for row in data:
        text += f"{row[0]} — {row[1]} marta ko‘rilgan\n"

    await msg.answer(text)

# ===== WEBHOOK =====
async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(app):
    await bot.session.close()

def main():
    app = web.Application()
    SimpleRequestHandler(dp, bot).register(app, path=WEBHOOK_PATH)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, port=PORT)

if __name__ == "__main__":
    main()
