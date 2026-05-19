import asyncio
import html
import json
import os
import re
import shutil
import subprocess
import wave
from datetime import date, datetime
from pathlib import Path

import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

load_dotenv()

MASTER_TOKEN = ("8883531040:AAEYrBOr7zv2ilVog9KFbqmucYOKJiqCIDw")
if not MASTER_TOKEN:
    raise RuntimeError("MASTER_TOKEN .env faylida topilmadi")

master_bot = Bot(token=MASTER_TOKEN)
master_dp = Dispatcher(storage=MemoryStorage())

DATA_DIR = Path("data")
BOTS_DIR = DATA_DIR / "bots"
TMP_DIR = DATA_DIR / "tmp"
MEDIA_DIR = DATA_DIR / "media"
IMPORTS_DIR = DATA_DIR / "imports"
ACCOUNTS_F = DATA_DIR / "accounts.json"

BOTS_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
IMPORTS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PROMPT = (
    "Siz Telegramda Ozodbek nomidan yozasiz. Hech qachon o'zingizni AI, bot, model yoki "
    "yordamchi deb tanishtirmang. Foydalanuvchi 'kimsan?' desa faqat 'Ozodbekman.' deb javob bering. "
    "Har doim o'zbek tilida, odamdek, qisqa va tabiiy yozing. Savolga javob bergandan keyin "
    "keraksiz qo'shimcha savol bermang. Emoji kerak bo'lsa juda kam ishlating. "
    "Noma'lum narsani o'ylab topmang."
)
DEFAULT_FALLBACK = "Hozir operator javob beradi. Iltimos, biroz kuting."
TEXT_MODEL = "gemini-2.5-flash-lite"
AUDIO_MODEL = "gemini-2.5-flash"
AUDIO_FALLBACK_MODEL = "gemini-2.5-flash-lite"
STYLE_MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash"]
TTS_MODEL = "gemini-2.5-flash-preview-tts"
BAD_WORDS = {
    "blya", "бля", "бляд", "suka", "сука", "нах", "нахуй", "xuy", "ху",
    "pizda", "пизд", "dalbayob", "dalbaeb", "долба", "еб", "yeban",
    "qotoq", "kot", "jalab", "haromi", "iflos", "padar", "lanat",
}
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".m4v"}
MEDIA_REQUEST_WORDS = {
    "rasm", "rasim", "surat", "foto", "photo", "kartinka", "video",
    "tashaber", "tashlab", "tasha", "yubor", "jonat", "jo'nat", "ber",
}
MEDIA_STOPWORDS = MEDIA_REQUEST_WORDS | {
    "shu", "mana", "manabu", "anavi", "iltimos", "menga", "meni", "ni", "ga",
    "qilib", "kerak", "bor", "bo'lsa", "bolsa", "papka", "papkaga", "kirib",
}


def jload(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        content = path.read_text(encoding="utf-8").strip()
        return json.loads(content) if content else {}
    except Exception:
        return {}


def jsave(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_accounts():
    return jload(ACCOUNTS_F)


def save_accounts(data):
    jsave(ACCOUNTS_F, data)


def bdir(token):
    return BOTS_DIR / token.replace(":", "_")[:20]


def bl(token, name):
    return jload(bdir(token) / f"{name}.json")


def bs(token, name, data):
    jsave(bdir(token) / f"{name}.json", data)


def bot_settings(token):
    settings = bl(token, "settings")
    changed = False
    defaults = {
        "welcome": "Assalomu alaykum, <b>{name}</b>! Savolingizni yozing, men yordam beraman.",
        "ai_enabled": False,
        "gemini_api_key": "",
        "gemini_backup_keys": [],
        "gemini_active_key": 0,
        "ai_prompt": DEFAULT_PROMPT,
        "ai_fallback": DEFAULT_FALLBACK,
        "voice_reply": False,
        "tts_voice": "Charon",
        "business_connection_id": "",
        "can_manage_stories": False,
        "style_enabled": True,
        "style_strength": "strict",
        "voice_reply_mode": "text_and_voice",
        "profanity_filter": True,
    }
    for key, value in defaults.items():
        if key not in settings:
            settings[key] = value
            changed = True
    if changed:
        bs(token, "settings", settings)
    return settings


def hide_key(key):
    if not key:
        return "kiritilmagan"
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def get_gemini_keys(settings):
    keys = []
    primary = (settings.get("gemini_api_key") or "").strip()
    if primary:
        keys.append(primary)
    for key in settings.get("gemini_backup_keys") or []:
        key = (key or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def get_active_gemini_key(settings):
    keys = get_gemini_keys(settings)
    if not keys:
        return ""
    index = int(settings.get("gemini_active_key", 0) or 0)
    if index < 0 or index >= len(keys):
        index = 0
    return keys[index]


def set_active_gemini_key(token, key):
    settings = bot_settings(token)
    keys = get_gemini_keys(settings)
    if key in keys:
        settings["gemini_active_key"] = keys.index(key)
        bs(token, "settings", settings)


def inc_stat(token, key, amount=1):
    stats = bl(token, "stats")
    today = str(date.today())
    if stats.get("day") != today:
        stats["day"] = today
        stats["today"] = 0
        stats["manual_today"] = 0
        stats["ai_today"] = 0
    stats["total"] = stats.get("total", 0) + amount
    stats["today"] = stats.get("today", 0) + amount
    stats[key] = stats.get(key, 0) + amount
    if key == "manual_replies":
        stats["manual_today"] = stats.get("manual_today", 0) + amount
    if key == "ai_replies":
        stats["ai_today"] = stats.get("ai_today", 0) + amount
    bs(token, "stats", stats)


def add_user(token, user):
    users = bl(token, "users")
    uid = str(user.id)
    if uid not in users:
        users[uid] = {
            "user_id": user.id,
            "username": user.username or "",
            "full_name": user.full_name or "",
            "blocked": False,
            "joined": datetime.now().isoformat(),
        }
    else:
        users[uid]["username"] = user.username or ""
        users[uid]["full_name"] = user.full_name or ""
    bs(token, "users", users)


def set_user_age(token, user_id, age):
    if not age or age < 1 or age > 120:
        return
    users = bl(token, "users")
    uid = str(user_id)
    users.setdefault(uid, {"user_id": user_id, "blocked": False})
    users[uid]["age"] = age
    users[uid]["age_updated"] = datetime.now().isoformat()
    bs(token, "users", users)


def get_user_age(token, user_id):
    return bl(token, "users").get(str(user_id), {}).get("age")


def extract_age(text):
    if not text:
        return None
    patterns = [
        r"\b(?:yoshim|yosh|менга|menga)\s*(\d{1,2})\b",
        r"\b(\d{1,2})\s*(?:yosh|yoshda|ёш|лет)\b",
        r"\b(?:i am|i'm)\s*(\d{1,2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            age = int(match.group(1))
            if 1 <= age <= 99:
                return age
    return None


def is_blocked(token, uid):
    return bl(token, "users").get(str(uid), {}).get("blocked", False)


def has_bad_words(text):
    text = (text or "").lower()
    clean = re.sub(r"[^a-zа-яё0-9'`]+", " ", text)
    words = clean.split()
    for word in words:
        if word in BAD_WORDS:
            return True
        if len(word) >= 4 and any(bad in word for bad in BAD_WORDS):
            return True
    return False


def profanity_warning(age):
    if isinstance(age, int):
        return "Sokinmang." if age > 16 else "Sokinma."
    return "Iltimos, sokinroq yozing."


def media_words(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-zа-яё0-9'`]+", " ", text)
    return [word for word in text.split() if word]


def wants_media(text):
    words = set(media_words(text))
    return bool(words & MEDIA_REQUEST_WORDS) and bool(words & {"rasm", "rasim", "surat", "foto", "photo", "kartinka", "video"})


def find_media_file(text):
    words = [word for word in media_words(text) if word not in MEDIA_STOPWORDS and len(word) > 1]
    files = [path for path in MEDIA_DIR.rglob("*") if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS]
    if not files:
        return None
    if not words:
        return files[0]

    best_path = None
    best_score = 0
    for path in files:
        name = re.sub(r"[^a-zа-яё0-9'`]+", " ", path.stem.lower())
        score = sum(1 for word in words if word in name)
        if score > best_score:
            best_score = score
            best_path = path
    return best_path if best_score else None


def find_reply(token, text):
    if not text:
        return None
    text_l = text.lower()
    for key, value in bl(token, "replies").items():
        if key.lower() in text_l:
            return value
    return None


def add_history(token, user_id, role, content):
    history = bl(token, "history")
    uid = str(user_id)
    history.setdefault(uid, [])
    history[uid].append({
        "role": role,
        "content": content[:4000],
        "time": datetime.now().isoformat(),
    })
    history[uid] = history[uid][-12:]
    bs(token, "history", history)


def get_history_text(token, user_id):
    items = bl(token, "history").get(str(user_id), [])[-8:]
    if not items:
        return ""
    lines = []
    for item in items:
        role = "Foydalanuvchi" if item.get("role") == "user" else "Bot"
        lines.append(f"{role}: {item.get('content', '')}")
    return "\n".join(lines)


def add_style_sample(token, text):
    text = (text or "").strip()
    if len(text) < 2:
        return
    samples = bl(token, "style_samples").get("items", [])
    samples.append({
        "text": text[:1000],
        "time": datetime.now().isoformat(),
    })
    samples = samples[-80:]
    bs(token, "style_samples", {"items": samples})
    return len(samples)


def get_style_text(token):
    samples = bl(token, "style_samples").get("items", [])[-25:]
    profile = bl(token, "style_profile").get("text", "")
    lines = [f"- {item.get('text', '')}" for item in samples if item.get("text")]
    sample_text = "\n".join(lines)
    if profile and sample_text:
        return f"Uslub profili:\n{profile}\n\nOxirgi real namunalar:\n{sample_text}"
    if profile:
        return f"Uslub profili:\n{profile}"
    return sample_text


def clear_style_samples(token):
    bs(token, "style_samples", {"items": []})
    bs(token, "style_profile", {})


def import_dir(token):
    path = IMPORTS_DIR / token.replace(":", "_")[:20]
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_plain_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return ""


def extract_messages_from_json(data, owner_names):
    messages = []
    if isinstance(data, dict) and isinstance(data.get("chats"), dict):
        for chat in data["chats"].get("list", []):
            messages.extend(extract_messages_from_json(chat, owner_names))
        return messages

    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        for item in data["messages"]:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            sender = str(item.get("from") or item.get("from_id") or "").lower()
            if owner_names and not any(name in sender for name in owner_names):
                continue
            text = normalize_plain_text(item.get("text", "")).strip()
            if text:
                messages.append(text)
    return messages


def extract_messages_from_html(content):
    rows = re.findall(
        r'<div class="message default clearfix[^"]*".*?</div>\s*</div>\s*</div>',
        content,
        flags=re.S | re.I,
    )
    messages = []
    for row in rows:
        text_match = re.search(r'<div class="text">(.*?)</div>', row, flags=re.S | re.I)
        if not text_match:
            continue
        text = re.sub(r"<br\s*/?>", "\n", text_match.group(1), flags=re.I)
        text = re.sub(r"<.*?>", "", text)
        text = html.unescape(text).strip()
        if text:
            messages.append(text)
    return messages


def parse_chat_export(path, owner_names):
    path = Path(path)
    suffix = path.suffix.lower()
    content = path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".json":
        data = json.loads(content)
        return extract_messages_from_json(data, owner_names)
    if suffix in {".html", ".htm"}:
        return extract_messages_from_html(content)
    raise ValueError("Faqat .json yoki .html Telegram export fayl qabul qilinadi")


def is_quota_error(exc):
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower()


def is_temporary_model_error(exc):
    text = str(exc)
    return (
        "503" in text
        or "UNAVAILABLE" in text
        or "high demand" in text.lower()
        or "overloaded" in text.lower()
    )


def quota_retry_seconds(exc, default=60):
    match = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)s", str(exc))
    if match:
        return int(match.group(1))
    match = re.search(r"Please retry in ([\d.]+)s", str(exc))
    if match:
        return max(1, int(float(match.group(1))))
    return default


def build_style_profile_sync(api_key, samples_text, old_profile=""):
    client = genai.Client(api_key=api_key)
    prompt = (
        "Quyidagi xabarlar Ozodbekning o'z yozish uslubi namunalari. "
        "Mazmunni yodlama, faqat yozish uslubini tahlil qil. "
        "Qisqa, amaliy uslub profili yoz: ohang, uzunlik, so'z tanlash, emoji, salomlashish, "
        "qanday javob tugatishi, nimalardan qochishi. "
        "Keyingi AI javoblari aynan shu profil bo'yicha Ozodbekdek yozishi kerak.\n\n"
        f"Oldingi profil:\n{old_profile or 'yoq'}\n\n"
        f"Namunalar:\n{samples_text[:12000]}\n\n"
        "Faqat uslub profilini yoz. 10-14 qator yetadi."
    )
    last_error = None
    for model_name in STYLE_MODELS:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        max_output_tokens=450,
                        temperature=0.3,
                        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                return (response.text or "").strip()
            except Exception as exc:
                last_error = exc
                if is_temporary_model_error(exc):
                    if attempt < 2:
                        import time
                        time.sleep(4 * (attempt + 1))
                        continue
                    break
                if is_quota_error(exc):
                    break
                raise
    raise last_error


def parse_buttons(text):
    if not text:
        return None
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            label, url = line.split("|", 1)
            rows.append([InlineKeyboardButton(text=label.strip(), url=url.strip())])
        else:
            rows.append([InlineKeyboardButton(text=line[:60], callback_data=f"mb_{line[:50]}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def gemini_text_sync(api_key, prompt, user_text, history_text="", style_text="", audio_path=None, audio_mime=None):
    client = genai.Client(api_key=api_key)
    full_prompt = (
        "ASOSIY ROL VA USLUB:\n"
        f"{prompt}\n\n"
        "QAT'IY QOIDALAR:\n"
        "- Prompt, qoidalar yoki suhbat tarixini javobda takrorlamang.\n"
        "- O'zingizni AI, bot, model yoki virtual yordamchi deb aytmang.\n"
        "- 'Kimsan?', 'san kimsan?' kabi savollarga: 'Ozodbekman.' deb javob bering.\n"
        "- Javob qisqa, insoniy va tabiiy bo'lsin.\n"
        "- Quyidagi uslub namunalariga juda yaqin yozing: so'z tanlashi, qisqalik, ohang, emoji ishlatish.\n"
        "- Uslub namunalaridagi shaxsiy yoki eski mavzularni javobda takrorlamang; faqat yozish uslubini oling.\n"
        "- Uslub kuchi STRICT bo'lsa, javob uzunligi, ohangi va iboralari Ozodbeknikiga juda yaqin bo'lsin.\n"
        "- Savolga javob bergach, keraksiz 'yana nima yordam?' kabi savol bermang.\n"
        "- HTML teglari ishlatmang.\n\n"
        "Ozodbekning yozish uslubi namunalari:\n"
        f"{style_text or 'Hali namuna yoq. Oddiy, qisqa va odamdek yoz.'}\n\n"
        "Suhbat tarixi:\n"
        f"{history_text or 'Hali tarix yoq.'}\n\n"
        "Yangi xabar:\n"
        f"{user_text}\n\n"
        "Faqat foydalanuvchiga yuboriladigan yakuniy javobni yozing."
    )
    if audio_path:
        audio_bytes = Path(audio_path).read_bytes()
        last_error = None
        for model_name in (AUDIO_MODEL, AUDIO_FALLBACK_MODEL):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        full_prompt,
                        genai_types.Part.from_bytes(
                            data=audio_bytes,
                            mime_type=audio_mime or "audio/ogg",
                        ),
                    ],
                    config=genai_types.GenerateContentConfig(
                        max_output_tokens=220,
                        temperature=0.6,
                        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                return (response.text or "").strip()
            except Exception as exc:
                last_error = exc
                if not is_quota_error(exc):
                    raise
        raise last_error
    else:
        response = client.models.generate_content(
            model=TEXT_MODEL,
            contents=full_prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=220,
                temperature=0.6,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            ),
        )
    return (response.text or "").strip()


def write_wave(path, pcm, channels=1, rate=24000, sample_width=2):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def gemini_tts_sync(api_key, text, voice_name):
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=TTS_MODEL,
        contents=f"O'zbek tilida tabiiy va sokin ohangda o'qing:\n{text}",
        config=genai_types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name=voice_name or "Charon",
                    )
                )
            ),
        ),
    )
    return response.candidates[0].content.parts[0].inline_data.data


def convert_wav_to_ogg(wav_path, ogg_path):
    if not shutil.which("ffmpeg"):
        return False
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            str(ogg_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0 and Path(ogg_path).exists()


def convert_input_audio(input_path):
    input_path = Path(input_path)
    if not shutil.which("ffmpeg"):
        return input_path, None

    wav_path = input_path.with_suffix(".input.wav")
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0 and wav_path.exists():
        return wav_path, "audio/wav"
    return input_path, None


def post_story_sync(bot_token, business_connection_id, media_path, media_type, caption=""):
    url = f"https://api.telegram.org/bot{bot_token}/postStory"
    if media_type == "photo":
        content = {"type": "photo", "photo": "attach://story_media"}
    else:
        content = {"type": "video", "video": "attach://story_media"}

    data = {
        "business_connection_id": business_connection_id,
        "content": json.dumps(content, ensure_ascii=False),
        "active_period": "86400",
        "caption": caption[:2048],
        "parse_mode": "HTML",
    }
    with open(media_path, "rb") as media_file:
        response = requests.post(url, data=data, files={"story_media": media_file}, timeout=60)
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "postStory xatosi"))
    return payload.get("result", {})


running_bots = {}


def create_user_bot(token: str, admin_id: int):
    ubot = Bot(token=token)
    udp = Dispatcher(storage=MemoryStorage())

    class St(StatesGroup):
        kw = State()
        rtext = State()
        rmedia = State()
        rbuttons = State()
        bc = State()
        welcome = State()
        blk = State()
        unblk = State()
        gemini_key = State()
        ai_prompt = State()
        ai_fallback = State()
        ai_test = State()
        backup_keys = State()
        style_sample = State()
        style_import = State()
        story_media = State()

    def main_kb():
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Avto javoblar", callback_data="u_replies"),
                InlineKeyboardButton(text="AI sozlamalar", callback_data="u_ai"),
            ],
            [
                InlineKeyboardButton(text="Foydalanuvchilar", callback_data="u_users"),
                InlineKeyboardButton(text="Broadcast", callback_data="u_broadcast"),
            ],
            [
                InlineKeyboardButton(text="Statistika", callback_data="u_stats"),
                InlineKeyboardButton(text="Bot sozlamalari", callback_data="u_bot_settings"),
            ],
            [InlineKeyboardButton(text="Telegram Story", callback_data="u_story")],
        ])

    def back_kb():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Orqaga", callback_data="u_back")]
        ])

    def skip_kb():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="O'tkazish", callback_data="u_skip")],
            [InlineKeyboardButton(text="Orqaga", callback_data="u_back")],
        ])

    def replies_kb():
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Javob qo'shish", callback_data="u_add"),
                InlineKeyboardButton(text="O'chirish", callback_data="u_del_list"),
            ],
            [InlineKeyboardButton(text="Orqaga", callback_data="u_back")],
        ])

    def ai_kb():
        settings = bot_settings(token)
        ai_text = "AI: ON" if settings.get("ai_enabled") else "AI: OFF"
        voice_text = "Ovozli javob: ON" if settings.get("voice_reply") else "Ovozli javob: OFF"
        style_text = "Uslub: ON" if settings.get("style_enabled") else "Uslub: OFF"
        mode_text = "Voice: matn+ovoz" if settings.get("voice_reply_mode") == "text_and_voice" else "Voice: faqat ovoz"
        profanity_text = "So'kinish filter: ON" if settings.get("profanity_filter") else "So'kinish filter: OFF"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=ai_text, callback_data="u_ai_toggle")],
            [InlineKeyboardButton(text="Gemini API key", callback_data="u_ai_key")],
            [InlineKeyboardButton(text="Zaxira Gemini keylar", callback_data="u_backup_keys")],
            [InlineKeyboardButton(text="AI prompt", callback_data="u_ai_prompt")],
            [InlineKeyboardButton(text=style_text, callback_data="u_style_toggle")],
            [InlineKeyboardButton(text=f"Uslub kuchi: {settings.get('style_strength', 'strict').upper()}", callback_data="u_style_strength")],
            [InlineKeyboardButton(text="Chat import yuklash", callback_data="u_style_import")],
            [InlineKeyboardButton(text="Uslub namuna qo'shish", callback_data="u_style_add")],
            [InlineKeyboardButton(text="Uslubni tahlil qilish", callback_data="u_style_analyze")],
            [InlineKeyboardButton(text="Uslubni tozalash", callback_data="u_style_clear")],
            [InlineKeyboardButton(text="Fallback javob", callback_data="u_ai_fallback")],
            [InlineKeyboardButton(text=voice_text, callback_data="u_voice_toggle")],
            [InlineKeyboardButton(text=mode_text, callback_data="u_voice_mode")],
            [InlineKeyboardButton(text=profanity_text, callback_data="u_profanity_toggle")],
            [InlineKeyboardButton(text="AI test", callback_data="u_ai_test")],
            [InlineKeyboardButton(text="Orqaga", callback_data="u_back")],
        ])

    def bot_settings_kb():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Xush kelibsiz matni", callback_data="u_welcome")],
            [
                InlineKeyboardButton(text="Bloklash", callback_data="u_block"),
                InlineKeyboardButton(text="Blokdan chiqarish", callback_data="u_unblock"),
            ],
            [InlineKeyboardButton(text="Orqaga", callback_data="u_back")],
        ])

    def story_kb():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Story qo'yish", callback_data="u_story_add")],
            [InlineKeyboardButton(text="Orqaga", callback_data="u_back")],
        ])

    def remember_quota_error(exc):
        settings = bot_settings(token)
        retry_after = quota_retry_seconds(exc)
        settings["gemini_quota_error"] = {
            "time": datetime.now().isoformat(),
            "retry_after": retry_after,
            "message": str(exc)[:700],
        }
        bs(token, "settings", settings)
        return retry_after

    def admin_text():
        settings = bot_settings(token)
        status = "yoqilgan" if settings.get("ai_enabled") else "o'chirilgan"
        voice = "yoqilgan" if settings.get("voice_reply") else "o'chirilgan"
        return (
            "<b>Admin panel</b>\n\n"
            "Bot boshqaruvi quyidagi bo'limlarga ajratildi.\n"
            f"AI auto-javob: <b>{status}</b>\n"
            f"Ovozli javob: <b>{voice}</b>"
        )

    async def send_manual_reply(chat_id, reply, bc_id=None):
        media_type = reply.get("media_type")
        media = reply.get("media")
        text = reply.get("text", "")
        kb = parse_buttons(reply.get("buttons"))
        extra = {"parse_mode": "HTML"}
        if bc_id:
            extra["business_connection_id"] = bc_id
        try:
            if media and media_type == "photo":
                await ubot.send_photo(chat_id, media, caption=text or None, reply_markup=kb, **extra)
            elif media and media_type == "video":
                await ubot.send_video(chat_id, media, caption=text or None, reply_markup=kb, **extra)
            elif text:
                await ubot.send_message(chat_id, text, reply_markup=kb, **extra)
        except Exception as exc:
            print(f"Manual javob xatosi: {exc}")

    async def rebuild_style_profile(silent=True):
        settings = bot_settings(token)
        api_key = get_active_gemini_key(settings)
        samples = bl(token, "style_samples").get("items", [])
        if not api_key or len(samples) < 3:
            return False
        samples_text = "\n".join(f"- {item.get('text', '')}" for item in samples[-80:] if item.get("text"))
        old_profile = bl(token, "style_profile").get("text", "")
        try:
            profile = await asyncio.to_thread(build_style_profile_sync, api_key, samples_text, old_profile)
            if profile:
                bs(token, "style_profile", {
                    "text": profile,
                    "sample_count": len(samples),
                    "updated": datetime.now().isoformat(),
                })
                return True
        except Exception as exc:
            print(f"Uslub tahlili xatosi: {exc}")
            if not silent:
                try:
                    await ubot.send_message(
                        admin_id,
                        f"<b>Uslub tahlili xatosi</b>\n<code>{html.escape(str(exc)[:700])}</code>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        return False

    async def maybe_rebuild_style_profile(sample_count):
        return

    async def send_ai_response(message, user_text, bc_id=None, audio_path=None, audio_mime=None):
        settings = bot_settings(token)
        api_keys = get_gemini_keys(settings)
        if not settings.get("ai_enabled") or not api_keys:
            if not bc_id:
                await message.answer(settings.get("ai_fallback") or DEFAULT_FALLBACK)
            return

        history_text = get_history_text(token, message.from_user.id)
        style_text = get_style_text(token) if settings.get("style_enabled") else ""
        if style_text and settings.get("style_strength") == "strict":
            style_text = (
                "USLUB KUCHI: STRICT. Javob imkon qadar Ozodbekning real yozish ohangiga yaqin bo'lsin.\n"
                f"{style_text}"
            )
        add_history(token, message.from_user.id, "user", user_text)
        prepared_audio = audio_path
        prepared_mime = audio_mime
        try:
            if audio_path:
                prepared_audio, converted_mime = await asyncio.to_thread(convert_input_audio, audio_path)
                if converted_mime:
                    prepared_mime = converted_mime
            answer = ""
            last_quota_error = None
            last_error = None
            active_key = ""
            start_index = int(settings.get("gemini_active_key", 0) or 0)
            ordered_keys = api_keys[start_index:] + api_keys[:start_index]
            for api_key in ordered_keys:
                try:
                    answer = await asyncio.to_thread(
                        gemini_text_sync,
                        api_key,
                        settings.get("ai_prompt") or DEFAULT_PROMPT,
                        user_text,
                        history_text,
                        style_text,
                        prepared_audio,
                        prepared_mime,
                    )
                    active_key = api_key
                    set_active_gemini_key(token, api_key)
                    break
                except Exception as exc:
                    last_error = exc
                    if is_quota_error(exc):
                        last_quota_error = exc
                        continue
                    raise
            if not answer and last_error:
                raise last_quota_error or last_error
            if not answer:
                answer = settings.get("ai_fallback") or DEFAULT_FALLBACK
            add_history(token, message.from_user.id, "assistant", answer)
            inc_stat(token, "ai_replies")

            extra = {}
            if bc_id:
                extra["business_connection_id"] = bc_id
            voice_only = bool(audio_path) and settings.get("voice_reply_mode") == "voice_only" and settings.get("voice_reply")
            if not voice_only:
                await ubot.send_message(message.chat.id, html.escape(answer), parse_mode="HTML", **extra)

            if settings.get("voice_reply") and not bc_id:
                await send_voice_answer(message.chat.id, active_key or api_keys[0], answer, settings.get("tts_voice"))
        except Exception as exc:
            print(f"Gemini xatosi: {exc}")
            retry_after = remember_quota_error(exc) if is_quota_error(exc) else None
            admin_msg = (
                "<b>Gemini quota tugadi</b>\n"
                f"Taxminan {retry_after} soniyadan keyin qayta urinib ko'ring.\n\n"
                "Ko'p ishlatsa, Google AI Studio billing yoqish yoki boshqa API key kerak bo'ladi."
                if retry_after else
                f"<b>Gemini xatosi</b>\n<code>{html.escape(str(exc)[:700])}</code>"
            )
            try:
                await ubot.send_message(
                    admin_id,
                    admin_msg,
                    parse_mode="HTML",
                )
            except Exception:
                pass
            if not bc_id:
                await message.answer(settings.get("ai_fallback") or DEFAULT_FALLBACK)
        finally:
            if prepared_audio and audio_path and Path(prepared_audio) != Path(audio_path):
                try:
                    Path(prepared_audio).unlink(missing_ok=True)
                except Exception:
                    pass

    async def send_voice_answer(chat_id, api_key, text, voice_name):
        stamp = f"{chat_id}_{int(datetime.now().timestamp() * 1000)}"
        wav_path = TMP_DIR / f"{stamp}.wav"
        ogg_path = TMP_DIR / f"{stamp}.ogg"
        try:
            pcm = await asyncio.to_thread(gemini_tts_sync, api_key, text[:1500], voice_name)
            await asyncio.to_thread(write_wave, wav_path, pcm)
            converted = await asyncio.to_thread(convert_wav_to_ogg, wav_path, ogg_path)
            if converted:
                await ubot.send_voice(chat_id, FSInputFile(ogg_path))
            else:
                await ubot.send_audio(chat_id, FSInputFile(wav_path), title="AI javob")
        except Exception as exc:
            print(f"TTS xatosi: {exc}")
        finally:
            for path in (wav_path, ogg_path):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass

    async def process_msg(message, state=None, bc_id=None, audio_path=None, audio_mime=None, text_override=None):
        user = message.from_user
        if not user:
            return
        if is_blocked(token, user.id):
            if not bc_id:
                await message.answer("Siz bloklangansiz.")
            return
        add_user(token, user)

        text = text_override or message.text or message.caption or ""
        age = extract_age(text)
        if age:
            set_user_age(token, user.id, age)

        if not text_override and bot_settings(token).get("profanity_filter") and has_bad_words(text):
            warning = profanity_warning(get_user_age(token, user.id))
            if bc_id:
                await ubot.send_message(message.chat.id, warning, business_connection_id=bc_id)
            else:
                await message.answer(warning)
            return

        if not text_override and wants_media(text):
            media_path = find_media_file(text)
            if media_path:
                try:
                    extra = {}
                    if bc_id:
                        extra["business_connection_id"] = bc_id
                    if media_path.suffix.lower() in {".mp4", ".mov", ".m4v"}:
                        await ubot.send_video(message.chat.id, FSInputFile(media_path), **extra)
                    else:
                        await ubot.send_photo(message.chat.id, FSInputFile(media_path), **extra)
                    return
                except Exception as exc:
                    print(f"Media yuborish xatosi: {exc}")
            elif not bc_id:
                await message.answer("Bu rasm media kutubxonadan topilmadi.")
                return

        manual = find_reply(token, text)
        if manual:
            await send_manual_reply(message.chat.id, manual, bc_id=bc_id)
            inc_stat(token, "manual_replies")
            return

        if text or audio_path:
            await send_ai_response(
                message,
                text or "Foydalanuvchi ovozli xabar yubordi.",
                bc_id=bc_id,
                audio_path=audio_path,
                audio_mime=audio_mime,
            )

    @udp.message(Command("start"))
    async def u_start(message: types.Message, state: FSMContext):
        await state.clear()
        user = message.from_user
        add_user(token, user)
        if user.id == admin_id:
            await message.answer(admin_text(), reply_markup=main_kb(), parse_mode="HTML")
            return
        settings = bot_settings(token)
        welcome = settings.get("welcome") or "Assalomu alaykum, <b>{name}</b>!"
        await message.answer(welcome.replace("{name}", html.escape(user.full_name)), parse_mode="HTML")

    @udp.message(Command("admin"))
    async def u_admin(message: types.Message, state: FSMContext):
        if message.from_user.id != admin_id:
            return
        await state.clear()
        await message.answer(admin_text(), reply_markup=main_kb(), parse_mode="HTML")

    @udp.message(F.text & ~F.text.startswith("/"))
    async def u_text(message: types.Message, state: FSMContext):
        if message.from_user.id == admin_id and await state.get_state():
            await u_admin_input(message, state)
            return
        await process_msg(message, state=state)

    @udp.message(F.photo | F.video)
    async def u_media_msg(message: types.Message, state: FSMContext):
        current_state = await state.get_state()
        if message.from_user.id == admin_id and current_state in {St.rmedia.state, St.story_media.state}:
            await u_admin_input(message, state)
            return
        await process_msg(message, state=state)

    @udp.message(F.document)
    async def u_document_msg(message: types.Message, state: FSMContext):
        if message.from_user.id == admin_id and await state.get_state() == St.style_import:
            await u_admin_input(message, state)
            return
        await process_msg(message, state=state)

    async def handle_voice_message(message: types.Message, state: FSMContext = None, bc_id=None):
        file_id = message.voice.file_id if message.voice else message.audio.file_id
        ext = "ogg" if message.voice else "mp3"
        mime_type = "audio/ogg" if message.voice else (message.audio.mime_type or "audio/mpeg")
        local_path = TMP_DIR / f"{message.chat.id}_{message.message_id}.{ext}"
        wait_msg = None
        try:
            if not bc_id:
                wait_msg = await message.answer("Ovozli xabar tushunilmoqda...")
            file = await ubot.get_file(file_id)
            await ubot.download_file(file.file_path, destination=local_path)
            prompt = (
                "Foydalanuvchi ovozli xabar yubordi. Audio ichidagi nutqni diqqat bilan eshitib tushun. "
                "Agar til o'zbekcha, ruscha yoki aralash bo'lsa ham mazmunini aniqlab ol. "
                "Transkriptni alohida yozma. Faqat shu ovozli xabarga mos qisqa javob yoz. "
                "Javob Ozodbek yozgandek tabiiy, qisqa va insoniy bo'lsin."
            )
            await process_msg(
                message,
                state=state,
                bc_id=bc_id,
                audio_path=local_path,
                audio_mime=mime_type,
                text_override=prompt,
            )
            if wait_msg:
                try:
                    await wait_msg.delete()
                except Exception:
                    pass
        except Exception as exc:
            print(f"Ovozli xabar xatosi: {exc}")
            if not bc_id:
                await message.answer(bot_settings(token).get("ai_fallback") or DEFAULT_FALLBACK)
            try:
                await ubot.send_message(
                    admin_id,
                    f"<b>Ovozli xabar xatosi</b>\n<code>{html.escape(str(exc)[:700])}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        finally:
            try:
                local_path.unlink(missing_ok=True)
            except Exception:
                pass

    @udp.message(F.voice | F.audio)
    async def u_voice_msg(message: types.Message, state: FSMContext):
        if message.from_user.id == admin_id and await state.get_state():
            await message.answer("Admin sozlama rejimida ovozli xabar qabul qilinmaydi.", reply_markup=back_kb())
            return
        await handle_voice_message(message, state=state)

    @udp.business_message()
    async def u_business(message: types.Message):
        user = message.from_user
        bc_id = message.business_connection_id
        if not user or not bc_id:
            return
        if user.id == admin_id:
            text = message.text or message.caption or ""
            if text:
                sample_count = add_style_sample(token, text)
                await maybe_rebuild_style_profile(sample_count)
            return
        if message.voice or message.audio:
            await handle_voice_message(message, bc_id=bc_id)
            return
        await process_msg(message, bc_id=bc_id)

    @udp.business_connection()
    async def u_bc(bc: types.BusinessConnection):
        settings = bot_settings(token)
        settings["business_connection_id"] = bc.id if bc.is_enabled else ""
        rights = getattr(bc, "rights", None)
        settings["can_manage_stories"] = bool(getattr(rights, "can_manage_stories", False)) if bc.is_enabled else False
        bs(token, "settings", settings)
        try:
            status = "ulandi" if bc.is_enabled else "uzildi"
            story_status = "bor" if settings.get("can_manage_stories") else "yo'q"
            await ubot.send_message(
                admin_id,
                f"<b>Business</b>\n"
                f"{html.escape(bc.user.full_name)} | <code>{bc.user.id}</code>\n"
                f"Holat: {status}\n"
                f"Story ruxsati: {story_status}",
                parse_mode="HTML",
            )
        except Exception:
            pass

    async def u_admin_input(message: types.Message, state: FSMContext):
        cur = await state.get_state()
        data = await state.get_data()

        if cur == St.kw:
            await state.update_data(kw=message.text.strip())
            await state.set_state(St.rtext)
            await message.answer(
                f"Kalit so'z: <b>{html.escape(message.text.strip())}</b>\n\nJavob matnini yozing:",
                parse_mode="HTML",
                reply_markup=back_kb(),
            )
        elif cur == St.rtext:
            await state.update_data(rtext=message.text)
            await state.set_state(St.rmedia)
            await message.answer("Rasm yoki video qo'shasizmi?", reply_markup=skip_kb())
        elif cur == St.rmedia:
            kw = data.get("kw")
            rt = data.get("rtext", "")
            replies = bl(token, "replies")
            if message.photo:
                replies[kw] = {"text": rt, "media": message.photo[-1].file_id, "media_type": "photo"}
            elif message.video:
                replies[kw] = {"text": rt, "media": message.video.file_id, "media_type": "video"}
            else:
                replies[kw] = {"text": rt, "media": None, "media_type": None}
            bs(token, "replies", replies)
            await state.set_state(St.rbuttons)
            await message.answer(
                "Tugmalar ixtiyoriy.\n\n"
                "<code>Matn | https://url.com</code> - havola\n"
                "<code>Matn</code> - oddiy tugma\n\n"
                "Har bir tugmani yangi qatorda yozing.",
                parse_mode="HTML",
                reply_markup=skip_kb(),
            )
        elif cur == St.rbuttons:
            kw = data.get("kw")
            replies = bl(token, "replies")
            if kw in replies:
                replies[kw]["buttons"] = message.text
                bs(token, "replies", replies)
            await state.clear()
            await message.answer(f"<b>{html.escape(kw)}</b> avto javob sifatida saqlandi.", parse_mode="HTML", reply_markup=main_kb())
        elif cur == St.bc:
            users = bl(token, "users")
            ok = fail = 0
            for item in users.values():
                if item.get("blocked"):
                    continue
                try:
                    await ubot.send_message(item["user_id"], message.text, parse_mode="HTML")
                    ok += 1
                except Exception:
                    fail += 1
                await asyncio.sleep(0.05)
            await state.clear()
            await message.answer(f"Broadcast yakunlandi.\nYuborildi: <b>{ok}</b>\nXato: <b>{fail}</b>", parse_mode="HTML", reply_markup=main_kb())
        elif cur == St.welcome:
            settings = bot_settings(token)
            settings["welcome"] = message.text
            bs(token, "settings", settings)
            await state.clear()
            await message.answer("Xush kelibsiz matni yangilandi.", reply_markup=main_kb())
        elif cur == St.blk:
            try:
                uid = str(int(message.text.strip()))
                users = bl(token, "users")
                if uid in users:
                    users[uid]["blocked"] = True
                    bs(token, "users", users)
                    await message.answer(f"{uid} bloklandi.", reply_markup=main_kb())
                else:
                    await message.answer("Foydalanuvchi topilmadi.", reply_markup=main_kb())
            except Exception:
                await message.answer("ID noto'g'ri.", reply_markup=main_kb())
            await state.clear()
        elif cur == St.unblk:
            try:
                uid = str(int(message.text.strip()))
                users = bl(token, "users")
                if uid in users:
                    users[uid]["blocked"] = False
                    bs(token, "users", users)
                    await message.answer(f"{uid} blokdan chiqarildi.", reply_markup=main_kb())
                else:
                    await message.answer("Foydalanuvchi topilmadi.", reply_markup=main_kb())
            except Exception:
                await message.answer("ID noto'g'ri.", reply_markup=main_kb())
            await state.clear()
        elif cur == St.gemini_key:
            settings = bot_settings(token)
            value = message.text.strip()
            if value.lower() in {"off", "ochirish", "delete"}:
                value = ""
            settings["gemini_api_key"] = value
            settings["gemini_active_key"] = 0
            bs(token, "settings", settings)
            await state.clear()
            await message.answer(f"Gemini API key saqlandi: <b>{hide_key(value)}</b>", parse_mode="HTML", reply_markup=ai_kb())
        elif cur == St.backup_keys:
            settings = bot_settings(token)
            text_value = message.text.strip()
            if text_value.lower() in {"off", "ochirish", "delete", "clear", "tozalash"}:
                keys = []
            else:
                keys = []
                for line in text_value.replace(",", "\n").splitlines():
                    key = line.strip()
                    if key and key != settings.get("gemini_api_key") and key not in keys:
                        keys.append(key)
            settings["gemini_backup_keys"] = keys
            settings["gemini_active_key"] = 0
            bs(token, "settings", settings)
            await state.clear()
            await message.answer(f"Zaxira keylar saqlandi: <b>{len(keys)}</b> ta", parse_mode="HTML", reply_markup=ai_kb())
        elif cur == St.ai_prompt:
            settings = bot_settings(token)
            settings["ai_prompt"] = message.text.strip()
            bs(token, "settings", settings)
            await state.clear()
            await message.answer("AI prompt yangilandi.", reply_markup=ai_kb())
        elif cur == St.ai_fallback:
            settings = bot_settings(token)
            settings["ai_fallback"] = message.text.strip()
            bs(token, "settings", settings)
            await state.clear()
            await message.answer("Fallback javob yangilandi.", reply_markup=ai_kb())
        elif cur == St.ai_test:
            settings = bot_settings(token)
            await message.answer("AI test qilinmoqda...")
            fake_history = ""
            style_text = get_style_text(token) if settings.get("style_enabled") else ""
            try:
                answer = await asyncio.to_thread(
                    gemini_text_sync,
                    get_active_gemini_key(settings),
                    settings.get("ai_prompt") or DEFAULT_PROMPT,
                    message.text,
                    fake_history,
                    style_text,
                    None,
                    None,
                )
                await message.answer(f"<b>AI javobi:</b>\n\n{html.escape(answer)}", parse_mode="HTML", reply_markup=ai_kb())
            except Exception as exc:
                await message.answer(f"AI test xatosi: <code>{html.escape(str(exc)[:300])}</code>", parse_mode="HTML", reply_markup=ai_kb())
            await state.clear()
        elif cur == St.style_sample:
            parts = [line.strip() for line in message.text.splitlines() if line.strip()]
            if not parts:
                await message.answer("Namuna bo'sh bo'lmasin.", reply_markup=back_kb())
                return
            sample_count = 0
            for part in parts:
                sample_count = add_style_sample(token, part) or sample_count
            await rebuild_style_profile(silent=False)
            await state.clear()
            await message.answer(
                f"Uslub uchun <b>{len(parts)}</b> ta namuna saqlandi.\n"
                f"Jami namuna: <b>{sample_count}</b>\n"
                "Uslub profili yangilandi.",
                parse_mode="HTML",
                reply_markup=ai_kb(),
            )
        elif cur == St.style_import:
            if not message.document:
                await message.answer("Telegram export faylini document qilib yuboring: .json yoki .html", reply_markup=back_kb())
                return
            file_name = message.document.file_name or "telegram_export"
            suffix = Path(file_name).suffix.lower()
            if suffix not in {".json", ".html", ".htm"}:
                await message.answer("Faqat .json yoki .html fayl qabul qilinadi.", reply_markup=back_kb())
                return
            dest = import_dir(token) / f"{int(datetime.now().timestamp())}_{file_name}"
            wait_msg = await message.answer("Chat export yuklanmoqda va analiz qilinmoqda...")
            try:
                file = await ubot.get_file(message.document.file_id)
                await ubot.download_file(file.file_path, destination=dest)
                owner_names = {
                    (message.from_user.full_name or "").lower(),
                    (message.from_user.username or "").lower(),
                    "ozodbek",
                }
                owner_names = {name for name in owner_names if name}
                imported = await asyncio.to_thread(parse_chat_export, dest, owner_names)
                if not imported:
                    await wait_msg.edit_text(
                        "Fayldan Ozodbek yozgan matnli xabar topilmadi.\n"
                        "Agar .html export bo'lsa, kerakli chat export qilinganini tekshiring.",
                        reply_markup=ai_kb(),
                    )
                    await state.clear()
                    return
                sample_count = 0
                for text_item in imported[:2000]:
                    sample_count = add_style_sample(token, text_item) or sample_count
                ok = await rebuild_style_profile(silent=False)
                imports = bl(token, "imports")
                imports[str(int(datetime.now().timestamp()))] = {
                    "file": str(dest),
                    "messages": len(imported),
                    "used": min(len(imported), 2000),
                    "profile_updated": ok,
                    "created": datetime.now().isoformat(),
                }
                bs(token, "imports", imports)
                await wait_msg.edit_text(
                    f"Import tayyor.\n\n"
                    f"Topilgan xabarlar: <b>{len(imported)}</b>\n"
                    f"Uslubga qo'shildi: <b>{min(len(imported), 2000)}</b>\n"
                    f"Jami namuna: <b>{sample_count}</b>\n"
                    f"Profil: <b>{'yangilandi' if ok else 'keyinroq analiz qiling'}</b>",
                    parse_mode="HTML",
                    reply_markup=ai_kb(),
                )
            except Exception as exc:
                await wait_msg.edit_text(
                    f"Import xatosi:\n<code>{html.escape(str(exc)[:700])}</code>",
                    parse_mode="HTML",
                    reply_markup=ai_kb(),
                )
            finally:
                await state.clear()
        elif cur == St.story_media:
            settings = bot_settings(token)
            business_connection_id = settings.get("business_connection_id", "")
            if not business_connection_id or not settings.get("can_manage_stories"):
                await state.clear()
                await message.answer(
                    "Story qo'yish uchun bot Telegram Business accountga ulangan va "
                    "<b>Manage Stories</b> ruxsati berilgan bo'lishi kerak.",
                    parse_mode="HTML",
                    reply_markup=story_kb(),
                )
                return

            if not (message.photo or message.video):
                await message.answer("Story uchun rasm yoki video yuboring.", reply_markup=back_kb())
                return

            media_type = "photo" if message.photo else "video"
            file_id = message.photo[-1].file_id if message.photo else message.video.file_id
            ext = "jpg" if media_type == "photo" else "mp4"
            local_path = TMP_DIR / f"story_{message.chat.id}_{message.message_id}.{ext}"
            wait_msg = await message.answer("Story yuklanmoqda...")
            try:
                file = await ubot.get_file(file_id)
                await ubot.download_file(file.file_path, destination=local_path)
                caption = message.caption or ""
                result = await asyncio.to_thread(
                    post_story_sync,
                    token,
                    business_connection_id,
                    local_path,
                    media_type,
                    caption,
                )
                stories = bl(token, "posted_stories")
                story_id = str(result.get("id") or result.get("story_id") or int(datetime.now().timestamp()))
                stories[story_id] = {
                    "type": media_type,
                    "caption": caption,
                    "created": datetime.now().isoformat(),
                }
                bs(token, "posted_stories", stories)
                await wait_msg.edit_text("Story qo'yildi.", reply_markup=story_kb())
            except Exception as exc:
                await wait_msg.edit_text(
                    "Story qo'yilmadi.\n\n"
                    f"<code>{html.escape(str(exc)[:700])}</code>\n\n"
                    "Eslatma: rasm 1080x1920, video 720x1280 bo'lishi va botda Manage Stories ruxsati bo'lishi kerak.",
                    parse_mode="HTML",
                    reply_markup=story_kb(),
                )
            finally:
                await state.clear()
                try:
                    local_path.unlink(missing_ok=True)
                except Exception:
                    pass

    @udp.callback_query(F.data == "u_back")
    async def u_cb_back(call: types.CallbackQuery, state: FSMContext):
        await state.clear()
        try:
            await call.message.edit_text(admin_text(), reply_markup=main_kb(), parse_mode="HTML")
        except Exception:
            await call.answer()

    @udp.callback_query(F.data == "u_skip")
    async def u_cb_skip(call: types.CallbackQuery, state: FSMContext):
        cur = await state.get_state()
        data = await state.get_data()
        if cur == St.rmedia:
            kw = data.get("kw")
            rt = data.get("rtext", "")
            replies = bl(token, "replies")
            replies[kw] = {"text": rt, "media": None, "media_type": None}
            bs(token, "replies", replies)
            await state.set_state(St.rbuttons)
            await call.message.edit_text(
                "Tugmalar ixtiyoriy.\n<code>Matn | https://url.com</code>",
                parse_mode="HTML",
                reply_markup=skip_kb(),
            )
        elif cur == St.rbuttons:
            kw = data.get("kw")
            await state.clear()
            await call.message.edit_text(f"<b>{html.escape(kw)}</b> saqlandi.", parse_mode="HTML", reply_markup=main_kb())
        else:
            await call.answer()

    @udp.callback_query(F.data == "u_replies")
    async def u_cb_replies(call: types.CallbackQuery):
        replies = bl(token, "replies")
        if not replies:
            text = "<b>Avto javoblar</b>\n\nHozircha avto javob yo'q."
        else:
            text = "<b>Avto javoblar</b>\n\n"
            for key, value in replies.items():
                media = "media" if value.get("media_type") else "matn"
                buttons = ", tugma" if value.get("buttons") else ""
                text += f"<code>{html.escape(key)}</code> - {media}{buttons}\n"
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=replies_kb())

    @udp.callback_query(F.data == "u_add")
    async def u_cb_add(call: types.CallbackQuery, state: FSMContext):
        await state.set_state(St.kw)
        await call.message.edit_text(
            "<b>Kalit so'z yozing</b>\n\nMasalan: narx, yetkazib berish, salom",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )

    @udp.callback_query(F.data == "u_del_list")
    async def u_cb_del_list(call: types.CallbackQuery):
        replies = bl(token, "replies")
        if not replies:
            await call.answer("Avto javob yo'q.", show_alert=True)
            return
        rows = [[InlineKeyboardButton(text=f"O'chirish: {key[:30]}", callback_data=f"udel_{key}")] for key in replies]
        rows.append([InlineKeyboardButton(text="Orqaga", callback_data="u_replies")])
        await call.message.edit_text("Qaysi javob o'chirilsin?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @udp.callback_query(F.data.startswith("udel_"))
    async def u_cb_do_del(call: types.CallbackQuery):
        key = call.data[5:]
        replies = bl(token, "replies")
        if key in replies:
            del replies[key]
            bs(token, "replies", replies)
        await call.answer("O'chirildi.")
        await call.message.edit_text(admin_text(), parse_mode="HTML", reply_markup=main_kb())

    @udp.callback_query(F.data == "u_ai")
    async def u_cb_ai(call: types.CallbackQuery):
        settings = bot_settings(token)
        key_status = hide_key(settings.get("gemini_api_key", ""))
        key_count = len(get_gemini_keys(settings))
        active_key = hide_key(get_active_gemini_key(settings))
        ai_status = "yoqilgan" if settings.get("ai_enabled") else "o'chirilgan"
        voice_status = "yoqilgan" if settings.get("voice_reply") else "o'chirilgan"
        style_status = "yoqilgan" if settings.get("style_enabled") else "o'chirilgan"
        voice_mode = "matn + ovoz" if settings.get("voice_reply_mode") == "text_and_voice" else "faqat ovoz"
        profanity_status = "yoqilgan" if settings.get("profanity_filter") else "o'chirilgan"
        style_count = len(bl(token, "style_samples").get("items", []))
        style_profile = bl(token, "style_profile")
        profile_status = "bor" if style_profile.get("text") else "hali yo'q"
        quota_error = settings.get("gemini_quota_error") or {}
        quota_text = ""
        if quota_error:
            quota_text = f"\nOxirgi quota xatosi: <b>{quota_error.get('time', '')[:19]}</b>"
        text = (
            "<b>AI sozlamalar</b>\n\n"
            f"Gemini API key: <b>{html.escape(key_status)}</b>\n"
            f"Jami Gemini key: <b>{key_count}</b>\n"
            f"Ishlayotgan key: <b>{html.escape(active_key)}</b>\n"
            f"AI auto-javob: <b>{ai_status}</b>\n"
            f"Ovozli javob: <b>{voice_status}</b>\n"
            f"Voice rejim: <b>{voice_mode}</b>\n"
            f"Ozodbek uslubi: <b>{style_status}</b>\n"
            f"So'kinish filter: <b>{profanity_status}</b>\n"
            f"Uslub namunalari: <b>{style_count}</b>\n\n"
            f"Uslub profili: <b>{profile_status}</b>{quota_text}\n\n"
            "Manual avto javob topilmasa, AI javob beradi. Uslub ON bo'lsa, javob Ozodbek yozgandek chiqadi."
        )
        try:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=ai_kb())
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                await call.answer()
            else:
                raise

    @udp.callback_query(F.data == "u_ai_toggle")
    async def u_cb_ai_toggle(call: types.CallbackQuery):
        settings = bot_settings(token)
        if not get_gemini_keys(settings):
            await call.answer("Avval Gemini API key kiriting.", show_alert=True)
            return
        settings["ai_enabled"] = not settings.get("ai_enabled")
        bs(token, "settings", settings)
        await u_cb_ai(call)

    @udp.callback_query(F.data == "u_voice_toggle")
    async def u_cb_voice_toggle(call: types.CallbackQuery):
        settings = bot_settings(token)
        if not get_gemini_keys(settings):
            await call.answer("Avval Gemini API key kiriting.", show_alert=True)
            return
        settings["voice_reply"] = not settings.get("voice_reply")
        bs(token, "settings", settings)
        await u_cb_ai(call)

    @udp.callback_query(F.data == "u_voice_mode")
    async def u_cb_voice_mode(call: types.CallbackQuery):
        settings = bot_settings(token)
        current = settings.get("voice_reply_mode", "text_and_voice")
        settings["voice_reply_mode"] = "voice_only" if current == "text_and_voice" else "text_and_voice"
        bs(token, "settings", settings)
        await u_cb_ai(call)

    @udp.callback_query(F.data == "u_profanity_toggle")
    async def u_cb_profanity_toggle(call: types.CallbackQuery):
        settings = bot_settings(token)
        settings["profanity_filter"] = not settings.get("profanity_filter")
        bs(token, "settings", settings)
        await u_cb_ai(call)

    @udp.callback_query(F.data == "u_style_toggle")
    async def u_cb_style_toggle(call: types.CallbackQuery):
        settings = bot_settings(token)
        settings["style_enabled"] = not settings.get("style_enabled")
        bs(token, "settings", settings)
        await u_cb_ai(call)

    @udp.callback_query(F.data == "u_style_strength")
    async def u_cb_style_strength(call: types.CallbackQuery):
        settings = bot_settings(token)
        settings["style_strength"] = "strict"
        bs(token, "settings", settings)
        await call.answer("Uslub kuchi STRICT rejimda.")
        await u_cb_ai(call)

    @udp.callback_query(F.data == "u_style_add")
    async def u_cb_style_add(call: types.CallbackQuery, state: FSMContext):
        await state.set_state(St.style_sample)
        await call.message.edit_text(
            "<b>Ozodbek uslubi uchun namunalar yuboring</b>\n\n"
            "Oldin yozgan xabarlaringizni bitta yoki bir nechta qatorda tashlang. "
            "Bot mazmunni emas, yozish uslubini oladi.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )

    @udp.callback_query(F.data == "u_style_import")
    async def u_cb_style_import(call: types.CallbackQuery, state: FSMContext):
        await state.set_state(St.style_import)
        await call.message.edit_text(
            "<b>Chat import yuklash</b>\n\n"
            "Telegram Desktop export faylini document qilib yuboring.\n"
            "Qabul qilinadi: <code>.json</code>, <code>.html</code>\n\n"
            "Fayl bot papkasida ham saqlanadi:\n"
            "<code>data/imports/</code>",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )

    @udp.callback_query(F.data == "u_style_clear")
    async def u_cb_style_clear(call: types.CallbackQuery):
        clear_style_samples(token)
        await call.answer("Uslub namunalari tozalandi.")
        await u_cb_ai(call)

    @udp.callback_query(F.data == "u_style_analyze")
    async def u_cb_style_analyze(call: types.CallbackQuery):
        await call.answer("Uslub tahlil qilinmoqda...")
        ok = await rebuild_style_profile(silent=False)
        if ok:
            await u_cb_ai(call)
        else:
            await call.message.answer(
                "Uslub profilini chiqarish uchun Gemini API key va kamida 3 ta yozish namunasi kerak.",
                reply_markup=ai_kb(),
            )

    @udp.callback_query(F.data == "u_ai_key")
    async def u_cb_ai_key(call: types.CallbackQuery, state: FSMContext):
        await state.set_state(St.gemini_key)
        await call.message.edit_text(
            "<b>Gemini API key yuboring</b>\n\n"
            "O'chirish uchun <code>off</code> yozing.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )

    @udp.callback_query(F.data == "u_backup_keys")
    async def u_cb_backup_keys(call: types.CallbackQuery, state: FSMContext):
        settings = bot_settings(token)
        keys = settings.get("gemini_backup_keys") or []
        preview = "\n".join(f"{idx + 1}. {hide_key(key)}" for idx, key in enumerate(keys)) or "yo'q"
        await state.set_state(St.backup_keys)
        await call.message.edit_text(
            "<b>Zaxira Gemini API keylar</b>\n\n"
            f"Hozir: \n{html.escape(preview)}\n\n"
            "Yangi zaxira keylarni har birini yangi qatorda yuboring.\n"
            "Hammasini tozalash uchun <code>clear</code> yozing.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )

    @udp.callback_query(F.data == "u_ai_prompt")
    async def u_cb_ai_prompt(call: types.CallbackQuery, state: FSMContext):
        settings = bot_settings(token)
        await state.set_state(St.ai_prompt)
        await call.message.edit_text(
            f"<b>Hozirgi prompt:</b>\n{html.escape(settings.get('ai_prompt', '')[:900])}\n\n"
            "Yangi prompt yozing.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )

    @udp.callback_query(F.data == "u_ai_fallback")
    async def u_cb_ai_fallback(call: types.CallbackQuery, state: FSMContext):
        settings = bot_settings(token)
        await state.set_state(St.ai_fallback)
        await call.message.edit_text(
            f"<b>Hozirgi fallback:</b>\n{html.escape(settings.get('ai_fallback', DEFAULT_FALLBACK))}\n\n"
            "Yangi fallback javob yozing.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )

    @udp.callback_query(F.data == "u_ai_test")
    async def u_cb_ai_test(call: types.CallbackQuery, state: FSMContext):
        settings = bot_settings(token)
        if not get_gemini_keys(settings):
            await call.answer("Avval Gemini API key kiriting.", show_alert=True)
            return
        await state.set_state(St.ai_test)
        await call.message.edit_text("AI test uchun savol yozing.", reply_markup=back_kb())

    @udp.callback_query(F.data == "u_stats")
    async def u_cb_stats(call: types.CallbackQuery):
        stats = bl(token, "stats")
        users = bl(token, "users")
        blocked = sum(1 for item in users.values() if item.get("blocked"))
        replies = bl(token, "replies")
        settings = bot_settings(token)
        text = (
            "<b>Statistika</b>\n\n"
            f"Foydalanuvchilar: <b>{len(users)}</b>\n"
            f"Bloklangan: <b>{blocked}</b>\n"
            f"Bugungi xabarlar: <b>{stats.get('today', 0)}</b>\n"
            f"Jami xabarlar: <b>{stats.get('total', 0)}</b>\n\n"
            f"Manual javoblar: <b>{stats.get('manual_replies', 0)}</b>\n"
            f"AI javoblar: <b>{stats.get('ai_replies', 0)}</b>\n"
            f"Avto javob shablonlari: <b>{len(replies)}</b>\n"
            f"AI holati: <b>{'ON' if settings.get('ai_enabled') else 'OFF'}</b>"
        )
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb())

    @udp.callback_query(F.data == "u_users")
    async def u_cb_users(call: types.CallbackQuery):
        users = bl(token, "users")
        if not users:
            await call.message.edit_text("Foydalanuvchi yo'q.", reply_markup=back_kb())
            return
        text = "<b>Oxirgi foydalanuvchilar</b>\n\n"
        for item in list(users.values())[-15:]:
            status = "blok" if item.get("blocked") else "aktiv"
            username = f"@{item['username']}" if item.get("username") else "-"
            age = f" | yosh: {item.get('age')}" if item.get("age") else ""
            text += f"<b>{html.escape(item.get('full_name', ''))}</b> ({status})\n{username} | <code>{item.get('user_id')}</code>{age}\n\n"
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb())

    @udp.callback_query(F.data == "u_broadcast")
    async def u_cb_bc(call: types.CallbackQuery, state: FSMContext):
        await state.set_state(St.bc)
        await call.message.edit_text("Broadcast uchun matn yozing.", reply_markup=back_kb())

    @udp.callback_query(F.data == "u_bot_settings")
    async def u_cb_bot_settings(call: types.CallbackQuery):
        await call.message.edit_text("<b>Bot sozlamalari</b>\n\nUmumiy boshqaruv.", parse_mode="HTML", reply_markup=bot_settings_kb())

    @udp.callback_query(F.data == "u_story")
    async def u_cb_story(call: types.CallbackQuery):
        settings = bot_settings(token)
        connected = "ulangan" if settings.get("business_connection_id") else "ulanmagan"
        rights = "bor" if settings.get("can_manage_stories") else "yo'q"
        text = (
            "<b>Telegram Story</b>\n\n"
            f"Business ulanish: <b>{connected}</b>\n"
            f"Manage Stories ruxsati: <b>{rights}</b>\n\n"
            "Story qo'yish uchun Telegram Business sozlamalarida botga "
            "<b>Manage Stories</b> huquqini bering. Keyin rasm yoki video yuboring.\n\n"
            "Talablar: rasm 1080x1920, video 720x1280."
        )
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=story_kb())

    @udp.callback_query(F.data == "u_story_add")
    async def u_cb_story_add(call: types.CallbackQuery, state: FSMContext):
        await state.set_state(St.story_media)
        await call.message.edit_text(
            "Story uchun rasm yoki video yuboring.\n\n"
            "Caption kerak bo'lsa, rasm/video captioniga yozing.",
            reply_markup=back_kb(),
        )

    @udp.callback_query(F.data == "u_welcome")
    async def u_cb_welcome(call: types.CallbackQuery, state: FSMContext):
        settings = bot_settings(token)
        await state.set_state(St.welcome)
        await call.message.edit_text(
            f"<b>Hozirgi matn:</b>\n{html.escape(settings.get('welcome', '')[:900])}\n\n"
            "<code>{name}</code> - foydalanuvchi ismi.\nYangi matn yozing.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )

    @udp.callback_query(F.data == "u_block")
    async def u_cb_block(call: types.CallbackQuery, state: FSMContext):
        await state.set_state(St.blk)
        await call.message.edit_text("Bloklash uchun foydalanuvchi ID yozing.", reply_markup=back_kb())

    @udp.callback_query(F.data == "u_unblock")
    async def u_cb_unblock(call: types.CallbackQuery, state: FSMContext):
        await state.set_state(St.unblk)
        await call.message.edit_text("Blokdan chiqarish uchun foydalanuvchi ID yozing.", reply_markup=back_kb())

    return ubot, udp


class MasterSt(StatesGroup):
    token = State()


@master_dp.message(Command("start"))
async def master_start(message: types.Message, state: FSMContext):
    await state.clear()
    accounts = get_accounts()
    uid = str(message.from_user.id)
    if uid in accounts:
        acc = accounts[uid]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Qayta ishga tushirish", callback_data="restart_bot")],
            [InlineKeyboardButton(text="Botni o'chirish", callback_data="delete_bot")],
        ])
        await message.answer(
            f"<b>Sizning botingiz mavjud</b>\n\n"
            f"Bot: @{html.escape(acc.get('username', '?'))}\n"
            f"Yaratilgan: {acc.get('created', '?')[:10]}\n\n"
            f"Botga o'ting: @{html.escape(acc.get('username', '?'))}",
            reply_markup=kb,
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"Assalomu alaykum, <b>{html.escape(message.from_user.full_name)}</b>!\n\n"
            "<b>O'z Telegram AI botingizni yarating.</b>\n\n"
            "1. @BotFather ga kiring\n"
            "2. /newbot yuboring\n"
            "3. Bot tokenini shu yerga yuboring",
            parse_mode="HTML",
        )
        await state.set_state(MasterSt.token)


@master_dp.callback_query(F.data == "restart_bot")
async def master_restart(call: types.CallbackQuery):
    uid = str(call.from_user.id)
    accounts = get_accounts()
    if uid not in accounts:
        await call.answer("Bot topilmadi.", show_alert=True)
        return
    token = accounts[uid]["token"]
    await stop_user_bot(token)
    await start_user_bot(token, call.from_user.id)
    await call.answer("Bot qayta ishga tushirildi.")


@master_dp.callback_query(F.data == "delete_bot")
async def master_delete(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Ha", callback_data="confirm_delete"),
        InlineKeyboardButton(text="Yo'q", callback_data="cancel_delete"),
    ]])
    await call.message.edit_text("Haqiqatan ham botni o'chirmoqchimisiz?", reply_markup=kb)


@master_dp.callback_query(F.data == "confirm_delete")
async def master_confirm_delete(call: types.CallbackQuery):
    uid = str(call.from_user.id)
    accounts = get_accounts()
    if uid in accounts:
        token = accounts[uid]["token"]
        await stop_user_bot(token)
        del accounts[uid]
        save_accounts(accounts)
    await call.message.edit_text("Bot o'chirildi. Yangi bot yaratish uchun /start bosing.")


@master_dp.callback_query(F.data == "cancel_delete")
async def master_cancel_delete(call: types.CallbackQuery):
    await call.message.edit_text("Bekor qilindi. /start")


@master_dp.message(MasterSt.token)
async def master_get_token(message: types.Message, state: FSMContext):
    token = message.text.strip()
    if ":" not in token or len(token) < 30:
        await message.answer("Token noto'g'ri.\nFormat: <code>123456:AAH...</code>", parse_mode="HTML")
        return
    await message.answer("Token tekshirilmoqda...")
    try:
        tb = Bot(token=token)
        info = await tb.get_me()
        await tb.session.close()
    except Exception as exc:
        await message.answer(f"Token noto'g'ri.\n{html.escape(str(exc)[:150])}", parse_mode="HTML")
        return

    accounts = get_accounts()
    for uid, acc in accounts.items():
        if acc.get("token") == token and uid != str(message.from_user.id):
            await message.answer("Bu token allaqachon ishlatilgan.")
            return

    uid = str(message.from_user.id)
    accounts[uid] = {
        "token": token,
        "username": info.username,
        "admin_id": message.from_user.id,
        "created": datetime.now().isoformat(),
    }
    save_accounts(accounts)
    bot_settings(token)
    await start_user_bot(token, message.from_user.id)
    await state.clear()
    await message.answer(
        f"<b>Bot yaratildi</b>\n\n"
        f"Bot: @{html.escape(info.username)}\n\n"
        f"Endi @{html.escape(info.username)} ga o'tib /start bosing.\n"
        "Business ulash: @BotFather -> Bot Settings -> Business Bot -> Enable",
        parse_mode="HTML",
    )


async def start_user_bot(token: str, admin_id: int):
    if token in running_bots:
        await stop_user_bot(token)
    ubot, udp = create_user_bot(token, admin_id)

    async def run():
        try:
            await udp.start_polling(
                ubot,
                allowed_updates=[
                    "message",
                    "business_message",
                    "business_connection",
                    "callback_query",
                ],
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"Bot xatosi ({token[:15]}...): {exc}")

    task = asyncio.create_task(run())
    running_bots[token] = {"bot": ubot, "dp": udp, "task": task}
    print(f"Bot ishga tushdi: {token[:15]}...")


async def stop_user_bot(token: str):
    if token not in running_bots:
        return
    info = running_bots[token]
    info["task"].cancel()
    try:
        await info["bot"].session.close()
    except Exception:
        pass
    del running_bots[token]


async def main():
    accounts = get_accounts()
    for acc in accounts.values():
        try:
            bot_settings(acc["token"])
            await start_user_bot(acc["token"], acc["admin_id"])
        except Exception as exc:
            print(f"Bot yuklanmadi: {exc}")
    print(f"Master bot ishga tushdi. Yuklangan botlar: {len(accounts)}")
    await master_dp.start_polling(master_bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
