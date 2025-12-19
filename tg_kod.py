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

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "https://example.com")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"  # token bilan obfuscation
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", "8080"))

# === Quick sanity checks ===
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment o'zgaruvchisini sozlang")
if WEBHOOK_HOST.startswith("http://"):
    print("[WARN] WEBHOOK_HOST http bo'lsa, TLS tavsiya etiladi (https).")

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
    # "?" ni psycopg2 uchun "%s" ga almashtiramiz
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

# ===== STATES (oddiy xotira) =====
wait_code = {}      # user_id -> True (kino kodini kiritish holati)
current_code = {}   # user_id -> oxirgi kiritilgan kino kodi

# ===== USER SAVE =====
def save_user(user):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(q("""
                    insert into users (user_id, first_name, username)
                    values (?, ?, ?)
                    on conflict (user_id) do nothing
                """), (user.id, user.first_name, user.username))
        print(f"[INFO] User saved: id={user.id}, username={user.username}")
    except Exception as e:
        print(f"[ERROR] save_user failed: {e}")

# ===== START =====
@dp.message(Command("start"))
async def start(msg: Message):
    save_user(msg.from_user)
    kb = ADMIN_MENU if msg.from_user.id == ADMIN_ID else USER_MENU
    await msg.answer("👋 Kino botga xush kelibsiz", reply_markup=kb)

# ===== USER FLOW: kino topish =====
@dp.message(lambda m: m.text == "🎬 Kino topish")
async def ask_code(msg: Message):
    wait_code[msg.from_user.id] = True
    await msg.answer("🎬 Kino kodini kiriting:")
    print(f"[DEBUG] Wait code enabled for user_id={msg.from_user.id}")

@dp.message(lambda m: wait_code.get(m.from_user.id))
async def get_movie(msg: Message):
    code = msg.text.strip()
    print(f"[DEBUG] User {msg.from_user.id} requested movie code: {code}")

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(q("select title from movies where code=?"), (code,))
                movie = cur.fetchone()
                if not movie:
                    print(f"[INFO] No movie found for code={code}")
                    await msg.answer("❌ Bunday kod topilmadi")
                    return

                # qismlar ro'yxati (id, title, views)
                cur.execute(q("""
                    select id, title, views
                    from parts
                    where movie_code=?
                    order by id
                """), (code,))
                parts = cur.fetchall()

        wait_code.pop(msg.from_user.id, None)
        current_code[msg.from_user.id] = code

        if not parts:
            await msg.answer(f"🎬 {movie[0]}\nHozircha qismlar mavjud emas.")
            print(f"[INFO] No parts for code={code}")
            return

        # Inline tugmalar
        kb = InlineKeyboardMarkup()
        for idx, part in enumerate(parts):
            kb.add(InlineKeyboardButton(
                text=f"{idx+1}-qism ({part[2]} marta ko‘rilgan)",
                callback_data=f"part_{part[0]}"
            ))

        text = f"🎬 {movie[0]}\nQismlardan birini tanlang:"
        print(f"[DEBUG] Movie '{movie[0]}' parts count={len(parts)} for code={code}")
        await msg.answer(text, reply_markup=kb)

    except Exception as e:
        print(f"[ERROR] get_movie failed for code={code}: {e}")
        await msg.answer("⚠️ Xatolik yuz berdi, keyinroq urinib ko‘ring.")

# ===== CALLBACK: qismni yuborish va views oshirish =====
@dp.callback_query(lambda c: c.data.startswith("part_"))
async def send_part(call: CallbackQuery):
    part_id = int(call.data.split("_")[1])
    user_id = call.from_user.id
    print(f"[DEBUG] User {user_id} clicked part_id={part_id}")

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Qismni olish
                cur.execute(q("select title, description, video, views from parts where id=?"), (part_id,))
                part = cur.fetchone()
                if not part:
                    print(f"[INFO] Part not found: id={part_id}")
                    await call.message.answer("❌ Qism topilmadi")
                    await call.answer()
                    return

                # Viewsni oshirish
                cur.execute(q("update parts set views = views + 1 where id=?"), (part_id,))
                conn.commit()

        # Video yuborish
        title, description, video_file_id, views_before = part
        caption = f"{title}\n{description}\n👁 {views_before + 1} marta ko‘rilgan"
        await call.message.answer_video(video_file_id, caption=caption)

        print(f"[INFO] Part sent: id={part_id}, views {views_before} -> {views_before + 1}, user_id={user_id}")
        await call.answer()

    except Exception as e:
        print(f"[ERROR] send_part failed: part_id={part_id}, user_id={user_id}, error={e}")
        await call.message.answer("⚠️ Qismni yuborishda xatolik yuz berdi.")
        await call.answer()

# ===== USER MOVIES STAT =====
@dp.message(lambda m: m.text == "📊 Kinolar statistikasi")
async def user_movies_stats(msg: Message):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(q("""
                    select m.title, sum(p.views) as total_views
                    from movies m
                    join parts p on m.code = p.movie_code
                    group by m.title
                    order by total_views desc
                """))
                data = cur.fetchall()

        if not data:
            await msg.answer("🎬 Hozircha kinolar mavjud emas.")
            print("[INFO] No movies for stats")
            return

        text = "🎬 Kinolar statistikasi:\n\n"
        for row in data:
            text += f"{row[0]} — {row[1]} marta ko‘rilgan\n"

        print(f"[DEBUG] Stats rows={len(data)}")
        await msg.answer(text)

    except Exception as e:
        print(f"[ERROR] user_movies_stats failed: {e}")
        await msg.answer("⚠️ Statistikani olishda xatolik yuz berdi.")

# ===== WEBHOOK =====
async def on_startup(app):
    try:
        await bot.set_webhook(WEBHOOK_URL)
        print(f"[INFO] Webhook set: {WEBHOOK_URL}")
    except Exception as e:
        print(f"[ERROR] set_webhook failed: {e}")

async def on_shutdown(app):
    await bot.session.close()
    print("[INFO] Bot session closed")

def main():
    app = web.Application()
    SimpleRequestHandler(dp, bot).register(app, path=WEBHOOK_PATH)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    print(f"[INFO] Running app on port {PORT}, path={WEBHOOK_PATH}")
    web.run_app(app, port=PORT)

if __name__ == "__main__":
    main()
