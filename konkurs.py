import asyncio
import html
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from dotenv import load_dotenv

load_dotenv()

KONKURS_TOKEN = os.getenv("KONKURS_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("KONKURS_ADMIN_IDS", "").replace(" ", "").split(",")
    if x.strip().isdigit()
}

DATA_DIR = Path("data/konkurs")
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_F = DATA_DIR / "settings.json"
USERS_F = DATA_DIR / "users.json"
LOCK_F = DATA_DIR / "konkurs.lock"


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
        "prize_title": "",
        "prize_text": "Sovg'a hali sozlanmagan.",
        "prize_photo": "",
        "end_time": "",
        "winner_id": "",
        "winner_time": "",
        "broadcast_count": 0,
        "status": "active",
        "winner_history": [],
        "reminders_sent": [],
        "daily_top_date": "",
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


def pid_is_running(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def user_title(item):
    name = item.get("full_name") or "User"
    username = item.get("username")
    return f"@{username}" if username else name


def channel_target():
    channel = settings().get("channel", "")
    return channel if channel else None


async def announce_to_channel(bot, text, photo=None):
    target = channel_target()
    if not target:
        return False
    try:
        if photo:
            await bot.send_photo(target, photo, caption=text)
        else:
            await bot.send_message(target, text)
        return True
    except Exception as exc:
        print(f"Kanalga e'lon xatosi: {exc}")
        return False


def contest_post_text():
    s = settings()
    end = s.get("end_time") or "Belgilanmagan"
    prize_title = s.get("prize_title") or "Sovg'a"
    prize_desc = s.get("prize_text") or ""
    return (
        "🎁 Konkurs!\n\n"
        f"Sovg'a: {prize_title}\n"
        f"{prize_desc}\n\n"
        f"Tugash vaqti: {end}\n\n"
        "Qatnashish uchun botga kiring, kanalga obuna bo'ling va do'stlaringizni taklif qiling."
    )


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


def menu_kb(admin=False):
    keyboard = [
        [KeyboardButton(text="🔗 Mening havolam"), KeyboardButton(text="👥 Referallarim")],
        [KeyboardButton(text="🎁 Sovg'a"), KeyboardButton(text="🏆 Reyting")],
        [KeyboardButton(text="ℹ️ Qoidalar")],
    ]
    if admin:
        keyboard.append([KeyboardButton(text="🛠 Admin")])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Konkurs menyusi",
    )


def menu_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔗 Mening havolam", callback_data="my_link"),
            InlineKeyboardButton(text="👥 Referallarim", callback_data="my_refs"),
        ],
        [
            InlineKeyboardButton(text="🎁 Sovg'a", callback_data="prize"),
            InlineKeyboardButton(text="🏆 Reyting", callback_data="rating"),
        ],
        [InlineKeyboardButton(text="ℹ️ Qoidalar", callback_data="rules")],
    ])


def sub_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Kanalga obuna bo'lish", url=join_url())],
        [InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")],
    ])


def admin_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📢 Majburiy kanal"), KeyboardButton(text="🎁 Sovg'a joylash")],
            [KeyboardButton(text="⏸ Status/Pauza"), KeyboardButton(text="✍️ Qo'lda g'olib")],
            [KeyboardButton(text="🔎 User qidirish"), KeyboardButton(text="📝 Post generator")],
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="📤 Reklama")],
            [KeyboardButton(text="🚫 User bloklash"), KeyboardButton(text="🛠 Admin")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Admin panel",
    )


def status_kb():
    is_active = settings().get("status") == "active"
    text = "⏸ Pause" if is_active else "▶️ Active"
    action = "status_pause" if is_active else "status_active"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=action)],
    ])


def status_text():
    is_active = settings().get("status") == "active"
    label = "🟢 Online" if is_active else "🔴 Offline"
    hint = "Hozir konkurs ishlayapti." if is_active else "Hozir konkurs pauzada."
    return f"⏸ Status/Pauza\n\nHolati: <b>{label}</b>\n{hint}"


class AdminSt(StatesGroup):
    channel = State()
    prize_title = State()
    prize_desc = State()
    photo = State()
    time = State()
    broadcast = State()
    block = State()
    manual_winner = State()
    find_user = State()
    ref_adjust = State()


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
    data[uid].setdefault("manual_refs", 0)
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
            try:
                await bot.send_message(
                    int(inviter_id),
                    f"Yangi referal qo'shildi.\nJami: {len(refs) + int(inviter.get('manual_refs', 0) or 0)} ta",
                )
            except Exception:
                pass
        inviter_name = user_title(inviter)
    save_users(data)
    return True, inviter_name


def top_users(limit=10):
    items = [u for u in users().values() if u.get("subscribed") and not u.get("blocked")]
    items.sort(key=lambda x: len(x.get("referrals", [])) + int(x.get("manual_refs", 0) or 0), reverse=True)
    return items[:limit]


def ref_count(item):
    return len(item.get("referrals", [])) + int(item.get("manual_refs", 0) or 0)


async def draw_winner(bot, manual=False):
    s = settings()
    if s.get("status") != "active" and not manual:
        return None
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
        chance = ref_count(item) + 1
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
        f"Referallar: {ref_count(winner)} ta"
    )
    history = s.setdefault("winner_history", [])
    history.append({
        "user_id": winner_id,
        "username": winner.get("username", ""),
        "full_name": winner.get("full_name", ""),
        "refs": ref_count(winner),
        "time": s["winner_time"],
    })
    save_json(SETTINGS_F, s)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass
    await announce_to_channel(bot, text, s.get("prize_photo"))
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
        f"Referallar: {ref_count(item)} ta"
    )
    history = s.setdefault("winner_history", [])
    history.append({
        "user_id": uid,
        "username": item.get("username", ""),
        "full_name": item.get("full_name", ""),
        "refs": ref_count(item),
        "time": s["winner_time"],
        "manual": True,
    })
    save_json(SETTINGS_F, s)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass
    await announce_to_channel(bot, text, s.get("prize_photo"))
    return item


async def refresh_subscriptions(bot):
    data = users()
    removed = 0
    for uid, item in data.items():
        if item.get("subscribed") and not await is_subscribed(bot, int(uid)):
            item["subscribed"] = False
            removed += 1
        await asyncio.sleep(0.03)
    save_users(data)
    return removed


def user_info_text(uid, item):
    inviter_id = item.get("inviter_id") or "-"
    inviter = users().get(str(inviter_id), {})
    return (
        "User ma'lumoti\n\n"
        f"Ism: {html.escape(item.get('full_name', '-'))}\n"
        f"Username: @{item.get('username') or '-'}\n"
        f"ID: <code>{uid}</code>\n"
        f"Obuna: {'ha' if item.get('subscribed') else 'yoq'}\n"
        f"Blok: {'ha' if item.get('blocked') else 'yoq'}\n"
        f"Referal: {ref_count(item)}\n"
        f"Inviter: {html.escape(user_title(inviter)) if inviter else inviter_id}"
    )


def adjust_refs(query):
    parts = query.split()
    if len(parts) < 2:
        return None, "Format: user_id +5 yoki @username -2"
    uid, item = find_user_by_query(parts[0])
    if not item:
        return None, "User topilmadi."
    try:
        delta = int(parts[1])
    except ValueError:
        return None, "Referal soni noto'g'ri. Masalan: +5 yoki -2"
    current = int(item.get("manual_refs", 0) or 0)
    item["manual_refs"] = max(0, current + delta)
    data = users()
    data[uid] = item
    save_users(data)
    return item, f"Referal yangilandi: {ref_count(item)} ta"


async def scheduler(bot):
    while True:
        await asyncio.sleep(60)
        s = settings()
        if s.get("status") != "active":
            continue
        today = datetime.now().date().isoformat()
        if s.get("daily_top_date") != today:
            items = top_users(5)
            if items:
                text = "Bugungi TOP referallar\n\n"
                for index, item in enumerate(items, 1):
                    text += f"{index}. {user_title(item)} - {ref_count(item)}\n"
                await announce_to_channel(bot, text)
            s["daily_top_date"] = today
            save_json(SETTINGS_F, s)
        if s.get("winner_id") or not s.get("end_time"):
            continue
        try:
            end = datetime.fromisoformat(s["end_time"])
        except Exception:
            continue
        remaining = end - datetime.now()
        reminders = s.setdefault("reminders_sent", [])
        for label, delta in (("24h", timedelta(hours=24)), ("3h", timedelta(hours=3)), ("1h", timedelta(hours=1))):
            if label not in reminders and timedelta(0) < remaining <= delta:
                await announce_to_channel(bot, f"Konkurs tugashiga {label} qoldi.\n\n{contest_post_text()}", s.get("prize_photo"))
                reminders.append(label)
                save_json(SETTINGS_F, s)
        if datetime.now() >= end:
            await draw_winner(bot)


async def start_konkurs_bot():
    if not KONKURS_TOKEN:
        print("Konkurs bot token yo'q: KONKURS_TOKEN .env ga qo'yilmagan.")
        return None
    if LOCK_F.exists():
        try:
            old_pid = int(LOCK_F.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            old_pid = 0
        if old_pid and old_pid != os.getpid() and pid_is_running(old_pid):
            print("Konkurs bot allaqachon ishga tushgan bo'lishi mumkin. LOCK topildi.")
            return None
    LOCK_F.write_text(str(os.getpid()), encoding="utf-8")

    bot = Bot(KONKURS_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    await bot.delete_webhook(drop_pending_updates=True)

    @dp.message(Command("start"))
    async def start(message: types.Message):
        if settings().get("status") == "paused":
            await message.answer("Konkurs vaqtincha pauzada.")
            return
        arg = ""
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            arg = parts[1].strip()
        await register_user(message, inviter_id=arg if arg.isdigit() else None)
        if await is_subscribed(bot, message.from_user.id):
            ok, info = await confirm_subscription(bot, message.from_user)
            if not ok:
                await message.answer(info, reply_markup=sub_kb())
                return
            joined_text = (
                f"✅ Siz konkursga {html.escape(info)} havolasi orqali qo'shildingiz."
                if info else
                "✅ Siz allaqachon obuna bo'lgansiz. Konkurs menyusi tayyor."
            )
            await message.answer(
                f"{joined_text}\n\n🎯 Do'stlaringizni taklif qiling va imkoniyatingizni oshiring.",
                reply_markup=menu_kb(is_admin(message.from_user.id)),
            )
            return
        await message.answer(
            "🎁 Xush kelibsiz!\n\nKonkursda qatnashish uchun avval kanalga obuna bo'ling.",
            reply_markup=sub_kb(),
        )

    @dp.message(Command("admin"))
    async def admin(message: types.Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        await state.clear()
        await message.answer("🛠 Admin panel\n\nKerakli bo'limni tanlang.", reply_markup=admin_kb())

    async def show_my_link(message_or_call):
        user_id = message_or_call.from_user.id
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start={user_id}"
        share = f"https://t.me/share/url?url={link}&text=Konkursda qatnashing 🎁"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📨 Do'stlarga yuborish", url=share)],
        ])
        text = f"🔗 Sizning maxsus havolangiz:\n\n{link}\n\n📈 Har bir qo'shilgan odam sizga +1 imkoniyat beradi."
        if isinstance(message_or_call, types.CallbackQuery):
            await message_or_call.message.answer(text, reply_markup=kb)
        else:
            await message_or_call.answer(text, reply_markup=kb)

    async def show_refs(message_or_call):
        item = users().get(str(message_or_call.from_user.id), {})
        count = ref_count(item)
        text = f"👥 Siz taklif qilgan odamlar: {count} ta" if count else "👥 Siz hali hech kimni taklif qilmagansiz."
        if isinstance(message_or_call, types.CallbackQuery):
            await message_or_call.message.answer(text)
        else:
            await message_or_call.answer(text)

    async def show_rating(message_or_call):
        items = top_users()
        text = "🏆 TOP 10\n\n"
        if not items:
            text += "Hali reyting yo'q."
        for index, item in enumerate(items, 1):
            text += f"{index}. {html.escape(user_title(item))} - {ref_count(item)}\n"
        if isinstance(message_or_call, types.CallbackQuery):
            await message_or_call.message.answer(text)
        else:
            await message_or_call.answer(text)

    async def show_prize(message_or_call):
        s = settings()
        end = s.get("end_time") or "Belgilanmagan"
        title = s.get("prize_title") or "Sovg'a"
        desc = s.get("prize_text") or ""
        text = f"🎁 Joriy sovg'a:\n<b>{html.escape(title)}</b>\n\n{html.escape(desc)}\n\n📅 Konkurs tugash vaqti:\n{end}"
        target = message_or_call.message if isinstance(message_or_call, types.CallbackQuery) else message_or_call
        if s.get("prize_photo"):
            await target.answer_photo(s["prize_photo"], caption=text, parse_mode="HTML")
        else:
            await target.answer(text, parse_mode="HTML")

    async def show_rules(message_or_call):
        text = (
            "ℹ️ Qoidalar:\n\n"
            "1. Kanalga obuna bo'ling.\n"
            "2. Maxsus havolangizni do'stlarga yuboring.\n"
            "3. Har bir tasdiqlangan referal +1 imkoniyat beradi.\n"
            "4. Konkurs oxirida kanalda bo'lmaganlar chiqariladi."
        )
        if isinstance(message_or_call, types.CallbackQuery):
            await message_or_call.message.answer(text)
        else:
            await message_or_call.answer(text)

    @dp.callback_query(F.data == "check_sub")
    async def check_sub(call: types.CallbackQuery):
        if settings().get("status") == "paused":
            await call.answer("Konkurs vaqtincha pauzada.", show_alert=True)
            return
        ok, info = await confirm_subscription(bot, call.from_user)
        if not ok:
            await call.answer(info, show_alert=True)
            return
        if info:
            await call.message.answer(
                f"✅ Siz konkursga {html.escape(info)} havolasi orqali qo'shildingiz.\n\n"
                "🎯 Endi imkoniyatingizni oshirish uchun do'stlaringizni taklif qiling.",
                reply_markup=menu_kb(is_admin(call.from_user.id)),
            )
        else:
            await call.message.answer(
                "✅ Siz konkursga muvaffaqiyatli qo'shildingiz.\n\n"
                "🎯 Endi imkoniyatingizni oshirish uchun do'stlaringizni taklif qiling.",
                reply_markup=menu_kb(is_admin(call.from_user.id)),
            )
        await call.answer()

    @dp.callback_query(F.data == "my_link")
    async def my_link(call: types.CallbackQuery):
        await show_my_link(call)
        await call.answer()

    @dp.callback_query(F.data == "my_refs")
    async def my_refs(call: types.CallbackQuery):
        await show_refs(call)
        await call.answer()

    @dp.callback_query(F.data == "rating")
    async def rating(call: types.CallbackQuery):
        await show_rating(call)
        await call.answer()

    @dp.callback_query(F.data == "prize")
    async def prize(call: types.CallbackQuery):
        await show_prize(call)
        await call.answer()

    @dp.callback_query(F.data == "rules")
    async def rules(call: types.CallbackQuery):
        await show_rules(call)
        await call.answer()

    @dp.callback_query(F.data == "back_menu")
    async def back_menu(call: types.CallbackQuery):
        await call.message.answer("🎉 Menu", reply_markup=menu_kb(is_admin(call.from_user.id)))
        await call.answer()

    @dp.message(F.text.in_({"🔗 Mening havolam", "Mening havolam"}))
    async def menu_link(message: types.Message):
        await show_my_link(message)

    @dp.message(F.text.in_({"👥 Referallarim", "Referallarim"}))
    async def menu_refs(message: types.Message):
        await show_refs(message)

    @dp.message(F.text.in_({"🎁 Sovg'a", "Sovg'a"}))
    async def menu_prize(message: types.Message):
        await show_prize(message)

    @dp.message(F.text.in_({"🏆 Reyting", "Reyting"}))
    async def menu_rating(message: types.Message):
        await show_rating(message)

    @dp.message(F.text.in_({"ℹ️ Qoidalar", "Qoidalar"}))
    async def menu_rules(message: types.Message):
        await show_rules(message)

    async def open_admin_action(message: types.Message, state: FSMContext, action: str):
        if not is_admin(message.from_user.id):
            return
        if action == "channel":
            await state.set_state(AdminSt.channel)
            await message.answer("📢 Kanal username/id yuboring.\nMasalan: @kanal yoki -100... | invite_link")
        elif action == "prize":
            await state.set_state(AdminSt.prize_title)
            await message.answer(
                "🎁 Sovg'a joylash boshlandi.\n\n"
                "1/4. Sovg'a nomini yuboring.\nMasalan: iPhone 15 Pro"
            )
        elif action == "photo":
            await state.set_state(AdminSt.photo)
            await message.answer("🖼 Sovg'a rasmini yuboring.")
        elif action == "time":
            await state.set_state(AdminSt.time)
            await message.answer("📅 Tugash vaqtini yuboring.\nFormat: 2026-05-28 20:00")
        elif action == "status":
            await message.answer(status_text(), parse_mode="HTML", reply_markup=status_kb())
        elif action == "winner":
            winner = await draw_winner(bot, manual=True)
            await message.answer("🏆 G'olib tanlandi." if winner else "Qatnashchi yo'q.", reply_markup=admin_kb())
        elif action == "manual_winner":
            await state.set_state(AdminSt.manual_winner)
            await message.answer("✍️ G'olib qilish uchun user ID yoki @username yuboring.")
        elif action == "find_user":
            await state.set_state(AdminSt.find_user)
            await message.answer("🔎 User ID yoki @username yuboring.")
        elif action == "ref_adjust":
            await state.set_state(AdminSt.ref_adjust)
            await message.answer("➕ Format: user_id +5 yoki @username -2")
        elif action == "post":
            await message.answer(f"📝 Tayyor post:\n\n{contest_post_text()}", reply_markup=admin_kb())
        elif action == "daily_top":
            items = top_users(10)
            text = "🔥 TOP referallar\n\n"
            if not items:
                text += "Hali ma'lumot yo'q."
            for index, item in enumerate(items, 1):
                text += f"{index}. {html.escape(user_title(item))} - {ref_count(item)}\n"
            await message.answer(text, reply_markup=admin_kb())
        elif action == "antifake":
            removed = await refresh_subscriptions(bot)
            await message.answer(f"🛡 Tekshirildi. Obunadan chiqqanlar: {removed}", reply_markup=admin_kb())
        elif action == "stats":
            data = users()
            total = len(data)
            subscribed = sum(1 for u in data.values() if u.get("subscribed"))
            refs = sum(ref_count(u) for u in data.values())
            await message.answer(
                f"📊 Statistika\n\nJami users: {total}\nObuna bo'lganlar: {subscribed}\nReferallar: {refs}\nStatus: {settings().get('status')}",
                reply_markup=admin_kb(),
            )
        elif action == "broadcast":
            await state.set_state(AdminSt.broadcast)
            await message.answer("📤 Reklama matnini yuboring.")
        elif action == "block":
            await state.set_state(AdminSt.block)
            await message.answer("🚫 Bloklash uchun user ID yuboring.")
        elif action == "reset":
            save_users({})
            s = settings()
            s["winner_id"] = ""
            s["winner_time"] = ""
            save_json(SETTINGS_F, s)
            await message.answer("🔄 Konkurs reset qilindi.", reply_markup=admin_kb())

    ADMIN_TEXT_ACTIONS = {
        "📢 Majburiy kanal": "channel",
        "🎁 Sovg'a joylash": "prize",
        "🎁 Sovg'a": "prize",
        "🖼 Sovg'a rasmi": "photo",
        "📅 Konkurs vaqti": "time",
        "⏸ Status/Pauza": "status",
        "✍️ Qo'lda g'olib": "manual_winner",
        "🔎 User qidirish": "find_user",
        "📝 Post generator": "post",
        "📊 Statistika": "stats",
        "📤 Reklama": "broadcast",
        "🚫 User bloklash": "block",
        "🛠 Admin": "admin",
    }

    @dp.message(F.text.in_(set(ADMIN_TEXT_ACTIONS.keys())))
    async def admin_reply_buttons(message: types.Message, state: FSMContext):
        action = ADMIN_TEXT_ACTIONS[message.text]
        if action == "admin":
            await message.answer("🛠 Admin panel\n\nKerakli bo'limni tanlang.", reply_markup=admin_kb())
            return
        await open_admin_action(message, state, action)

    @dp.callback_query(F.data.in_({"status_pause", "status_active"}))
    async def status_toggle(call: types.CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        s = settings()
        s["status"] = "paused" if call.data == "status_pause" else "active"
        save_json(SETTINGS_F, s)
        await call.message.edit_text(status_text(), parse_mode="HTML", reply_markup=status_kb())
        await call.answer("Status yangilandi.")

    @dp.callback_query(F.data.startswith("a_"))
    async def admin_callbacks(call: types.CallbackQuery, state: FSMContext):
        if not is_admin(call.from_user.id):
            return
        action = call.data
        if action == "a_channel":
            await state.set_state(AdminSt.channel)
            await call.message.edit_text("Kanal username/id yuboring. Masalan: @kanal yoki -100...")
        elif action == "a_prize":
            await state.set_state(AdminSt.prize_title)
            await call.message.answer(
                "🎁 Sovg'a joylash boshlandi.\n\n"
                "1/4. Sovg'a nomini yuboring.\nMasalan: iPhone 15 Pro",
                reply_markup=admin_kb(),
            )
        elif action == "a_photo":
            await state.set_state(AdminSt.photo)
            await call.message.edit_text("Sovg'a rasmini yuboring.")
        elif action == "a_time":
            await state.set_state(AdminSt.time)
            await call.message.edit_text("Tugash vaqtini yuboring.\nFormat: 2026-05-28 20:00")
        elif action == "a_status":
            await call.message.answer(status_text(), parse_mode="HTML", reply_markup=status_kb())
        elif action == "a_winner":
            winner = await draw_winner(bot, manual=True)
            await call.message.answer("G'olib tanlandi." if winner else "G'olib tanlash uchun qatnashchi yo'q.", reply_markup=admin_kb())
        elif action == "a_manual_winner":
            await state.set_state(AdminSt.manual_winner)
            await call.message.edit_text("G'olib qilish uchun user ID yoki @username yuboring.")
        elif action == "a_find_user":
            await state.set_state(AdminSt.find_user)
            await call.message.edit_text("User ID yoki @username yuboring.")
        elif action == "a_ref_adjust":
            await state.set_state(AdminSt.ref_adjust)
            await call.message.edit_text("Format: user_id +5 yoki @username -2")
        elif action == "a_post":
            await call.message.answer(contest_post_text(), reply_markup=admin_kb())
        elif action == "a_daily_top":
            items = top_users(10)
            text = "TOP referallar\n\n"
            if not items:
                text += "Hali ma'lumot yo'q."
            for index, item in enumerate(items, 1):
                text += f"{index}. {html.escape(user_title(item))} - {ref_count(item)}\n"
            await call.message.answer(text, reply_markup=admin_kb())
        elif action == "a_antifake":
            removed = await refresh_subscriptions(bot)
            await call.message.answer(f"Tekshirildi. Obunadan chiqqanlar: {removed}", reply_markup=admin_kb())
        elif action == "a_stats":
            data = users()
            total = len(data)
            subscribed = sum(1 for u in data.values() if u.get("subscribed"))
            refs = sum(len(u.get("referrals", [])) for u in data.values())
            await call.message.answer(
                f"Statistika\n\nJami users: {total}\nObuna bo'lganlar: {subscribed}\nReferallar: {refs}\nStatus: {settings().get('status')}",
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
            await call.message.answer("Konkurs reset qilindi.", reply_markup=admin_kb())
        await call.answer()

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

    @dp.message(AdminSt.prize_title)
    async def set_prize_title(message: types.Message, state: FSMContext):
        s = settings()
        s["prize_title"] = message.text.strip()
        save_json(SETTINGS_F, s)
        await state.set_state(AdminSt.prize_desc)
        await message.answer("📝 2/4. Endi sovg'a description yozing.\nMasalan: Yangi, 256GB, natural titanium.")

    @dp.message(AdminSt.prize_desc)
    async def set_prize_desc(message: types.Message, state: FSMContext):
        s = settings()
        s["prize_text"] = message.text.strip()
        save_json(SETTINGS_F, s)
        await state.set_state(AdminSt.photo)
        await message.answer("🖼 3/4. Endi sovg'a rasmini yuboring.")

    @dp.message(AdminSt.photo, F.photo)
    async def set_photo(message: types.Message, state: FSMContext):
        s = settings()
        s["prize_photo"] = message.photo[-1].file_id
        save_json(SETTINGS_F, s)
        await state.set_state(AdminSt.time)
        await message.answer("📅 4/4. Konkurs tugash vaqtini yuboring.\nFormat: 2026-05-28 20:00")

    @dp.message(AdminSt.photo)
    async def photo_required(message: types.Message):
        await message.answer("🖼 Iltimos, sovg'a rasmini oddiy foto qilib yuboring.")

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
        await message.answer("✅ Sovg'a to'liq saqlandi va konkurs vaqti belgilandi.", reply_markup=admin_kb())

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

    @dp.message(AdminSt.find_user)
    async def find_user_admin(message: types.Message, state: FSMContext):
        uid, item = find_user_by_query(message.text)
        await state.clear()
        if not item:
            await message.answer("User topilmadi.", reply_markup=admin_kb())
            return
        await message.answer(user_info_text(uid, item), parse_mode="HTML", reply_markup=admin_kb())

    @dp.message(AdminSt.ref_adjust)
    async def ref_adjust_admin(message: types.Message, state: FSMContext):
        item, result = adjust_refs(message.text)
        await state.clear()
        await message.answer(result, reply_markup=admin_kb())

    asyncio.create_task(scheduler(bot))
    print("Konkurs bot ishga tushdi.")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        try:
            LOCK_F.unlink(missing_ok=True)
        except Exception:
            pass
