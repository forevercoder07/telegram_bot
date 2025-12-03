import json
import asyncio
import random
import os  # <-- YANGI: Environment Variables o'qish uchun
from typing import Optional
from aiogram import Bot, Dispatcher
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

# Webhook uchun yangi importlar
from aiohttp import web 
from aiogram.dispatcher.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# --- Webhook Konfiguratsiya (Polling o'rniga) ---
# BOT_TOKEN endi ENV variable orqali olinadi, lekin eski usulni ham qoldiramiz.
# Lekin Renderda faqat ENV ishlatish kerak!
# BOT_TOKEN ni global o'zgaruvchilardan olib tashlang va quyidagicha o'zgartiring:

# Bot tokeni Environment Variable orqali olinadi
# Agar ENV sozlanmagan bo'lsa, xato beradi yoki o'rnatilgan tokendan foydalanadi (Test uchun)
BOT_TOKEN_ENV = os.getenv("BOT_TOKEN", "8584498135:AAFTzRZHOnh5ZR_AAyXsSJkX2u8hStXkLmg") 

# Renderdan olinadigan URL va Port
BASE_WEBHOOK_URL = os.getenv("RENDER_URL") # Masalan: https://kino-bot.onrender.com
WEB_SERVER_PORT = int(os.getenv("PORT", 8080)) # Render bergan port

# Telegramga o'rnatiladigan Webhook manzili
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN_ENV}"
WEBHOOK_URL = f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}" if BASE_WEBHOOK_URL else None
WEB_SERVER_HOST = "0.0.0.0" 

# --- KONFIGURATSIYA ---
ADMIN_ID = 1629210003 
MOVIE_FILE = "movies.json"
SETTINGS_FILE = "settings.json"

bot = Bot(token=BOT_TOKEN_ENV)
dp = Dispatcher()

# ... (Qolgan barcha funksiyalar va handlerlar o'zgarishsiz qoladi) ...
# --- Per-user state konteynerlari (oddiy dict bilan) ---
user_waiting_code: dict[int, bool] = {}
user_waiting_part: dict[int, bool] = {}
user_current_code: dict[int, str] = {}
admin_temp_video: dict[int, str] = {}
admin_repair_code: dict[int, str] = {}

# --- Fayl yordamchilari va normalizatsiya ---
def load_movies() -> dict:
    try:
        with open(MOVIE_FILE, "r", encoding="utf-8") as f:
            movies = json.load(f)
    except Exception:
        movies = {}

    changed = False
    for code, info in list(movies.items()):
        if not isinstance(info, dict):
            movies[code] = {"title": str(info), "parts": [], "views": 0}
            changed = True
            continue

        # Legacy -> parts avtomatik migratsiya
        if "parts" not in info and "video" in info:
            movies[code] = {
                "title": info.get("title", code),
                "parts": [{
                    "title": info.get("title", code),
                    "description": info.get("description", ""),
                    "video": info.get("video", "")
                }],
                "views": info.get("views", 0)
            }
            changed = True
            continue

        if "parts" in info:
            info.setdefault("title", info.get("title", code))
            info.setdefault("views", info.get("views", 0))
            new_parts = []
            for part in info.get("parts", []):
                if isinstance(part, dict):
                    part.setdefault("title", info.get("title", code))
                    part.setdefault("description", "")
                    part.setdefault("video", "")
                    new_parts.append(part)
            info["parts"] = new_parts
            changed = True

    if changed:
        try:
            with open(MOVIE_FILE, "w", encoding="utf-8") as f:
                json.dump(movies, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return movies

def save_movies(movies: dict):
    with open(MOVIE_FILE, "w", encoding="utf-8") as f:
        json.dump(movies, f, ensure_ascii=False, indent=2)

def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"channels": []}

def save_settings(settings: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

def get_channels() -> list:
    return load_settings().get("channels", [])

def save_channels_list(channels: list):
    settings = load_settings()
    settings["channels"] = channels
    save_settings(settings)

# --- Subscription check with diagnostics ---
async def is_subscribed_all_diagnostic(user_id: int):
    channels = get_channels()
    if not channels:
        # Agar kanallar ro'yxati bo'lmasa, foydalanish ochiq
        return True, {"not_subscribed": [], "inaccessible": []}

    not_subscribed = []
    inaccessible = []
    for ch in channels:
        name = ch.lstrip("@").strip()
        if not name:
            inaccessible.append((ch, "Empty channel name"))
            continue
        chat_id = f"@{name}"
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                not_subscribed.append(ch)
        except Exception as e:
            inaccessible.append((ch, str(e)))
    ok = (len(not_subscribed) == 0 and len(inaccessible) == 0)
    return ok, {"not_subscribed": not_subscribed, "inaccessible": inaccessible}

async def is_subscribed_all(user_id: int) -> bool:
    ok, _ = await is_subscribed_all_diagnostic(user_id)
    return ok

# --- Reply Keyboardlar ---
MAIN_BUTTONS_USER = [
    [KeyboardButton(text="🎬 Kino topish")],
    [KeyboardButton(text="📊 Statistika")],
    [KeyboardButton(text="📽 Kino tavsiyasi")],
    [KeyboardButton(text="📩 Adminga murojaat")]
]

MAIN_BUTTONS_ADMIN = [
    [KeyboardButton(text="➕ Kino qo‘shish")],
    [KeyboardButton(text="📚 Barcha kinolar")],
    [KeyboardButton(text="⚙️ Kanallarni boshqarish")],
    [KeyboardButton(text="🛠 Repair")],
    [KeyboardButton(text="🔁 Migratsiya")]
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

# --- Channels panel markup (inline for links + check) ---
def channels_panel_markup(channels: list):
    buttons = []
    for idx, ch in enumerate(channels, start=1):
        url = f"https://t.me/{ch.lstrip('@')}"
        buttons.append([InlineKeyboardButton(text=f"{idx}-kanal ↗", url=url)])
    buttons.append([InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def send_subscription_panel(to):
    channels = get_channels()
    if not channels:
        return
    text = (
        "❗ Botdan foydalanish uchun pastdagi kanallarga obuna bo‘ling.\n"
        "Keyin ✅ Tasdiqlash tugmasini bosing."
    )
    kb = channels_panel_markup(channels)
    if isinstance(to, Message):
        await to.answer(text, reply_markup=kb)
    else:
        await to.message.answer(text, reply_markup=kb)

# --- Helper: tugma matnlarini aniqlash ---
def is_button_text(text: str) -> bool:
    base = {
        "🎬 Kino topish", "📊 Statistika", "📽 Kino tavsiyasi", "📩 Adminga murojaat",
        "➕ Kino qo‘shish", "📚 Barcha kinolar", "⚙️ Kanallarni boshqarish",
        "🛠 Repair", "🔁 Migratsiya", "🔙 Asosiy menyu"
    }
    if text in base:
        return True
    if text.endswith("-qism") and text[:-5].isdigit():
        return True
    # raqamli javoblar qism tanlash kontekstida handle_text_flow tomonidan qabul qilinadi,
    # shu sababli bu yerda faqat "-qism" formatini tugma deb hisoblaymiz.
    return False

# --- Start handler ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    user_waiting_code.pop(user_id, None)
    user_waiting_part.pop(user_id, None)
    user_current_code.pop(user_id, None)

    welcome_text = (
        "👋 Assalomu alaykum!\n\n"
        "📽 Bu bot orqali siz kinolarni kod orqali topishingiz, qismlarini ko‘rishingiz, "
        "statistikani ko‘rishingiz va tavsiyalar olishingiz mumkin.\n\n"
        "🎬 Kino olamiga xush kelibsiz!"
    )
    kb = main_menu(is_admin=(user_id == ADMIN_ID))
    ok, info = await is_subscribed_all_diagnostic(user_id)
    if not ok:
        text = "❗ Obuna tekshiruvida muammo:\n"
        if info["not_subscribed"]:
            text += "Obuna bo‘lish kerak bo‘lgan kanallar:\n"
            for i, c in enumerate(info["not_subscribed"], start=1):
                text += f"{i}-kanal: {c}\n"
        if info["inaccessible"]:
            text += "\nKanal bilan muammo (bot kanalga kira olmaydi yoki username noto'g'ri):\n"
            for ch, err in info["inaccessible"]:
                text += f"{ch} — {err}\n"
        await message.answer(text)
        await send_subscription_panel(message)
        return

    await message.answer(welcome_text, reply_markup=kb)

# --- Kino topish bosilganda obuna tekshiruvi va kod kiritish rejimi ---
@dp.message(lambda m: m.text == "🎬 Kino topish")
async def btn_search(message: Message):
    user_id = message.from_user.id
    ok, _ = await is_subscribed_all_diagnostic(user_id)
    if not ok:
        await send_subscription_panel(message)
        return
    user_waiting_code[user_id] = True
    user_waiting_part.pop(user_id, None)
    user_current_code.pop(user_id, None)
    await message.answer("Kino kodini kiriting:")

# --- Statistika ---
@dp.message(lambda m: m.text == "📊 Statistika")
async def btn_stats(message: Message):
    ok, _ = await is_subscribed_all_diagnostic(message.from_user.id)
    if not ok:
        await send_subscription_panel(message)
        return
    movies = load_movies()
    if not movies:
        await message.answer("Hozircha statistikada kino yo‘q.")
        return
    top = sorted(movies.items(), key=lambda x: x[1].get("views", 0), reverse=True)
    lines = ["📊 Eng ko‘p ko‘rilgan kinolar:\n"]
    for i, (code, info) in enumerate(top, start=1):
        lines.append(f"{i}. {info.get('title', code)} (Kod: {code}) — {info.get('views', 0)} marta")
    await message.answer("\n".join(lines))

# --- Tavsiya ---
@dp.message(lambda m: m.text == "📽 Kino tavsiyasi")
async def btn_recommend(message: Message):
    ok, _ = await is_subscribed_all_diagnostic(message.from_user.id)
    if not ok:
        await send_subscription_panel(message)
        return
    movies = load_movies()
    if not movies:
        await message.answer("Hozircha tavsiya uchun kinolar yo‘q.")
        return
    multi = [(code, info) for code, info in movies.items() if info.get("parts")]
    single = [(code, info) for code, info in movies.items() if info.get("parts") == [] and info.get("video")]
    if multi and (not single or random.random() < 0.7):
        code, info = random.choice(multi)
        part = random.choice(info["parts"])
        increment_view(code)
        try:
            await message.answer_video(part.get("video", ""), caption=f"🎬 {part.get('title','')}\n\n📝 {part.get('description','')}\n\n💡 Tavsiya qilindi")
        except TelegramBadRequest as e:
            await message.answer("❌ Tavsiya qilingan qism uchun video yuborib bo‘lmadi — file_id noto‘g‘ri yoki eskirgan.")
            await message.answer(f"Adminga: /repair {code} yozing va yangi videoni yuboring.\nXato: {e}")
    elif single:
        code, info = random.choice(single)
        increment_view(code)
        try:
            await message.answer_video(info.get("video", ""), caption=f"🎬 {info.get('title', code)}\n\n📝 {info.get('description','')}\n\n💡 Tavsiya qilindi")
        except TelegramBadRequest as e:
            await message.answer("❌ Tavsiya qilingan video yuborib bo‘lmadi.")
            await message.answer(f"Adminga: /repair {code} yozing va yangi videoni yuboring.\nXato: {e}")
    else:
        await message.answer("Hali kinolar qo‘shilmagan.")

# --- Adminga murojaat ---
@dp.message(lambda m: m.text == "📩 Adminga murojaat")
async def btn_contact(message: Message):
    await message.answer("Adminga murojaat: https://t.me/mr_forever777")

# --- Asosiy menyuga qaytish ---
@dp.message(lambda m: m.text == "🔙 Asosiy menyu")
async def btn_back_to_main(message: Message):
    user_id = message.from_user.id
    user_waiting_part.pop(user_id, None)
    user_waiting_code.pop(user_id, None)
    user_current_code.pop(user_id, None)
    kb = main_menu(is_admin=(user_id == ADMIN_ID))
    await message.answer("Asosiy menyu.", reply_markup=kb)

# --- Admin: kino qo'shish (video -> Kod|Qism|Sharh) ---
@dp.message(lambda m: m.text == "➕ Kino qo‘shish" and m.from_user.id == ADMIN_ID)
async def btn_add_movie(message: Message):
    await message.answer("Videoni yuboring, keyin matn yuboring: Kod | Qism nomi | Sharh")

@dp.message(lambda m: m.video and m.from_user.id == ADMIN_ID)
async def admin_receive_video(message: Message):
    admin_temp_video[message.from_user.id] = message.video.file_id
    await message.answer("✅ Video qabul qilindi.\nEndi matn yuboring: Kod | Qism nomi | Sharh")

@dp.message(lambda m: m.text and "|" in m.text and m.from_user.id == ADMIN_ID)
async def admin_receive_info(message: Message):
    try:
        code, part_title, desc = map(str.strip, message.text.split("|", maxsplit=2))
        video_id = admin_temp_video.get(message.from_user.id)
        if not video_id:
            await message.answer("❗ Avval video yuboring.")
            return
        movies = load_movies()
        if code not in movies:
            movies[code] = {"title": part_title, "parts": [], "views": 0}
        movies[code].setdefault("parts", [])
        movies[code]["parts"].append({"title": part_title, "description": desc, "video": video_id})
        save_movies(movies)
        admin_temp_video.pop(message.from_user.id, None)
        try:
            await message.answer_video(video=video_id, caption=f"🎬 {part_title}\n\n📝 {desc}")
        except TelegramBadRequest:
            await message.answer("✅ Qism qo‘shildi, lekin preview yuborilmadi (file_id muammosi).")
        await message.answer("✅ Qism qo‘shildi.")
    except Exception:
        await message.answer("❌ Format noto‘g‘ri. To‘g‘ri format: Kod | Qism nomi | Sharh")

# --- Admin: barcha kinolar ro'yxati ---
@dp.message(lambda m: m.text == "📚 Barcha kinolar" and m.from_user.id == ADMIN_ID)
async def btn_list_movies(message: Message):
    movies = load_movies()
    if not movies:
        await message.answer("Hozircha kino yo‘q.")
        return
    lines = ["📚 Barcha kinolar:\n"]
    for code, info in movies.items():
        lines.append(f"🎬 {info.get('title', code)} (Kod: {code})")
    text = "\n".join(lines)
    for i in range(0, len(text), 3500):
        await message.answer(text[i:i+3500])

# --- Admin: Kanallarni boshqarish boshlash ---
@dp.message(lambda m: m.text == "⚙️ Kanallarni boshqarish" and m.from_user.id == ADMIN_ID)
async def edit_channels_start(message: Message):
    current = get_channels()
    existing = "\n".join(f"{i+1}-kanal: {ch}" for i, ch in enumerate(current, start=1)) if current else "— Mavjud emas —"
    await message.answer(
        "Kanallar ro‘yxatini yuboring (har birini alohida qatorda).\nQabul qilinadi: @kanal yoki https://t.me/kanal\n\n"
        f"Hozirgi ro‘yxat:\n{existing}"
    )
    # Belgilaymiz: admin keyingi matnni kanallar ro'yxati sifatida yuboradi
    user_waiting_code[message.from_user.id] = False
    user_waiting_part[message.from_user.id] = False
    user_current_code[message.from_user.id] = "__editing_channels__"

@dp.message(lambda m: m.from_user.id == ADMIN_ID and m.text and m.text.strip() and user_current_code.get(m.from_user.id) == "__editing_channels__")
async def edit_channels_apply(message: Message):
    lines = [ln.strip() for ln in message.text.splitlines() if ln.strip()]
    channels = []
    for ln in lines:
        if ln.startswith("http://") or ln.startswith("https://"):
            ln = ln.rstrip("/").split("/")[-1]
        ln = ln.lstrip("@").strip()
        if ln:
            channels.append("@" + ln)
    save_channels_list(channels)
    user_current_code.pop(message.from_user.id, None)
    kb = channels_panel_markup(channels)
    await message.answer("✅ Kanallar yangilandi. Foydalanuvchilarga ko‘rinishi:", reply_markup=kb)

# --- Check subscription callback from inline panel ---
@dp.callback_query(lambda c: c.data == "check_sub")
async def check_subscription(callback: CallbackQuery):
    ok, info = await is_subscribed_all_diagnostic(callback.from_user.id)
    if ok:
        await callback.message.answer("✅ Obuna tasdiqlandi. /start ni bosing.")
        await cmd_start(callback.message)
    else:
        text = "❌ Hozircha to‘liq obuna aniqlanmadi.\n"
        if info["not_subscribed"]:
            text += "Obuna bo‘lish kerak bo‘lgan kanallar:\n"
            for i, c in enumerate(info["not_subscribed"], start=1):
                text += f"{i}-kanal: {c}\n"
        if info["inaccessible"]:
            text += "\nKanal bilan muammo (bot kanalga kira olmaydi yoki username noto'g'ri):\n"
            for ch, err in info["inaccessible"]:
                text += f"{ch} — {err}\n"
        await callback.message.answer(text)
        await send_subscription_panel(callback)

# --- Admin: repair info and command ---
@dp.message(lambda m: m.text == "🛠 Repair" and m.from_user.id == ADMIN_ID)
async def btn_repair_help(message: Message):
    await message.answer("Foydalanish: /repair <KOD> yoki /repair <KOD> <QISM_RAQAMI>\nBuyruqdan so‘ng yangi videoni yuboring — u belgilangan qismga bog‘lanadi.")

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
    movies = load_movies()
    if code not in movies:
        await message.answer("Bunday kod topilmadi.")
        return
    admin_repair_code[message.from_user.id] = json.dumps({"code": code, "part": qism_index})
    await message.answer(f"✅ Kod {code} uchun video qabul qilish rejimi yoqildi. Yangi videoni yuboring.")

@dp.message(lambda m: m.video and m.from_user.id == ADMIN_ID and m.from_user.id in admin_repair_code)
async def admin_receive_repair_video(message: Message):
    raw = admin_repair_code.pop(message.from_user.id, None)
    if not raw:
        await message.answer("Repair rejimi topilmadi. /repair bilan qayta urinib ko‘ring.")
        return
    data = json.loads(raw)
    code = data.get("code")
    part_idx = data.get("part")
    movies = load_movies()
    if code not in movies:
        await message.answer("Kod topilmadi, bekor qilindi.")
        return
    if part_idx is None:
        if movies[code].get("parts"):
            movies[code]["parts"][-1]["video"] = message.video.file_id
        else:
            movies[code]["video"] = message.video.file_id
    else:
        parts = movies[code].get("parts", [])
        if 0 <= part_idx < len(parts):
            movies[code]["parts"][part_idx]["video"] = message.video.file_id
        else:
            await message.answer("❌ Bunday qism topilmadi. Repair bekor qilindi.")
            return
    save_movies(movies)
    await message.answer("✅ Video yangilandi va saqlandi.")

# --- Migrate buyruq ---
@dp.message(lambda m: m.text == "🔁 Migratsiya" and m.from_user.id == ADMIN_ID)
async def btn_migrate_help(message: Message):
    await cmd_migrate(message)

@dp.message(Command("migrate"))
async def cmd_migrate(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    movies = load_movies()
    changed = False
    for code, info in list(movies.items()):
        if "parts" not in info and info.get("video"):
            movies[code] = {
                "title": info.get("title", code),
                "parts": [{
                    "title": info.get("title", code),
                    "description": info.get("description", ""),
                    "video": info.get("video", "")
                }],
                "views": info.get("views", 0)
            }
            changed = True
    if changed:
        save_movies(movies)
        await message.answer("✅ Migratsiya bajarildi: legacy yozuvlar parts formatiga o‘tkazildi.")
    else:
        await message.answer("ℹ️ Migratsiya kerak emas: legacy yozuv topilmadi.")

# --- Umumiy matn oqimi: kod kiritish va qism tanlash ---
@dp.message(lambda m: m.text and not is_button_text(m.text))
async def handle_text_flow(message: Message):
    text = message.text.strip()
    user_id = message.from_user.id

    # Agar qism tanlash rejimida bo'lsa
    if user_waiting_part.get(user_id):
        if text.endswith("-qism") and text[:-5].isdigit():
            idx = int(text[:-5]) - 1
        elif text.isdigit():
            idx = int(text) - 1
        else:
            await message.answer("Qism raqamini tanlang (masalan: 1-qism) yoki 🔙 Asosiy menyuga qayting.")
            return

        code = user_current_code.get(user_id)
        movies = load_movies()
        if not code or code not in movies:
            await message.answer("❌ Qism tanlash konteksti yo‘qoldi. Iltimos, ‘🎬 Kino topish’dan qayta urinib ko‘ring.")
            user_waiting_part.pop(user_id, None)
            user_current_code.pop(user_id, None)
            return

        parts = movies[code].get("parts", [])
        if idx < 0 or idx >= len(parts):
            await message.answer("❌ Bunday qism mavjud emas. Tugmalardan tanlang.")
            return

        part = parts[idx]
        increment_view(code)
        try:
            await message.answer_video(
                video=part.get("video", ""),
                caption=f"🎬 {part.get('title','')}\n\n📝 {part.get('description','')}"
            )
        except TelegramBadRequest as e:
            await message.answer("❌ Ushbu qism uchun video yuborib bo‘lmadi — file_id noto‘g‘ri yoki eskirgan.")
            await message.answer(f"Adminga: /repair {code} yozing va yangi videoni yuboring.\nXato: {e}")

        user_waiting_part.pop(user_id, None)
        user_current_code.pop(user_id, None)
        kb = main_menu(is_admin=(user_id == ADMIN_ID))
        await message.answer("Yana nima qilamiz?", reply_markup=kb)
        return

    # Agar kod kiritish rejimida bo'lsa
    if user_waiting_code.get(user_id):
        ok, _ = await is_subscribed_all_diagnostic(user_id)
        if not ok:
            await send_subscription_panel(message)
            return

        code = text
        movies = load_movies()
        if code not in movies:
            await message.answer("📥 Bunday kodli kino topilmadi.")
            return

        m = movies[code]
        parts = m.get("parts", [])

        if parts:
            # Agar faqat bitta qism bo'lsa, bevosita shu qismni yuboramiz
            if len(parts) == 1:
                part = parts[0]
                increment_view(code)
                try:
                    await message.answer_video(
                        video=part.get("video", ""),
                        caption=f"🎬 {part.get('title','')}\n\n📝 {part.get('description','')}"
                    )
                except TelegramBadRequest as e:
                    await message.answer("❌ Video yuborib bo‘lmadi — file_id noto‘g‘ri yoki eskirgan.")
                    await message.answer(f"Adminga: /repair {code} yozing va yangi videoni yuboring.\nXato: {e}")
                user_waiting_code.pop(user_id, None)
                kb = main_menu(is_admin=(user_id == ADMIN_ID))
                await message.answer("Yana nima qilamiz?", reply_markup=kb)
                return

            # Agar qism 2 yoki undan ko'p bo'lsa — qismlar menyusini ko'rsatamiz
            kb = parts_menu(len(parts))
            user_current_code[user_id] = code
            user_waiting_part[user_id] = True
            await message.answer(f"🎬 {m.get('title', code)} qismlarini tanlang:", reply_markup=kb)
            return

        # Single video (agar legacy bo'lsa)
        video_id = m.get("video") or (parts[0].get("video") if parts else None)
        if video_id:
            increment_view(code)
            try:
                await message.answer_video(
                    video=video_id,
                    caption=f"🎬 {m.get('title', code)}\n\n📝 {m.get('description','')}"
                )
            except TelegramBadRequest as e:
                await message.answer("❌ Video yuborib bo‘lmadi — file_id noto‘g‘ri yoki eskirgan.")
                await message.answer(f"Adminga: /repair {code} yozing va yangi videoni yuboring.\nXato: {e}")
            user_waiting_code.pop(user_id, None)
            kb = main_menu(is_admin=(user_id == ADMIN_ID))
            await message.answer("Yana nima qilamiz?", reply_markup=kb)
            return

        await message.answer("📥 Bu kodda kontent topilmadi.")
        return

    # Aks holda: foydalanuvchi oddiy matn yozdi
    await message.answer("Iltimos, menyudan biror tugmani tanlang yoki /start ni bosing.")

# --- Views increment (def once) ---
def increment_view(code: str):
    movies = load_movies()
    if code in movies:
        movies[code]["views"] = movies[code].get("views", 0) + 1
        save_movies(movies)

# --- Main ---
# --- 3. WEBHOOK LOGIKASI ---

async def on_startup(dispatcher: Dispatcher, bot: Bot) -> None:
    """Server ishga tushganda Webhook manzilini Telegramga o'rnatadi."""
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        print(f"Webhook muvaffaqiyatli o'rnatildi: {WEBHOOK_URL}")
    else:
        print("WEBHOOK_URL sozlanmagan. Polling rejimida ishlashni afzal biling.")

async def on_shutdown(dispatcher: Dispatcher, bot: Bot) -> None:
    """Server o'chirilganda Webhookni Telegramdan o'chiradi."""
    await bot.delete_webhook()
    print("Webhook o'chirildi.")
    
# Render Health Check (Sog'liqni tekshirish) uchun oddiy GET rute
async def health_check(request: web.Request) -> web.Response:
    """Render so'raganda OK deb javob qaytaradi."""
    return web.Response(text="OK")

# --- 4. ASOSIY ISHGA TUSHIRISH FUNKSIYASI ---

def main():
    if not all([BOT_TOKEN_ENV, BASE_WEBHOOK_URL]):
         # Agar Webhook uchun zaruriy ENV variables sozlanmagan bo'lsa, Pollingni ishlatamiz (Local test uchun)
         # Renderda bu kod faqat Polling rejimida ishlaydi, bu yaxshi emas.
         # Agar Renderda ishlashni istasangiz, pastdagi raise ValueError ni faollashtiring.
         print("❌ Webhook ENV variables (BOT_TOKEN, RENDER_URL) topilmadi. Polling rejimiga qaytildi.")
         asyncio.run(dp.start_polling(bot))
         return

    # DP ga ishga tushirish/o'chirish funksiyalarini ulash
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Aiohttp ilovasini yaratish
    app = web.Application()
    
    # Aiogram Request Handlerni Aiohttpga ulash
    handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        # Agar kerak bo'lsa, webhook_process_kwargs = {'timeout': 55} qo'shish mumkin
    )
    handler.register(app, WEBHOOK_PATH)
    
    # Render Health Check (Sog'liqni tekshirish) uchun "/" rutini qo'shish
    app.router.add_get("/", health_check)

    # Aiohttp serverini ishga tushirish
    web.run_app(
        app,
        host=WEB_SERVER_HOST,
        port=WEB_SERVER_PORT,
    )

if __name__ == "__main__":
    main()
