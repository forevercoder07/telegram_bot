import json
import random
import os
from typing import Optional
from aiohttp import web
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
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# --- Konfiguratsiya ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8584498135:AAFTzRZHOnh5ZR_AAyXsSJkX2u8hStXkLmg")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1629210003"))
MOVIE_FILE = "movies.json"
SETTINGS_FILE = "settings.json"

# Webhook sozlamalari
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "https://your-domain.com")  # O'zingizning domeningiz
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Web server sozlamalari
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("PORT", "8080"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Per-user state konteynerlari ---
user_waiting_code: dict[int, bool] = {}
user_waiting_part: dict[int, bool] = {}
user_current_code: dict[int, str] = {}
admin_temp_video: dict[int, str] = {}
admin_repair_code: dict[int, str] = {}

# --- Fayl yordamchilari ---
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

def get_channels() -> list[str]:
    # Har bir element: "@username" yoki "https://t.me/+invitecode"
    return load_settings().get("channels", [])

def save_channels_list(channels: list[str]):
    settings = load_settings()
    settings["channels"] = channels
    save_settings(settings)

# --- Invite-link aniqlash ---
def is_invite_link(s: str) -> bool:
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return ("t.me/" in s) and ("/+" in s or s.startswith("https://t.me/+") or s.startswith("http://t.me/+"))
    return False

def normalize_channel_input(s: str) -> str:
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        # t.me/username yoki t.me/+invitecode ni saqlab qo'yish
        return s.rstrip("/")
    # @username formatiga keltirish
    return "@" + s.lstrip("@")

# --- Subscription check ---
async def is_subscribed_all_diagnostic(user_id: int):
    channels = get_channels()
    if not channels:
        return True, {"not_subscribed": [], "inaccessible": [], "invite_only": []}

    not_subscribed = []
    inaccessible = []
    invite_only = []

    for ch in channels:
        ch_str = ch.strip()
        # Agar invite-link bo'lsa: tekshiruvdan ozod (pending holatni Telegram API bermaydi)
        if is_invite_link(ch_str):
            invite_only.append(ch_str)
            continue

        # Aks holda @username bo'lishi kerak
        name = ch_str.lstrip("@").strip()
        if not name:
            inaccessible.append((ch_str, "Bo'sh kanal nomi"))
            continue

        chat_id = f"@{name}"
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                not_subscribed.append(ch_str)
        except Exception as e:
            # Agar kanal private bo'lib username yo'q bo'lsa, admin panelda invite-linkdan foydalaning
            inaccessible.append((ch_str, str(e)))

    ok = (len(not_subscribed) == 0 and len(inaccessible) == 0)
    # invite_only kanallar “OK” hisoblanadi (pending bo’lsa ham), shuning uchun ok hisobiga ta’sir qilmaydi
    return ok, {"not_subscribed": not_subscribed, "inaccessible": inaccessible, "invite_only": invite_only}

async def is_subscribed_all(user_id: int) -> bool:
    ok, _ = await is_subscribed_all_diagnostic(user_id)
    return ok

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
            url = ch  # to'g'ridan-to'g'ri invite-link
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

# --- Handlers ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    user_waiting_code.pop(user_id, None)
    user_waiting_part.pop(user_id, None)
    user_current_code.pop(user_id, None)

    welcome_text = (
        "👋 Assalomu alaykum!\n\n"
        "📽 Bu bot orqali siz kinolarni kod orqali topishingiz, qismlarini ko'rishingiz, "
        "statistikani ko'rishingiz va tavsiyalar olishingiz mumkin.\n\n"
        "🎬 Kino olamiga xush kelibsiz!"
    )
    kb = main_menu(is_admin=(user_id == ADMIN_ID))
    ok, info = await is_subscribed_all_diagnostic(user_id)
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
                "\nℹ️ Invite-link kanallar (t.me/+...) qo'shilish so'rovi asosida ishlaydi. "
                "Tasdiqlash tugmasini bosing va kuting, tasdiqlangach a'zo bo'lasiz.\n"
            )
        await message.answer(text)
        await send_subscription_panel(message)
        return

    await message.answer(welcome_text, reply_markup=kb)

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

@dp.message(lambda m: m.text == "📊 Statistika")
async def btn_stats(message: Message):
    ok, _ = await is_subscribed_all_diagnostic(message.from_user.id)
    if not ok:
        await send_subscription_panel(message)
        return
    movies = load_movies()
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
    movies = load_movies()
    if not movies:
        await message.answer("Hozircha tavsiya uchun kinolar yo'q.")
        return
    multi = [(code, info) for code, info in movies.items() if info.get("parts")]
    single = [(code, info) for code, info in movies.items() if info.get("parts") == [] and info.get("video")]
    if multi and (not single or random.random() < 0.7):
        code, info = random.choice(multi)
        part = random.choice(info["parts"])
        video_id = part.get("video")
        if not video_id:
            await message.answer("❌ Tavsiya qilingan qism uchun video topilmadi.")
        else:
            increment_view(code)
            try:
                await message.answer_video(video_id, caption=f"🎬 {part.get('title','')}\n\n📝 {part.get('description','')}\n\n💡 Tavsiya qilindi")
            except TelegramBadRequest as e:
                await message.answer("❌ Tavsiya qilingan qism uchun video yuborib bo'lmadi.")
                await message.answer(f"Adminga: /repair {code} yozing va yangi videoni yuboring.\nXato: {e}")
    elif single:
        code, info = random.choice(single)
        video_id = info.get("video", "")
        if not video_id:
            await message.answer("❌ Tavsiya qilingan video topilmadi.")
        else:
            increment_view(code)
            try:
                await message.answer_video(video_id, caption=f"🎬 {info.get('title', code)}\n\n📝 {info.get('description','')}\n\n💡 Tavsiya qilindi")
            except TelegramBadRequest as e:
                await message.answer("❌ Tavsiya qilingan video yuborib bo'lmadi.")
                await message.answer(f"Adminga: /repair {code} yozing va yangi videoni yuboring.\nXato: {e}")
    else:
        await message.answer("Hali kinolar qo'shilmagan.")

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

@dp.message(lambda m: m.text == "➕ Kino qo'shish" and m.from_user.id == ADMIN_ID)
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
            await message.answer("✅ Qism qo'shildi, lekin preview yuborilmadi (file_id muammosi).")
        await message.answer("✅ Qism qo'shildi.")
    except Exception:
        await message.answer("❌ Format noto'g'ri. To'g'ri format: Kod | Qism nomi | Sharh")

@dp.message(lambda m: m.text == "📚 Barcha kinolar" and m.from_user.id == ADMIN_ID)
async def btn_list_movies(message: Message):
    movies = load_movies()
    if not movies:
        await message.answer("Hozircha kino yo'q.")
        return
    lines = ["📚 Barcha kinolar:\n"]
    for code, info in movies.items():
        lines.append(f"🎬 {info.get('title', code)} (Kod: {code})")
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
    user_waiting_code[message.from_user.id] = False
    user_waiting_part[message.from_user.id] = False
    user_current_code[message.from_user.id] = "__editing_channels__"

@dp.message(lambda m: m.from_user.id == ADMIN_ID and m.text and m.text.strip() and user_current_code.get(m.from_user.id) == "__editing_channels__")
async def edit_channels_apply(message: Message):
    lines = [ln.strip() for ln in message.text.splitlines() if ln.strip()]
    channels = []
    for ln in lines:
        if ln.startswith("http://") or ln.startswith("https://"):
            ln = ln.rstrip("/")
            # t.me/username yoki t.me/+invitecode ni to'g'ridan-to'g'ri saqlaymiz
            # (username bo'lsa keyinchalik tekshiriladi, invite-link bo'lsa tekshiruvdan ozod)
            channels.append(ln)
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
    # Invite-link kanallar tekshiruvdan ozod, shuning uchun ok faqat username kanallarga bog'liq
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
        await message.answer("Repair rejimi topilmadi. /repair bilan qayta urinib ko'ring.")
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
        await message.answer("✅ Migratsiya bajarildi: legacy yozuvlar parts formatiga o'tkazildi.")
    else:
        await message.answer("ℹ️ Migratsiya kerak emas: legacy yozuv topilmadi.")

# --- O'chirish tugmasi va buyrug'i ---
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

    movies = load_movies()
    if code not in movies:
        await message.answer("❌ Bunday kod topilmadi.")
        return

    if qism_index is None:
        movies.pop(code)
        save_movies(movies)
        await message.answer(f"✅ Kod {code} uchun butun kino o‘chirildi.")
    else:
        parts_list = movies[code].get("parts", [])
        if 0 <= qism_index < len(parts_list):
            deleted_part = parts_list.pop(qism_index)
            save_movies(movies)
            await message.answer(f"✅ Kod {code} uchun {qism_index+1}-qism o‘chirildi.\n🎬 {deleted_part.get('title','')}")
        else:
            await message.answer("❌ Bunday qism topilmadi.")

# --- Matn oqimi: tuzatilgan handle_text_flow ---
@dp.message(lambda m: m.text and not is_button_text(m.text))
async def handle_text_flow(message: Message):
    text = message.text.strip()
    user_id = message.from_user.id

    # Qism tanlash rejimi
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
            await message.answer("❌ Qism tanlash konteksti yo'qoldi. Iltimos, '🎬 Kino topish'dan qayta urinib ko'ring.")
            user_waiting_part.pop(user_id, None)
            user_current_code.pop(user_id, None)
            return

        parts = movies[code].get("parts", [])
        if idx < 0 or idx >= len(parts):
            await message.answer("❌ Bunday qism mavjud emas. Tugmalardan tanlang.")
            return

        part = parts[idx]
        video_id = part.get("video")
        if not video_id:
            await message.answer("❌ Ushbu qism uchun video topilmadi.")
            return

        increment_view(code)
        try:
            await message.answer_video(
                video=video_id,
                caption=f"🎬 {part.get('title','')}\n\n📝 {part.get('description','')}"
            )
        except TelegramBadRequest as e:
            await message.answer("❌ Ushbu qism uchun video yuborib bo'lmadi.")
            await message.answer(f"Adminga: /repair {code} {idx+1} yozing va yangi videoni yuboring.\nXato: {e}")

        user_waiting_part.pop(user_id, None)
        user_current_code.pop(user_id, None)
        kb = main_menu(is_admin=(user_id == ADMIN_ID))
        await message.answer("Yana nima qilamiz?", reply_markup=kb)
        return

    # Kod kiritish rejimi
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
            if len(parts) == 1:
                part = parts[0]
                video_id = part.get("video")
                if not video_id:
                    await message.answer("❌ Ushbu qism uchun video topilmadi.")
                    return
                increment_view(code)
                try:
                    await message.answer_video(
                        video=video_id,
                        caption=f"🎬 {part.get('title','')}\n\n📝 {part.get('description','')}"
                    )
                except TelegramBadRequest as e:
                    await message.answer("❌ Video yuborib bo'lmadi.")
                    await message.answer(f"Adminga: /repair {code} yozing va yangi videoni yuboring.\nXato: {e}")
                user_waiting_code.pop(user_id, None)
                kb = main_menu(is_admin=(user_id == ADMIN_ID))
                await message.answer("Yana nima qilamiz?", reply_markup=kb)
                return

            kb = parts_menu(len(parts))
            user_current_code[user_id] = code
            user_waiting_part[user_id] = True
            await message.answer(f"🎬 {m.get('title', code)} qismlarini tanlang:", reply_markup=kb)
            return

        video_id = m.get("video") or (parts[0].get("video") if parts else None)
        if video_id:
            increment_view(code)
            try:
                await message.answer_video(
                    video=video_id,
                    caption=f"🎬 {m.get('title', code)}\n\n📝 {m.get('description','')}"
                )
            except TelegramBadRequest as e:
                await message.answer("❌ Video yuborib bo'lmadi.")
                await message.answer(f"Adminga: /repair {code} yozing va yangi videoni yuboring.\nXato: {e}")
            user_waiting_code.pop(user_id, None)
            kb = main_menu(is_admin=(user_id == ADMIN_ID))
            await message.answer("Yana nima qilamiz?", reply_markup=kb)
            return

        await message.answer("📥 Bu kodda kontent topilmadi.")
        return

    await message.answer("Iltimos, menyudan biror tugmani tanlang yoki /start ni bosing.")

def increment_view(code: str):
    movies = load_movies()
    if code in movies:
        movies[code]["views"] = movies[code].get("views", 0) + 1
        save_movies(movies)

# --- Webhook lifecycle ---
async def on_startup(app: web.Application):
    """Webhook o'rnatish"""
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        await bot.set_webhook(url=WEBHOOK_URL)
        print(f"✅ Webhook o'rnatildi: {WEBHOOK_URL}")
    else:
        print(f"ℹ️ Webhook allaqachon o'rnatilgan: {WEBHOOK_URL}")

async def on_shutdown(app: web.Application):
    """Shutdown"""
    await bot.session.close()
    print("🛑 Bot sessiyasi yopildi")

# --- Main function ---
def main():
    # aiohttp web application yaratish
    app = web.Application()
    
    # Webhook handler setup
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_handler.register(app, path=WEBHOOK_PATH)
    
    # Startup va shutdown eventlar
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    # Setup application
    setup_application(app, dp, bot=bot)
    
    # Web server ishga tushirish
    print(f"🚀 Bot ishga tushmoqda...")
    print(f"🌐 Server: {WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
    print(f"🔗 Webhook URL: {WEBHOOK_URL}")
    
    web.run_app(
        app,
        host=WEB_SERVER_HOST,
        port=WEB_SERVER_PORT
    )

if __name__ == "__main__":
    main()
