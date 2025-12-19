import os
import psycopg2
from aiogram import Bot, Dispatcher
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")  # https://your-app.onrender.com
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 8080))

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# ================== DATABASE ==================
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

# ================== MENUS ==================
USER_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎬 Kino topish")],
        [KeyboardButton(text="📩 Adminga murojaat")]
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Kino qo'shish")],
        [KeyboardButton(text="📊 User statistikasi")],
        [KeyboardButton(text="📢 Xabar yuborish")],
        [KeyboardButton(text="🎬 All Films")],
        [KeyboardButton(text="🗑 Kino o'chirish")],
        [KeyboardButton(text="📺 Channels")],
        [KeyboardButton(text="🔙 Asosiy menyu")]
    ],
    resize_keyboard=True
)

def parts_menu(count: int):
    kb, row = [], []
    for i in range(1, count + 1):
        row.append(KeyboardButton(text=f"{i}-qism"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([KeyboardButton(text="🔙 Asosiy menyu")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ================== STATES ==================
wait_code = {}
wait_part = {}
current_code = {}
temp_video = {}
broadcast_wait = {}
delete_wait = {}
channels_wait = {}
mandatory_channels = []

# ================== USER SAVE ==================
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

# ================== STAT FUNCTIONS ==================
def total_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("select count(*) from users")
    r = cur.fetchone()[0]
    conn.close()
    return r

def today_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("select count(*) from users where joined_at::date = current_date")
    r = cur.fetchone()[0]
    conn.close()
    return r

def monthly_stats():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        select to_char(joined_at, 'YYYY-MM'), count(*)
        from users
        group by 1
        order by 1 desc
    """)
    r = cur.fetchall()
    conn.close()
    return r

def get_all_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("select user_id from users")
    r = [x[0] for x in cur.fetchall()]
    conn.close()
    return r

# ================== MOVIE STAT ==================
def increment_movie_view(code):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(q("""
        update movies set views = views + 1
        where code=?
    """), (code,))
    conn.commit()
    conn.close()

def all_movies_stat():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(q("""
        select m.code, m.title, count(p.id)
        from movies m
        left join parts p on m.code = p.movie_code
        group by m.code, m.title
        order by m.title
    """))
    r = cur.fetchall()
    conn.close()
    return r

# ================== START ==================
@dp.message(Command("start"))
async def start(msg: Message):
    save_user(msg.from_user)
    kb = ADMIN_MENU if msg.from_user.id == ADMIN_ID else USER_MENU
    # Check mandatory channels if they exist
    if mandatory_channels and msg.from_user.id != ADMIN_ID:
        text = "📢 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:\n"
        for ch in mandatory_channels:
            text += f"▫️ {ch}\n"
        await msg.answer(text)
        return
    await msg.answer("👋 Kino botga xush kelibsiz", reply_markup=kb)

# ================== USER FLOW ==================
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
        select title, description, video
        from parts
        where movie_code=?
        order by id
    """), (code,))
    parts = cur.fetchall()
    conn.close()

    wait_code.pop(msg.from_user.id)
    current_code[msg.from_user.id] = code

    if len(parts) == 1:
        await msg.answer_video(parts[0][2], caption=f"{parts[0][0]}\n\n{parts[0][1]}")
        increment_movie_view(code)
        return

    wait_part[msg.from_user.id] = True
    await msg.answer("Qismni tanlang:", reply_markup=parts_menu(len(parts)))

@dp.message(lambda m: wait_part.get(m.from_user.id) and m.text.endswith("-qism"))
async def send_part(msg: Message):
    idx = int(msg.text.replace("-qism", "")) - 1
    code = current_code[msg.from_user.id]

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(q("""
        select title, description, video
        from parts
        where movie_code=?
        order by id
    """), (code,))
    parts = cur.fetchall()
    conn.close()

    await msg.answer_video(
        parts[idx][2],
        caption=f"{parts[idx][0]}\n\n{parts[idx][1]}"
    )

    increment_movie_view(code)

    wait_part.pop(msg.from_user.id)
    current_code.pop(msg.from_user.id)

# ================== ADMIN FUNCTIONS ==================
@dp.message(lambda m: m.text == "➕ Kino qo'shish" and m.from_user.id == ADMIN_ID)
async def add_movie(msg: Message):
    await msg.answer("🎥 Videoni yuboring")

@dp.message(lambda m: m.video and m.from_user.id == ADMIN_ID)
async def save_video(msg: Message):
    temp_video[msg.from_user.id] = msg.video.file_id
    await msg.answer("📄 Format:\nKOD | QISM NOMI | IZOH")

@dp.message(lambda m: "|" in m.text and m.from_user.id == ADMIN_ID)
async def save_movie(msg: Message):
    code, title, desc = [x.strip() for x in msg.text.split("|", 2)]
    video = temp_video.get(msg.from_user.id)

    conn = get_conn()
    cur = conn.cursor()
    # Add movie if not exists
    cur.execute(q("""
        insert into movies(code, title, views)
        values (?, ?, 0)
        on conflict (code) do nothing
    """), (code, title))
    # Add part
    cur.execute(q("""
        insert into parts(movie_code, title, description, video)
        values (?, ?, ?, ?)
    """), (code, title, desc, video))
    conn.commit()
    conn.close()

    temp_video.pop(msg.from_user.id)
    await msg.answer("✅ Kino qo‘shildi")

# ================== USER STATISTICS ==================
@dp.message(lambda m: m.text == "📊 User statistikasi" and m.from_user.id == ADMIN_ID)
async def stats(msg: Message):
    text = (
        f"📊 USER STATISTIKASI\n\n"
        f"👥 Jami: {total_users()}\n"
        f"📅 Bugun: {today_users()}\n\n"
        "🗓 Oylik:\n"
    )
    for mth, cnt in monthly_stats():
        text += f"▫️ {mth} — {cnt}\n"
    await msg.answer(text)

# ================== BROADCAST ==================
@dp.message(lambda m: m.text == "📢 Xabar yuborish" and m.from_user.id == ADMIN_ID)
async def start_bc(msg: Message):
    broadcast_wait[msg.from_user.id] = True
    await msg.answer("📢 Yuboriladigan xabarni jo‘nating")

@dp.message(lambda m: broadcast_wait.get(m.from_user.id) and m.from_user.id == ADMIN_ID)
async def do_bc(msg: Message):
    users = get_all_users()
    ok, bad = 0, 0

    for uid in users:
        try:
            await msg.copy_to(uid)
            ok += 1
        except TelegramBadRequest:
            bad += 1
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(q("delete from users where user_id=?"), (uid,))
            conn.commit()
            conn.close()

    broadcast_wait.pop(msg.from_user.id)
    await msg.answer(f"✅ Yuborildi: {ok}\n❌ Block: {bad}")

# ================== ALL FILMS ==================
@dp.message(lambda m: m.text == "🎬 All Films" and m.from_user.id == ADMIN_ID)
async def all_films(msg: Message):
    films = all_movies_stat()
    text = "🎬 BARCHA KINOLAR\n\n"
    for code, title, part_count in films:
        text += f"▫️ {title} | Kod: {code} | Qism: {part_count}\n"
    await msg.answer(text)

# ================== DELETE MOVIE ==================
@dp.message(lambda m: m.text == "🗑 Kino o'chirish" and m.from_user.id == ADMIN_ID)
async def delete_movie(msg: Message):
    delete_wait[msg.from_user.id] = True
    await msg.answer("❌ Kino kodini kiriting (butun kino yoki qismni o'chirish uchun):")

@dp.message(lambda m: delete_wait.get(m.from_user.id) and m.from_user.id == ADMIN_ID)
async def do_delete(msg: Message):
    code = msg.text.strip()
    conn = get_conn()
    cur = conn.cursor()
    # delete all parts
    cur.execute(q("delete from parts where movie_code=?"), (code,))
    # delete movie
    cur.execute(q("delete from movies where code=?"), (code,))
    conn.commit()
    conn.close()
    delete_wait.pop(msg.from_user.id)
    await msg.answer(f"✅ Kino va barcha qismlar o'chirildi: {code}")

# ================== CHANNELS ==================
@dp.message(lambda m: m.text == "📺 Channels" and m.from_user.id == ADMIN_ID)
async def channels(msg: Message):
    channels_wait[msg.from_user.id] = True
    await msg.answer("📺 Kanal nomini qo'shing yoki o'chirish uchun '-' qo'shing:")

@dp.message(lambda m: channels_wait.get(m.from_user.id) and m.from_user.id == ADMIN_ID)
async def do_channels(msg: Message):
    text = msg.text.strip()
    if text.startswith("-"):
        ch = text[1:].strip()
        if ch in mandatory_channels:
            mandatory_channels.remove(ch)
            await msg.answer(f"❌ Kanal o'chirildi: {ch}")
        else:
            await msg.answer("❌ Kanal topilmadi")
    else:
        if text not in mandatory_channels:
            mandatory_channels.append(text)
            await msg.answer(f"✅ Kanal qo'shildi: {text}")
    channels_wait.pop(msg.from_user.id)

# ================== WEBHOOK ==================
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
