import os
import psycopg2
from aiogram import Bot, Dispatcher
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "https://example.com")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", "8080"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable belgilanmagan")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= DATABASE =================
def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", "5432"),
        connect_timeout=5
    )

def q(sql: str):
    return sql.replace("?", "%s")

# ================= MENUS =================
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

# ================= STATES =================
wait_code: dict[int, bool] = {}
current_code: dict[int, str] = {}

# ================= USER SAVE =================
def save_user(user):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(q("""
                    INSERT INTO users (user_id, first_name, username)
                    VALUES (?, ?, ?)
                    ON CONFLICT (user_id) DO NOTHING
                """), (user.id, user.first_name, user.username))
    except Exception as e:
        print(f"[ERROR] save_user: {e}")

# ================= START =================
@dp.message(Command("start"))
async def start(msg: Message):
    save_user(msg.from_user)
    kb = ADMIN_MENU if msg.from_user.id == ADMIN_ID else USER_MENU
    await msg.answer("👋 Kino botga xush kelibsiz", reply_markup=kb)

# ================= USER FLOW =================
@dp.message(lambda m: m.text == "🎬 Kino topish")
async def ask_code(msg: Message):
    wait_code[msg.from_user.id] = True
    await msg.answer("🎬 Kino kodini kiriting:")

@dp.message(lambda m: wait_code.get(m.from_user.id))
async def get_movie(msg: Message):
    code = msg.text.strip()

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(q("SELECT title FROM movies WHERE code=?"), (code,))
                movie = cur.fetchone()
                if not movie:
                    await msg.answer("❌ Bunday kod topilmadi")
                    return

                cur.execute(q("""
                    SELECT id, title, views
                    FROM parts
                    WHERE movie_code=?
                    ORDER BY id
                """), (code,))
                parts = cur.fetchall()

        wait_code.pop(msg.from_user.id, None)
        current_code[msg.from_user.id] = code

        if not parts:
            await msg.answer(f"🎬 {movie[0]}\nHozircha qismlar mavjud emas.")
            return

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"{i+1}-qism ({p[2]} marta ko‘rilgan)",
                        callback_data=f"part_{p[0]}"
                    )
                ]
                for i, p in enumerate(parts)
            ]
        )

        await msg.answer(
            f"🎬 {movie[0]}\nQismlardan birini tanlang:",
            reply_markup=kb
        )

    except Exception as e:
        print(f"[ERROR] get_movie: {e}")
        await msg.answer("⚠️ Xatolik yuz berdi")

# ================= CALLBACK =================
@dp.callback_query(lambda c: c.data and c.data.startswith("part_"))
async def send_part(call: CallbackQuery):
    part_id = int(call.data.split("_")[1])

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    q("SELECT title, description, video, views FROM parts WHERE id=?"),
                    (part_id,)
                )
                part = cur.fetchone()
                if not part:
                    await call.message.answer("❌ Qism topilmadi")
                    await call.answer()
                    return

                cur.execute(q(
                    "UPDATE parts SET views = views + 1 WHERE id=?"
                ), (part_id,))
                conn.commit()

        title, desc, video_id, views = part
        await call.message.answer_video(
            video_id,
            caption=f"{title}\n{desc}\n👁 {views + 1} marta ko‘rilgan"
        )
        await call.answer()

    except Exception as e:
        print(f"[ERROR] send_part: {e}")
        await call.message.answer("⚠️ Xatolik yuz berdi")
        await call.answer()

# ================= STATS =================
@dp.message(lambda m: m.text == "📊 Kinolar statistikasi")
async def stats(msg: Message):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(q("""
                    SELECT m.title, SUM(p.views)
                    FROM movies m
                    JOIN parts p ON m.code = p.movie_code
                    GROUP BY m.title
                    ORDER BY SUM(p.views) DESC
                """))
                data = cur.fetchall()

        if not data:
            await msg.answer("🎬 Hozircha ma'lumot yo‘q")
            return

        text = "🎬 Kinolar statistikasi:\n\n"
        for title, views in data:
            text += f"{title} — {views} marta\n"

        await msg.answer(text)

    except Exception as e:
        print(f"[ERROR] stats: {e}")
        await msg.answer("⚠️ Xatolik yuz berdi")

# ================= WEBHOOK =================
async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    print("[INFO] Webhook o‘rnatildi")

async def on_shutdown(app):
    await bot.session.close()
    print("[INFO] Bot to‘xtadi")

def main():
    app = web.Application()
    SimpleRequestHandler(dp, bot).register(app, path=WEBHOOK_PATH)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, port=PORT)

if __name__ == "__main__":
    main()
