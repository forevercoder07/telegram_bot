# tg_kod.py
import os
import json
import sqlite3
import random
from typing import Optional
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1629210003"))
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "https://your-app.onrender.com")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("PORT", "8080"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Database file path ---
DB_FILE = os.getenv("DB_FILE", "movies.db")

def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS movies (
        code TEXT PRIMARY KEY,
        title TEXT,
        views INTEGER DEFAULT 0
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS parts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movie_code TEXT,
        title TEXT,
        description TEXT,
        video TEXT,
        FOREIGN KEY(movie_code) REFERENCES movies(code) ON DELETE CASCADE
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    conn.commit()
    conn.close()
    print("[INIT_DB] Database initialized or already exists.")

# --- Settings helpers ---
def get_setting(key: str) -> Optional[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def set_setting(key: str, value: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def del_setting(key: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit()
    conn.close()

def get_channels() -> list[str]:
    v = get_setting("channels")
    if not v:
        return []
    try:
        return json.loads(v)
    except Exception:
        return []

def save_channels_list(channels: list[str]):
    set_setting("channels", json.dumps(channels, ensure_ascii=False))
    print(f"[SAVE_CHANNELS] channels_saved_count={len(channels)}")

def set_temp_video(admin_id: int, file_id: str):
    key = f"temp_video:{admin_id}"
    set_setting(key, file_id)
    print(f"[SET_TEMP_VIDEO] admin_id={admin_id} file_id={file_id}")

def get_temp_video(admin_id: int) -> Optional[str]:
    key = f"temp_video:{admin_id}"
    return get_setting(key)

def del_temp_video(admin_id: int):
    key = f"temp_video:{admin_id}"
    del_setting(key)
    print(f"[DEL_TEMP_VIDEO] admin_id={admin_id}")

def has_migrated() -> bool:
    return get_setting("migrated") == "1"

def set_migrated():
    set_setting("migrated", "1")
    print("[SET_MIGRATED] migration flag set")

# --- Movie CRUD ---
def add_movie_part(code: str, title: str, description: str, video: str):
    print(f"[DB_ADD_PART] code={code} title={title} video_present={bool(video)}")
    if not video:
        print(f"[DB_ADD_PART_ERROR] code={code} no_video_provided")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO movies (code, title, views) VALUES (?, ?, 0)", (code, title))
    cur.execute("UPDATE movies SET title = ? WHERE code = ? AND (title IS NULL OR title = '')", (title, code))
    cur.execute("SELECT 1 FROM parts WHERE movie_code=? AND video=? LIMIT 1", (code, video))
    if cur.fetchone():
        print(f"[DB_ADD_PART_SKIP] code={code} duplicate_video")
    else:
        cur.execute(
            "INSERT INTO parts (movie_code, title, description, video) VALUES (?, ?, ?, ?)",
            (code, title, description, video)
        )
        conn.commit()
        cur.execute("SELECT id, movie_code, title, description, video FROM parts WHERE movie_code=? ORDER BY id", (code,))
        rows = cur.fetchall()
        print(f"[DB_ADD_PART_AFTER] code={code} parts_count={len(rows)} last_part={rows[-1] if rows else None}")
    conn.close()

def get_all_movies() -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT code, title, views FROM movies")
    movies = {}
    for code, title, views in cur.fetchall():
        cur.execute("SELECT title, description, video FROM parts WHERE movie_code=? ORDER BY id", (code,))
        parts = [{"title": p[0], "description": p[1], "video": p[2]} for p in cur.fetchall()]
        movies[code] = {"title": title, "views": views, "parts": parts}
    conn.close()
    print(f"[DB_GET_ALL] total_movies={len(movies)}")
    return movies

def get_movie(code: str) -> Optional[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT title, views FROM movies WHERE code=?", (code,))
    row = cur.fetchone()
    if not row:
        conn.close()
        print(f"[DB_GET_MOVIE] code={code} not_found")
        return None
    title, views = row
    cur.execute("SELECT title, description, video FROM parts WHERE movie_code=? ORDER BY id", (code,))
    parts_raw = cur.fetchall()
    parts = [{"title": p[0], "description": p[1], "video": p[2]} for p in parts_raw]
    conn.close()
    print(f"[DB_GET_MOVIE] code={code} title={title} views={views} parts_count={len(parts)} parts_videos={[p.get('video') for p in parts]}")
    return {"title": title, "views": views, "parts": parts}

def increment_view(code: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE movies SET views = views + 1 WHERE code=?", (code,))
    conn.commit()
    conn.close()
    print(f"[INCREMENT_VIEW] code={code}")

def delete_movie(code: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM parts WHERE movie_code=?", (code,))
    cur.execute("DELETE FROM movies WHERE code=?", (code,))
    conn.commit()
    conn.close()
    print(f"[DELETE_MOVIE] code={code}")

def delete_movie_part(code: str, part_index: int) -> Optional[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM parts WHERE movie_code=? ORDER BY id", (code,))
    rows = cur.fetchall()
    if part_index < 0 or part_index >= len(rows):
        conn.close()
        print(f"[DELETE_PART_FAIL] code={code} part_index={part_index} out_of_range")
        return None
    part_id, part_title = rows[part_index]
    cur.execute("DELETE FROM parts WHERE id=?", (part_id,))
    conn.commit()
    conn.close()
    print(f"[DELETE_PART] code={code} part_index={part_index} title={part_title}")
    return {"title": part_title}

def update_part_video(code: str, part_index: Optional[int], video_file_id: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM parts WHERE movie_code=? ORDER BY id", (code,))
    rows = cur.fetchall()
    if part_index is None:
        if rows:
            last_id = rows[-1][0]
            cur.execute("UPDATE parts SET video=? WHERE id=?", (video_file_id, last_id))
            print(f"[UPDATE_PART_VIDEO] code={code} updated_last_part_id={last_id}")
        else:
            cur.execute("INSERT OR IGNORE INTO movies (code, title, views) VALUES (?, ?, 0)", (code, code))
            cur.execute("INSERT INTO parts (movie_code, title, description, video) VALUES (?, ?, ?, ?)",
                        (code, code, "", video_file_id))
            print(f"[UPDATE_PART_VIDEO] code={code} created_part_with_video")
    else:
        if 0 <= part_index < len(rows):
            pid = rows[part_index][0]
            cur.execute("UPDATE parts SET video=? WHERE id=?", (video_file_id, pid))
            print(f"[UPDATE_PART_VIDEO] code={code} updated_part_index={part_index} id={pid}")
        else:
            conn.close()
            print(f"[UPDATE_PART_VIDEO_FAIL] code={code} part_index={part_index} out_of_range")
            return False
    conn.commit()
    conn.close()
    return True

# --- JSON migration ---
def migrate_json_to_sqlite(json_path: str = "movies.json"):
    if has_migrated():
        print("[MIGRATE] already migrated, skipping.")
        return
    if not os.path.exists(json_path):
        print(f"[MIGRATE] {json_path} not found, skipping migration.")
        set_migrated()
        return
    with open(json_path, "r", encoding="utf-8") as f:
        try:
            movies = json.load(f)
        except Exception as e:
            print(f"[MIGRATE_ERROR] failed to load json: {e}")
            return
    conn = get_conn()
    cur = conn.cursor()
    for code, info in movies.items():
        title = info.get("title", code)
        views = info.get("views", 0)
        cur.execute("INSERT OR IGNORE INTO movies (code, title, views) VALUES (?, ?, ?)", (code, title, views))
        parts = info.get("parts", [])
        if parts:
            for part in parts:
                p_title = part.get("title", title)
                p_desc = part.get("description", "")
                p_video = part.get("video", "")
                if p_video:
                    cur.execute("SELECT 1 FROM parts WHERE movie_code=? AND video=? LIMIT 1", (code, p_video))
                    if cur.fetchone():
                        print(f"[MIGRATE_SKIP] code={code} video_exists, skipping")
                        continue
                cur.execute("INSERT INTO parts (movie_code, title, description, video) VALUES (?, ?, ?, ?)",
                            (code, p_title, p_desc, p_video))
        else:
            video = info.get("video", "")
            desc = info.get("description", "")
            if video:
                cur.execute("SELECT 1 FROM parts WHERE movie_code=? AND video=? LIMIT 1", (code, video))
                if not cur.fetchone():
                    cur.execute("INSERT INTO parts (movie_code, title, description, video) VALUES (?, ?, ?, ?)",
                                (code, title, desc, video))
    conn.commit()
    conn.close()
    set_migrated()
    print("[MIGRATE] migration finished.")

# --- Utilities ---
def is_invite_link(s: str) -> bool:
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return ("t.me/" in s) and ("/+" in s or s.startswith("https://t.me/+") or s.startswith("http://t.me/+"))
    return False

def normalize_channel_input(s: str) -> str:
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s.rstrip("/")
    return "@" + s.lstrip("@")

async def is_subscribed_all_diagnostic(user_id: int):
    channels = get_channels()
    if not channels:
        return True, {"not_subscribed": [], "inaccessible": [], "invite_only": []}
    not_subscribed = []
    inaccessible = []
    invite_only = []
    for ch in channels:
        ch_str = ch.strip()
        if is_invite_link(ch_str):
            invite_only.append(ch_str)
            continue
        name = ch_str.lstrip("@").strip()
        if not name:
            inaccessible.append((ch_str, "Empty channel name"))
            continue
        chat_id = f"@{name}"
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                not_subscribed.append(ch_str)
        except Exception as e:
            inaccessible.append((ch_str, str(e)))
    ok = (len(not_subscribed) == 0 and len(inaccessible) == 0)
    return ok, {"not_subscribed": not_subscribed, "inaccessible": inaccessible, "invite_only": invite_only}

# --- Keyboards ---
MAIN_BUTTONS_USER = [
    [KeyboardButton(text="🎬 Kino topish")],
    [KeyboardButton(text="📊 Statistika")],
    [KeyboardButton(text="📽 Kino tavsiyasi")],
    [KeyboardButton(text="📩 Adminga murojaat")]
]
MAIN_BUTTONS_ADMIN = [
    [KeyboardButton(text="➕ Kino qo'shish")],
    [KeyboardButton(text="📚 Barcha kinolar")],
    [KeyboardButton(text="⚙️ Kanallarni boshqarish")],
    [KeyboardButton(text="🛠 Repair")],
    [KeyboardButton(text="🔁 Migratsiya")],
    [KeyboardButton(text="🗑 Kino o'chirish")]
]

def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    buttons = [*MAIN_BUTTONS_USER]
    if is_admin:
        buttons.extend(MAIN_BUTTONS_ADMIN)
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def parts_menu(parts_count: int) -> ReplyKeyboardMarkup:
    rows = []
    row = []
    for i in range(1, parts_count + 1):
        row.append(KeyboardButton(text=f"{i}-qism"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(text="🔙 Asosiy menyu")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def channels_panel_markup(channels: list[str]):
    buttons = []
    for idx, ch in enumerate(channels, start=1):
        label = f"{idx}-kanal ↗"
        if is_invite_link(ch):
            url = ch
        else:
            url = f"https://t.me/{ch.lstrip('@')}"
        buttons.append([InlineKeyboardButton(text=label, url=url)])
    buttons.append([InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def send_subscription_panel(to):
    channels = get_channels()
    if not channels:
        return
    text = (
        "❗ Botdan foydalanish uchun pastdagi kanallarga obuna bo'ling.\n"
        "Keyin ✅ Tasdiqlash tugmasini bosing.\n\n"
        "Agar havola t.me/+... bo'lsa, qo'shilish so'rovi yuboriladi va tasdiqlangach a'zo bo'lasiz."
    )
    kb = channels_panel_markup(channels)
    if isinstance(to, Message):
        await to.answer(text, reply_markup=kb)
    else:
        await to.message.answer(text, reply_markup=kb)

def is_button_text(text: str) -> bool:
    base = {
        "🎬 Kino topish", "📊 Statistika", "📽 Kino tavsiyasi", "📩 Adminga murojaat",
        "➕ Kino qo'shish", "📚 Barcha kinolar", "⚙️ Kanallarni boshqarish",
        "🛠 Repair", "🔁 Migratsiya", "🔙 Asosiy menyu", "🗑 Kino o'chirish"
    }
    if text in base:
        return True
    if text.endswith("-qism") and text[:-5].isdigit():
        return True
    return False

# --- Per-user state ---
user_waiting_code: dict[int, bool] = {}
user_waiting_part: dict[int, bool] = {}
user_current_code: dict[int, str] = {}
admin_repair_code: dict[int, str] = {}

# --- Handlers ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    user_waiting_code.pop(user_id, None)
    user_waiting_part.pop(user_id, None)
    user_current_code.pop(user_id, None)
    kb = main_menu(is_admin=(user_id == ADMIN_ID))
    ok, info = await is_subscribed_all_diagnostic(user_id)
    welcome_text = (
        "👋 Assalomu alaykum!\n\n"
        "📽 Kino botga xush kelibsiz. Kod orqali kino toping yoki admin bilan bog'laning."
    )
    if not ok:
        text = "❗ Obuna tekshiruvida muammo:\n"
        if info["not_subscribed"]:
            text += "Obuna bo'lish kerak bo'lgan kanallar:\n"
            for i, c in enumerate(info["not_subscribed"], start=1):
                text += f"{i}-kanal: {c}\n"
        if info["inaccessible"]:
            text += "\nKanal bilan muammo:\n"
            for ch, err in info["inaccessible"]:
                text += f"{ch} — {err}\n"
        if info.get("invite_only"):
            text += (
                "\nℹ️ Invite-link kanallar (t.me/+...) qo'shilish so'rovi yuboriladi. "
                "Tasdiqlash tugmasini bosing va kuting.\n"
            )
        await message.answer(text)
        await send_subscription_panel(message)
        return
    await message.answer(welcome_text, reply_markup=kb)

@dp.message(lambda m: m.text == "🎬 Kino topish")
async def btn_search(message: Message):
    user_waiting_code[message.from_user.id] = True
    user_waiting_part.pop(message.from_user.id, None)
    user_current_code.pop(message.from_user.id, None)
    await message.answer("Kino kodini kiriting:")

@dp.message(lambda m: m.text == "📊 Statistika")
async def btn_stats(message: Message):
    ok, _ = await is_subscribed_all_diagnostic(message.from_user.id)
    if not ok:
        await send_subscription_panel(message)
        return
    movies = get_all_movies()
    if not movies:
        await message.answer("Hozircha statistikada kino yo'q.")
        return
    top = sorted(movies.items(), key=lambda x: x[1].get("views", 0), reverse=True)
    lines = ["📊 Eng ko'p ko'rilgan kinolar:\n"]
    for i, (code, info) in enumerate(top, start=1):
        lines.append(f"{i}. {info.get('title', code)} (Kod: {code}) — {info.get('views', 0)} marta")
    await message.answer("\n".join(lines))

@dp.message(lambda m: m.text == "📽 Kino tavsiyasi")
async def btn_recommend(message: Message):
    ok, _ = await is_subscribed_all_diagnostic(message.from_user.id)
    if not ok:
        await send_subscription_panel(message)
        return
    movies = get_all_movies()
    if not movies:
        await message.answer("Hozircha tavsiya uchun kinolar yo'q.")
        return
    multi = [(code, info) for code, info in movies.items() if info.get("parts")]
    if not multi:
        await message.answer("Hozircha tavsiya uchun kinolar yo'q.")
        return
    code, info = random.choice(multi)
    part = random.choice(info["parts"])
    video_id = part.get("video")
    if not video_id:
        await message.answer("❌ Tavsiya qilingan qism uchun video topilmadi.")
        return
    increment_view(code)
    try:
        await message.answer_video(video=video_id, caption=f"🎬 {part.get('title','')}\n\n📝 {part.get('description','')}\n\n💡 Tavsiya qilindi")
    except TelegramBadRequest:
        await message.answer("❌ Tavsiya qilingan qism uchun video yuborib bo'lmadi.")

@dp.message(lambda m: m.text == "📩 Adminga murojaat")
async def btn_contact(message: Message):
    await message.answer("Adminga murojaat: https://t.me/forever_projects")

@dp.message(lambda m: m.text == "🔙 Asosiy menyu")
async def btn_back_to_main(message: Message):
    user_id = message.from_user.id
    user_waiting_part.pop(user_id, None)
    user_waiting_code.pop(user_id, None)
    user_current_code.pop(user_id, None)
    kb = main_menu(is_admin=(user_id == ADMIN_ID))
    await message.answer("Asosiy menyu.", reply_markup=kb)

# --- Admin flows ---
@dp.message(lambda m: m.text == "➕ Kino qo'shish" and m.from_user.id == ADMIN_ID)
async def btn_add_movie(message: Message):
    await message.answer("Videoni yuboring, keyin matn yuboring: Kod | Qism nomi | Sharh")

@dp.message(lambda m: m.video and m.from_user.id == ADMIN_ID)
async def admin_receive_video(message: Message):
    file_id = message.video.file_id
    set_temp_video(message.from_user.id, file_id)
    print(f"[ADMIN_VIDEO] admin_id={message.from_user.id} file_id={file_id} file_size={getattr(message.video, 'file_size', None)} mime_type={getattr(message.video, 'mime_type', None)}")
    await message.answer("✅ Video qabul qilindi.\nEndi matn yuboring: Kod | Qism nomi | Sharh")

@dp.message(lambda m: m.text and "|" in m.text and m.from_user.id == ADMIN_ID)
async def admin_receive_info(message: Message):
    try:
        parts = message.text.split("|", maxsplit=2)
        if len(parts) < 3:
            await message.answer("❌ Format noto'g'ri. To'g'ri format: Kod | Qism nomi | Sharh")
            return
        code, part_title, desc = map(lambda s: s.strip(), parts)
        video_id = get_temp_video(message.from_user.id)
        print(f"[ADMIN_INFO] admin_id={message.from_user.id} code={code} part_title={part_title} desc_len={len(desc)} video_id={video_id}")
        if not video_id:
            await message.answer("❗ Avval video yuboring yoki /cancel bilan qayta urinib ko'ring.")
            print(f"[ADMIN_INFO_ERROR] admin_id={message.from_user.id} no_temp_video_found")
            return
        add_movie_part(code, part_title, desc, video_id)
        del_temp_video(message.from_user.id)
        print(f"[ADMIN_INFO_SAVED] admin_id={message.from_user.id} code={code} video_saved={video_id}")
        try:
            await message.answer_video(video=video_id, caption=f"🎬 {part_title}\n\n📝 {desc}")
        except TelegramBadRequest as e:
            print(f"[ADMIN_PREVIEW_ERROR] admin_id={message.from_user.id} code={code} error={e}")
            await message.answer("✅ Qism qo'shildi, lekin preview yuborilmadi (file_id muammosi).")
        await message.answer("✅ Qism qo'shildi.")
    except Exception as e:
        print(f"[ADMIN_INFO_EXCEPTION] error={e}")
        await message.answer("❌ Format noto'g'ri. To'g'ri format: Kod | Qism nomi | Sharh")

@dp.message(lambda m: m.text == "📚 Barcha kinolar" and m.from_user.id == ADMIN_ID)
async def btn_list_movies(message: Message):
    movies = get_all_movies()
    if not movies:
        await message.answer("Hozircha kino yo'q.")
        return
    lines = ["📚 Barcha kinolar:\n"]
    for code, info in movies.items():
        lines.append(f"🎬 {info.get('title', code)} (Kod: {code}) — qismlar: {len(info.get('parts', []))}")
    text = "\n".join(lines)
    for i in range(0, len(text), 3500):
        await message.answer(text[i:i+3500])

@dp.message(lambda m: m.text == "⚙️ Kanallarni boshqarish" and m.from_user.id == ADMIN_ID)
async def edit_channels_start(message: Message):
    current = get_channels()
    existing = "\n".join(f"{i+1}-kanal: {ch}" for i, ch in enumerate(current, start=1)) if current else "— Mavjud emas —"
    await message.answer(
        "Kanallar ro'yxatini yuboring (har birini alohida qatorda).\n"
        "Qabul qilinadi: @kanal yoki https://t.me/kanal yoki https://t.me/+invite_link\n\n"
        f"Hozirgi ro'yxat:\n{existing}"
    )
    user_current_code[message.from_user.id] = "__editing_channels__"

@dp.message(lambda m: m.from_user.id == ADMIN_ID and m.text and m.text.strip() and user_current_code.get(m.from_user.id) == "__editing_channels__")
async def edit_channels_apply(message: Message):
    lines = [ln.strip() for ln in message.text.splitlines() if ln.strip()]
    channels = []
    for ln in lines:
        if ln.startswith("http://") or ln.startswith("https://"):
            channels.append(ln.rstrip("/"))
        else:
            ln = ln.lstrip("@").strip()
            if ln:
                channels.append("@" + ln)
    save_channels_list(channels)
    user_current_code.pop(message.from_user.id, None)
    kb = channels_panel_markup(channels)
    await message.answer("✅ Kanallar yangilandi. Foydalanuvchilarga ko'rinishi:", reply_markup=kb)

@dp.callback_query(lambda c: c.data == "check_sub")
async def check_subscription(callback: CallbackQuery):
    ok, info = await is_subscribed_all_diagnostic(callback.from_user.id)
    if ok:
        await callback.message.answer("✅ Obuna tasdiqlandi. /start ni bosing.")
        await cmd_start(callback.message)
    else:
        text = "❌ Hozircha to'liq obuna aniqlanmadi.\n"
        if info["not_subscribed"]:
            text += "Obuna bo'lish kerak bo'lgan kanallar:\n"
            for i, c in enumerate(info["not_subscribed"], start=1):
                text += f"{i}-kanal: {c}\n"
        if info["inaccessible"]:
            text += "\nKanal bilan muammo:\n"
            for ch, err in info["inaccessible"]:
                text += f"{ch} — {err}\n"
        if info.get("invite_only"):
            text += (
                "\nℹ️ Invite-link (t.me/+...) kanallarga qo'shilish so'rovi yuboriladi. "
                "Tasdiqlashni kuting, keyin /start bosing.\n"
            )
        await callback.message.answer(text)
        await send_subscription_panel(callback)

@dp.message(lambda m: m.text == "🛠 Repair" and m.from_user.id == ADMIN_ID)
async def btn_repair_help(message: Message):
    await message.answer("Foydalanish: /repair <KOD> yoki /repair <KOD> <QISM_RAQAMI>\nBuyruqdan so'ng yangi videoni yuboring.")

@dp.message(Command("repair"))
async def cmd_repair(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Foydalanish: /repair <KOD> yoki /repair <KOD> <QISM_RAQAMI>")
        return
    code = parts[1].strip()
    qism_index: Optional[int] = None
    if len(parts) >= 3 and parts[2].isdigit():
        qism_index = int(parts[2]) - 1
    movie = get_movie(code)
    if not movie:
        await message.answer("Bunday kod topilmadi.")
        return
    admin_repair_code[message.from_user.id] = json.dumps({"code": code, "part": qism_index})
    await message.answer(f"✅ Kod {code} uchun video qabul qilish rejimi yoqildi. Yangi videoni yuboring.")

@dp.message(lambda m: m.video and m.from_user.id == ADMIN_ID and m.from_user.id in admin_repair_code)
async def admin_receive_repair_video(message: Message):
    raw = admin_repair_code.pop(message.from_user.id, None)
    if not raw:
        await message.answer("Repair rejimi topilmadi. /repair bilan qayta urinib ko'ring.")
        return
    data = json.loads(raw)
    code = data.get("code")
    part_idx = data.get("part")
    movie = get_movie(code)
    if not movie:
        await message.answer("Kod topilmadi, bekor qilindi.")
        return
    ok = update_part_video(code, part_idx, message.video.file_id)
    if not ok:
        await message.answer("❌ Qism topilmadi. Repair bekor qilindi.")
        return
    await message.answer("✅ Video yangilandi va saqlandi.")

@dp.message(lambda m: m.text == "🔁 Migratsiya" and m.from_user.id == ADMIN_ID)
async def btn_migrate_help(message: Message):
    await cmd_migrate(message)

@dp.message(Command("migrate"))
async def cmd_migrate(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    migrate_json_to_sqlite()
    await message.answer("✅ Migratsiya bajarildi (agar movies.json mavjud bo'lsa).")

@dp.message(lambda m: m.text == "🗑 Kino o'chirish" and m.from_user.id == ADMIN_ID)
async def btn_delete_movie(message: Message):
    await message.answer(
        "🗑 O'chirish rejimi.\n\n"
        "Format:\n"
        "- Butun kino: /delete <KOD>\n"
        "- Faqat qism: /delete <KOD> <QISM_RAQAMI>\n\n"
        "Masalan:\n/delete A123\n/delete A123 2"
    )

@dp.message(Command("delete"))
async def cmd_delete(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Format noto'g'ri. Foydalanish: /delete <KOD> yoki /delete <KOD> <QISM_RAQAMI>")
        return
    code = parts[1].strip()
    qism_index: Optional[int] = None
    if len(parts) >= 3 and parts[2].isdigit():
        qism_index = int(parts[2]) - 1
    movie = get_movie(code)
    if not movie:
        await message.answer("❌ Bunday kod topilmadi.")
        return
    if qism_index is None:
        delete_movie(code)
        await message.answer(f"✅ Kod {code} uchun butun kino o‘chirildi.")
    else:
        res = delete_movie_part(code, qism_index)
        if res:
            await message.answer(f"✅ Kod {code} uchun {qism_index+1}-qism o‘chirildi.\n🎬 {res.get('title','')}")
        else:
            await message.answer("❌ Bunday qism topilmadi.")

# --- Text flow handler ---
@dp.message(lambda m: m.text and not is_button_text(m.text))
async def handle_text_flow(message: Message):
    text = message.text.strip()
    user_id = message.from_user.id

    if user_waiting_part.get(user_id):
        print(f"[USER_PART_SELECT] user_id={user_id} text={text} waiting_part=True current_code={user_current_code.get(user_id)}")
        if text.endswith("-qism") and text[:-5].isdigit():
            idx = int(text[:-5]) - 1
        elif text.isdigit():
            idx = int(text) - 1
        else:
            await message.answer("Qism raqamini tanlang (masalan: 1-qism) yoki 🔙 Asosiy menyuga qayting.")
            return
        code = user_current_code.get(user_id)
        movie = get_movie(code) if code else None
        if not code or not movie:
            await message.answer("❌ Qism tanlash konteksti yo'qoldi. Iltimos, '🎬 Kino topish'dan qayta urinib ko'ring.")
            user_waiting_part.pop(user_id, None)
            user_current_code.pop(user_id, None)
            return
        parts = movie.get("parts", [])
        if idx < 0 or idx >= len(parts):
            await message.answer("❌ Bunday qism mavjud emas. Tugmalardan tanlang.")
            return
        part = parts[idx]
        video_id = part.get("video")
        print(f"[USER_PART_RESOLVE] user_id={user_id} code={code} idx={idx} part_video={video_id}")
        if not video_id:
            await message.answer("❌ Ushbu qism uchun video topilmadi.")
            return
        increment_view(code)
        try:
            await message.answer_video(video=video_id, caption=f"🎬 {part.get('title','')}\n\n📝 {part.get('description','')}")
        except TelegramBadRequest as e:
            print(f"[USER_PART_SEND_ERROR] user_id={user_id} code={code} idx={idx} error={e}")
            await message.answer("❌ Ushbu qism uchun video yuborib bo'lmadi.")
        user_waiting_part.pop(user_id, None)
        user_current_code.pop(user_id, None)
        kb = main_menu(is_admin=(user_id == ADMIN_ID))
        await message.answer("Yana nima qilamiz?", reply_markup=kb)
        return

    if user_waiting_code.get(user_id):
        print(f"[USER_CODE_ENTER] user_id={user_id} code_entered={text}")
        ok, _ = await is_subscribed_all_diagnostic(user_id)
        if not ok:
            await send_subscription_panel(message)
            return
        code = text
        movie = get_movie(code)
        print(f"[USER_CODE_MOVIE] user_id={user_id} movie_found={bool(movie)}")
        if not movie:
            await message.answer("📥 Bunday kodli kino topilmadi.")
            return
        parts = movie.get("parts", [])
        if parts:
            if len(parts) == 1:
                part = parts[0]
                video_id = part.get("video")
                print(f"[USER_CODE_SINGLE_PART] user_id={user_id} code={code} video_id={video_id}")
                if not video_id:
                    await message.answer("❌ Ushbu qism uchun video topilmadi.")
                    return
                increment_view(code)
                try:
                    await message.answer_video(video=video_id, caption=f"🎬 {part.get('title','')}\n\n📝 {part.get('description','')}")
                except TelegramBadRequest as e:
                    print(f"[USER_CODE_SEND_ERROR] user_id={user_id} code={code} error={e}")
                    await message.answer("❌ Video yuborib bo'lmadi.")
                user_waiting_code.pop(user_id, None)
                kb = main_menu(is_admin=(user_id == ADMIN_ID))
                await message.answer("Yana nima qilamiz?", reply_markup=kb)
                return
            kb = parts_menu(len(parts))
            user_current_code[user_id] = code
            user_waiting_part[user_id] = True
            await message.answer(f"🎬 {movie.get('title', code)} qismlarini tanlang:", reply_markup=kb)
            return
        await message.answer("📥 Bu kodda kontent topilmadi.")
        return

    await message.answer("Iltimos, menyudan biror tugmani tanlang yoki /start ni bosing.")

# --- Webhook lifecycle ---
async def on_startup(app: web.Application):
    init_db()
    migrate_json_to_sqlite()
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        await bot.set_webhook(url=WEBHOOK_URL)
        print(f"✅ Webhook set: {WEBHOOK_URL}")
    else:
        print(f"ℹ️ Webhook already set: {WEBHOOK_URL}")

async def on_shutdown(app: web.Application):
    await bot.session.close()
    print("🛑 Bot session closed")

def main():
    print("🚀 Bot starting...")
    print(f"🌐 Server: {WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
    print(f"🔗 Webhook URL: {WEBHOOK_URL}")
    app = web.Application()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

if __name__ == "__main__":
    main()
