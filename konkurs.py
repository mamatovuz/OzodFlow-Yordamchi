import asyncio
import html
import json
import os
import random
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv

load_dotenv()

KONKURS_TOKEN = ("KONKURS_TOKEN", "8672277904:AAG1tBnP4D8c3D3wxika9bYUjo2f5kwGCQM").strip()
ADMIN_IDS = {
    int(x.strip())
    for x in ("KONKURS_ADMIN_IDS", "7903688837").replace(" ", "").split(",")
    if x.strip().isdigit()
}

DATA_DIR = Path("data/konkurs")
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_F = DATA_DIR / "settings.json"
USERS_F = DATA_DIR / "users.json"


def load_json(path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def settings():
    data = load_json(SETTINGS_F)
    defaults = {
        "channel": "",
        "channel_url": "",
        "prize_text": "Sovg'a hali sozlanmagan.",
        "prize_photo": "",
        "end_time": "",
        "winner_id": "",
        "winner_time": "",
        "broadcast_count": 0,
    }
    changed = False
    for key, value in defaults.items():
        if key not in data:
            data[key] = value
            changed = True
    if changed:
        save_json(SETTINGS_F, data)
    return data


def users():
    return load_json(USERS_F)


def save_users(data):
    save_json(USERS_F, data)


def is_admin(user_id):
    return user_id in ADMIN_IDS


def user_title(item):
    name = item.get("full_name") or "User"
    username = item.get("username")
    return f"@{username}" if username else name


def parse_end_time(text):
    text = text.strip()
    formats = [
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError("Format: 2026-05-28 20:00")


def join_url():
    s = settings()
    if s.get("channel_url"):
        return s["channel_url"]
    channel = s.get("channel", "")
    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"
    return "https://t.me/"


def menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Mening havolam", callback_data="my_link"),
            InlineKeyboardButton(text="Referallarim", callback_data="my_refs"),
        ],
        [
            InlineKeyboardButton(text="Sovg'a", callback_data="prize"),
            InlineKeyboardButton(text="Reyting", callback_data="rating"),
        ],
        [InlineKeyboardButton(text="Qoidalar", callback_data="rules")],
    ])


def sub_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Kanalga obuna bo'lish", url=join_url())],
        [InlineKeyboardButton(text="Tekshirish", callback_data="check_sub")],
    ])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Majburiy kanal", callback_data="a_channel")],
        [InlineKeyboardButton(text="Sovg'a sozlash", callback_data="a_prize")],
        [InlineKeyboardButton(text="Sovg'a rasmi", callback_data="a_photo")],
        [InlineKeyboardButton(text="Konkurs vaqti", callback_data="a_time")],
        [InlineKeyboardButton(text="G'olibni tanlash", callback_data="a_winner")],
        [InlineKeyboardButton(text="Qo'lda g'olib", callback_data="a_manual_winner")],
        [InlineKeyboardButton(text="Statistika", callback_data="a_stats")],
        [InlineKeyboardButton(text="Reklama yuborish", callback_data="a_broadcast")],
        [InlineKeyboardButton(text="User bloklash", callback_data="a_block")],
        [InlineKeyboardButton(text="Konkursni reset qilish", callback_data="a_reset")],
    ])


class AdminSt(StatesGroup):
    channel = State()
    prize = State()
    photo = State()
    time = State()
    broadcast = State()
    block = State()
    manual_winner = State()


async def is_subscribed(bot, user_id):
    channel = settings().get("channel")
    if not channel:
        return True
    try:
        member = await bot.get_chat_member(channel, user_id)
        return member.status in {"member", "administrator", "creator"}
    except Exception:
        return False


async def register_user(message, inviter_id=None):
    data = users()
    uid = str(message.from_user.id)
    is_new = uid not in data
    data.setdefault(uid, {
        "user_id": message.from_user.id,
        "username": message.from_user.username or "",
        "full_name": message.from_user.full_name or "",
        "joined": datetime.now().isoformat(),
        "subscribed": False,
        "blocked": False,
        "inviter_id": "",
        "referrals": [],
    })
    data[uid]["username"] = message.from_user.username or ""
    data[uid]["full_name"] = message.from_user.full_name or ""
    if is_new and inviter_id and str(inviter_id) != uid:
        data[uid]["inviter_id"] = str(inviter_id)
    save_users(data)


async def confirm_subscription(bot, user):
    data = users()
    uid = str(user.id)
    item = data.get(uid)
    if not item:
        return False, ""
    if item.get("blocked"):
        return False, "Siz konkursdan bloklangansiz."
    ok = await is_subscribed(bot, user.id)
    if not ok:
        return False, "Siz hali kanalga obuna bo'lmagansiz."

    first_sub = not item.get("subscribed")
    item["subscribed"] = True
    inviter_id = item.get("inviter_id")
    inviter_name = ""
    if first_sub and inviter_id and inviter_id in data:
        inviter = data[inviter_id]
        refs = inviter.setdefault("referrals", [])
        if uid not in refs:
            refs.append(uid)
        inviter_name = user_title(inviter)
    save_users(data)
    return True, inviter_name


def top_users(limit=10):
    items = [u for u in users().values() if u.get("subscribed") and not u.get("blocked")]
    items.sort(key=lambda x: len(x.get("referrals", [])), reverse=True)
    return items[:limit]


async def draw_winner(bot, manual=False):
    s = settings()
    if s.get("winner_id") and not manual:
        return None
    data = users()
    pool = []
    for uid, item in data.items():
        if item.get("blocked") or not item.get("subscribed"):
            continue
        if not await is_subscribed(bot, int(uid)):
            item["subscribed"] = False
            continue
        chance = len(item.get("referrals", [])) + 1
        pool.extend([uid] * chance)
    save_users(data)
    if not pool:
        return None
    winner_id = random.choice(pool)
    s["winner_id"] = winner_id
    s["winner_time"] = datetime.now().isoformat()
    save_json(SETTINGS_F, s)
    winner = data[winner_id]
    text = (
        "G'olib aniqlandi!\n\n"
        f"G'olib: {html.escape(user_title(winner))}\n"
        f"Referallar: {len(winner.get('referrals', []))} ta"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass
    return winner


def find_user_by_query(query):
    query = query.strip()
    if query.startswith("@"):
        query = query[1:]
    data = users()
    if query in data:
        return query, data[query]
    for uid, item in data.items():
        if str(item.get("user_id")) == query:
            return uid, item
        if (item.get("username") or "").lower() == query.lower():
            return uid, item
        phone = str(item.get("phone") or "")
        if phone and phone.replace("+", "") == query.replace("+", ""):
            return uid, item
    return None, None


async def set_manual_winner(bot, query):
    uid, item = find_user_by_query(query)
    if not item:
        return None
    s = settings()
    s["winner_id"] = uid
    s["winner_time"] = datetime.now().isoformat()
    save_json(SETTINGS_F, s)
    text = (
        "G'olib qo'lda belgilandi.\n\n"
        f"G'olib: {html.escape(user_title(item))}\n"
        f"User ID: <code>{uid}</code>\n"
        f"Referallar: {len(item.get('referrals', []))} ta"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass
    return item


async def scheduler(bot):
    while True:
        await asyncio.sleep(60)
        s = settings()
        if s.get("winner_id") or not s.get("end_time"):
            continue
        try:
            end = datetime.fromisoformat(s["end_time"])
        except Exception:
            continue
        if datetime.now() >= end:
            await draw_winner(bot)


async def start_konkurs_bot():
    if not KONKURS_TOKEN:
        print("Konkurs bot token yo'q: KONKURS_TOKEN .env ga qo'yilmagan.")
        return None

    bot = Bot(KONKURS_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    @dp.message(Command("start"))
    async def start(message: types.Message):
        arg = ""
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            arg = parts[1].strip()
        await register_user(message, inviter_id=arg if arg.isdigit() else None)
        await message.answer(
            "Xush kelibsiz!\nKonkursda qatnashing va sovg'a yutib oling.",
            reply_markup=sub_kb(),
        )

    @dp.message(Command("admin"))
    async def admin(message: types.Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        await state.clear()
        await message.answer("Admin panel", reply_markup=admin_kb())

    @dp.callback_query(F.data == "check_sub")
    async def check_sub(call: types.CallbackQuery):
        ok, info = await confirm_subscription(bot, call.from_user)
        if not ok:
            await call.answer(info, show_alert=True)
            return
        if info:
            await call.message.edit_text(
                f"Siz konkursga {html.escape(info)} havolasi orqali qo'shildingiz.\n\n"
                "Endi imkoniyatingizni oshirish uchun do'stlaringizni taklif qiling.",
                reply_markup=menu_kb(),
            )
        else:
            await call.message.edit_text(
                "Siz konkursga muvaffaqiyatli qo'shildingiz.\n\n"
                "Endi imkoniyatingizni oshirish uchun do'stlaringizni taklif qiling.",
                reply_markup=menu_kb(),
            )

    @dp.callback_query(F.data == "my_link")
    async def my_link(call: types.CallbackQuery):
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start={call.from_user.id}"
        share = f"https://t.me/share/url?url={link}&text=Konkursda qatnashing"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Do'stlarga yuborish", url=share)],
            [InlineKeyboardButton(text="Orqaga", callback_data="back_menu")],
        ])
        await call.message.edit_text(
            f"Sizning maxsus havolangiz:\n\n{link}\n\nHar bir qo'shilgan odam sizga +1 imkoniyat beradi.",
            reply_markup=kb,
        )

    @dp.callback_query(F.data == "my_refs")
    async def my_refs(call: types.CallbackQuery):
        item = users().get(str(call.from_user.id), {})
        count = len(item.get("referrals", []))
        text = f"Siz taklif qilgan odamlar: {count} ta" if count else "Siz hali hech kimni taklif qilmagansiz."
        await call.message.edit_text(text, reply_markup=menu_kb())

    @dp.callback_query(F.data == "rating")
    async def rating(call: types.CallbackQuery):
        items = top_users()
        text = "TOP 10\n\n"
        if not items:
            text += "Hali reyting yo'q."
        for index, item in enumerate(items, 1):
            text += f"{index}. {html.escape(user_title(item))} - {len(item.get('referrals', []))}\n"
        await call.message.edit_text(text, reply_markup=menu_kb())

    @dp.callback_query(F.data == "prize")
    async def prize(call: types.CallbackQuery):
        s = settings()
        end = s.get("end_time") or "Belgilanmagan"
        text = f"Joriy sovg'a:\n{s.get('prize_text')}\n\nKonkurs tugash vaqti:\n{end}"
        if s.get("prize_photo"):
            await call.message.answer_photo(s["prize_photo"], caption=text)
            await call.answer()
        else:
            await call.message.edit_text(text, reply_markup=menu_kb())

    @dp.callback_query(F.data == "rules")
    async def rules(call: types.CallbackQuery):
        await call.message.edit_text(
            "Qoidalar:\n\n"
            "1. Kanalga obuna bo'ling.\n"
            "2. Maxsus havolangizni do'stlarga yuboring.\n"
            "3. Har bir tasdiqlangan referal +1 imkoniyat beradi.\n"
            "4. Konkurs oxirida kanalda bo'lmaganlar chiqariladi.",
            reply_markup=menu_kb(),
        )

    @dp.callback_query(F.data == "back_menu")
    async def back_menu(call: types.CallbackQuery):
        await call.message.edit_text("Menu", reply_markup=menu_kb())

    @dp.callback_query(F.data.startswith("a_"))
    async def admin_callbacks(call: types.CallbackQuery, state: FSMContext):
        if not is_admin(call.from_user.id):
            return
        action = call.data
        if action == "a_channel":
            await state.set_state(AdminSt.channel)
            await call.message.edit_text("Kanal username/id yuboring. Masalan: @kanal yoki -100...")
        elif action == "a_prize":
            await state.set_state(AdminSt.prize)
            await call.message.edit_text("Sovg'a matnini yuboring.")
        elif action == "a_photo":
            await state.set_state(AdminSt.photo)
            await call.message.edit_text("Sovg'a rasmini yuboring.")
        elif action == "a_time":
            await state.set_state(AdminSt.time)
            await call.message.edit_text("Tugash vaqtini yuboring.\nFormat: 2026-05-28 20:00")
        elif action == "a_winner":
            winner = await draw_winner(bot, manual=True)
            await call.message.edit_text("G'olib tanlandi." if winner else "G'olib tanlash uchun qatnashchi yo'q.", reply_markup=admin_kb())
        elif action == "a_manual_winner":
            await state.set_state(AdminSt.manual_winner)
            await call.message.edit_text("G'olib qilish uchun user ID yoki @username yuboring.")
        elif action == "a_stats":
            data = users()
            total = len(data)
            subscribed = sum(1 for u in data.values() if u.get("subscribed"))
            refs = sum(len(u.get("referrals", [])) for u in data.values())
            await call.message.edit_text(
                f"Statistika\n\nJami users: {total}\nObuna bo'lganlar: {subscribed}\nReferallar: {refs}",
                reply_markup=admin_kb(),
            )
        elif action == "a_broadcast":
            await state.set_state(AdminSt.broadcast)
            await call.message.edit_text("Reklama matnini yuboring.")
        elif action == "a_block":
            await state.set_state(AdminSt.block)
            await call.message.edit_text("Bloklash uchun user ID yuboring.")
        elif action == "a_reset":
            save_users({})
            s = settings()
            s["winner_id"] = ""
            s["winner_time"] = ""
            save_json(SETTINGS_F, s)
            await call.message.edit_text("Konkurs reset qilindi.", reply_markup=admin_kb())

    @dp.message(AdminSt.channel)
    async def set_channel(message: types.Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        s = settings()
        raw = message.text.strip()
        if "|" in raw:
            channel, url = raw.split("|", 1)
            s["channel"] = channel.strip()
            s["channel_url"] = url.strip()
        else:
            s["channel"] = raw
            s["channel_url"] = f"https://t.me/{raw[1:]}" if raw.startswith("@") else ""
        save_json(SETTINGS_F, s)
        await state.clear()
        await message.answer("Kanal saqlandi.", reply_markup=admin_kb())

    @dp.message(AdminSt.prize)
    async def set_prize(message: types.Message, state: FSMContext):
        s = settings()
        s["prize_text"] = message.text
        save_json(SETTINGS_F, s)
        await state.clear()
        await message.answer("Sovg'a saqlandi.", reply_markup=admin_kb())

    @dp.message(AdminSt.photo, F.photo)
    async def set_photo(message: types.Message, state: FSMContext):
        s = settings()
        s["prize_photo"] = message.photo[-1].file_id
        save_json(SETTINGS_F, s)
        await state.clear()
        await message.answer("Sovg'a rasmi saqlandi.", reply_markup=admin_kb())

    @dp.message(AdminSt.time)
    async def set_time(message: types.Message, state: FSMContext):
        try:
            end = parse_end_time(message.text)
        except Exception as exc:
            await message.answer(str(exc))
            return
        s = settings()
        s["end_time"] = end.isoformat()
        s["winner_id"] = ""
        s["winner_time"] = ""
        save_json(SETTINGS_F, s)
        await state.clear()
        await message.answer("Konkurs vaqti saqlandi.", reply_markup=admin_kb())

    @dp.message(AdminSt.broadcast)
    async def broadcast(message: types.Message, state: FSMContext):
        data = users()
        ok = fail = 0
        for item in data.values():
            if item.get("blocked"):
                continue
            try:
                await bot.send_message(item["user_id"], message.text)
                ok += 1
            except Exception:
                fail += 1
            await asyncio.sleep(0.05)
        await state.clear()
        await message.answer(f"Yuborildi: {ok}\nXato: {fail}", reply_markup=admin_kb())

    @dp.message(AdminSt.block)
    async def block_user(message: types.Message, state: FSMContext):
        data = users()
        uid = message.text.strip()
        if uid in data:
            data[uid]["blocked"] = True
            save_users(data)
            await message.answer("User bloklandi.", reply_markup=admin_kb())
        else:
            await message.answer("User topilmadi.", reply_markup=admin_kb())
        await state.clear()

    @dp.message(AdminSt.manual_winner)
    async def manual_winner(message: types.Message, state: FSMContext):
        winner = await set_manual_winner(bot, message.text)
        await state.clear()
        if winner:
            await message.answer(
                f"G'olib belgilandi: {html.escape(user_title(winner))}",
                reply_markup=admin_kb(),
            )
        else:
            await message.answer("User topilmadi. ID yoki @username tekshiring.", reply_markup=admin_kb())

    asyncio.create_task(scheduler(bot))
    print("Konkurs bot ishga tushdi.")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
