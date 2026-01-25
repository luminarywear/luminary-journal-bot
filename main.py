import asyncio
import os
import random
import hashlib
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
from aiosqlite import connect as aconnect
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

DB_PATH = "/tmp/luminary.db"

# === DATABASE ===
async def init_db():
    async with aconnect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                soft_name TEXT,
                agreed BOOLEAN DEFAULT 0,
                subscribed BOOLEAN DEFAULT 0,
                subscription_until TEXT,
                last_entry TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                entry_type TEXT DEFAULT 'free'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sent_affirmations (
                user_id INTEGER,
                affirmation_hash TEXT,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

# === AFFIRMATIONS ===
OPENINGS = [
    "сегодня тебе не нужно",
    "ты имеешь право на",
    "иногда достаточно просто",
    "в этот день можно позволить себе",
    "твоя внутренняя",
    "даже если кажется иначе —",
    "ты не обязан(а)",
    "пусть сегодня будет",
    "всё, что нужно сейчас —",
    "ты можешь отпустить"
]

CORES = [
    "ничего доказывать",
    "медленный день",
    "подышать",
    "быть мягким(ой)",
    "тишина — самый честный ответ",
    "усталость — часть пути",
    "свет уже есть в тебе",
    "просто быть",
    "отпустить всё",
    "довериться моменту",
    "ничего не менять",
    "остаться с собой",
    "чувствовать землю под ногами",
    "ждать без цели",
    "слушать своё дыхание",
    "не знать ответа"
]

ENDINGS = [
    ". Просто будь. 🌿",
    ". Это уже достаточно. ✨",
    ". Отдохни. 🌙",
    ". Ты здесь — и этого хватит. 🤍",
    ". Доверься себе. 💚",
    ". Пусть будет так. 🌱",
    ". Ты цел(а). 💛",
    ". Всё в порядке. 🌸",
    ". Ты растёшь. 🌷",
    ". Дыши. 💙",
    ". Ты светишь. ⚡️",
    ". Всё проходит. 🍀",
    ". Ты любим(а). 💘",
    ". Сердце знает. ❤️",
    ". Путь мягкий. ☘️",
    ". Время твоё. 🌾"
]

def generate_affirmation():
    opening = random.choice(OPENINGS)
    core = random.choice(CORES)
    ending = random.choice(ENDINGS)
    text = f"{opening} {core}{ending}"
    hash_ = hashlib.sha256(text.encode()).hexdigest()[:16]
    return text, hash_

async def get_unique_affirmation(user_id):
    since = datetime.utcnow() - timedelta(days=180)
    async with aconnect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT affirmation_hash FROM sent_affirmations WHERE user_id = ? AND sent_at > ?",
            (user_id, since.isoformat())
        )
        rows = await cursor.fetchall()
        used_hashes = {row[0] for row in rows}
        
        for _ in range(15):
            text, hash_ = generate_affirmation()
            if hash_ not in used_hashes:
                await db.execute(
                    "INSERT INTO sent_affirmations (user_id, affirmation_hash, sent_at) VALUES (?, ?, ?)",
                    (user_id, hash_, datetime.utcnow().isoformat())
                )
                await db.commit()
                return text
        
        text, hash_ = generate_affirmation()
        await db.execute(
            "INSERT INTO sent_affirmations (user_id, affirmation_hash, sent_at) VALUES (?, ?, ?)",
            (user_id, hash_, datetime.utcnow().isoformat())
        )
        await db.commit()
        return text

def get_addressing(soft_name):
    return f"{soft_name}, " if soft_name else ""

# === HANDLERS ===
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    async with aconnect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (message.from_user.id, message.from_user.username)
        )
        await db.commit()
    await message.answer(
        "Привет. Это твой дневник — место, где можно быть собой.\n\n"
        "Каждое утро я буду присылать тебе тихую аффирмацию. "
        "А в любое время ты можешь написать сюда всё, что живёт внутри.\n\n"
        "Перед началом — пожалуйста, ознакомься с нашим "
        "<a href='https://luminarywear.ru/journal/terms.html'>пользовательским соглашением</a>.\n\n"
        "Если ты согласен(а) — напиши «Да».",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

@dp.message_handler(lambda m: m.text and m.text.lower() in {"да", "yes", "согласен"})
async def handle_agreement(message: types.Message):
    async with aconnect(DB_PATH) as db:
        await db.execute("UPDATE users SET agreed = 1 WHERE user_id = ?", (message.from_user.id,))
        await db.commit()
    await message.answer(
        "Спасибо. 💛\n\n"
        "А теперь — как мне к тебе обращаться?\n"
        "Напиши имя, в котором ты чувствуешь себя собой.\n\n"
        "Например: <b>Аня, Леша, Марина</b>…\n"
        "Или просто скажи «без имени» — и я буду писать так, будто мы с тобой наедине, но без слов.",
        parse_mode="HTML"
    )

@dp.message_handler(commands=["terms"])
async def show_terms(message: types.Message):
    await message.answer(
        "<b>Пользовательское соглашение</b>\n\n"
        "• Возраст: от 14 лет (без согласия родителей).\n"
        "• Это твоё пространство — записи принадлежат только тебе.\n"
        "• Мы не удаляем данные автоматически.\n"
        "• Приватность: никаких email, телефона, геолокации.\n"
        "• Подписка: 7 дней бесплатно, потом — по желанию.\n\n"
        "Полная версия: https://luminarywear.ru/journal/terms.html",
        parse_mode="HTML"
    )

@dp.message_handler(commands=["privacy"])
async def show_privacy(message: types.Message):
    await message.answer(
        "<b>Политика конфиденциальности</b>\n\n"
        "• Собираем: Telegram ID, записи, мягкое имя (если дал).\n"
        "• Не делимся, не продаём, не анализируем.\n"
        "• Хочешь удалить всё? Напиши /delete_all.\n\n"
        "Полная версия: https://luminarywear.ru/journal/privacy.html",
        parse_mode="HTML"
    )

@dp.message_handler(commands=["delete_all"])
async def delete_all_start(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton("Да, удалить всё"))
    await message.answer(
        "Ты хочешь удалить все свои записи из дневника?\n\n"
        "Это действие нельзя отменить. Твои слова исчезнут навсегда.\n\n"
        "Если ты уверен(а) — нажми кнопку ниже.",
        reply_markup=kb
    )

@dp.message_handler(lambda m: m.text == "Да, удалить всё")
async def delete_all_confirm(message: types.Message):
    async with aconnect(DB_PATH) as db:
        await db.execute("DELETE FROM entries WHERE user_id = ?", (message.from_user.id,))
        await db.execute(
            "UPDATE users SET soft_name = NULL, last_entry = NULL WHERE user_id = ?",
            (message.from_user.id,)
        )
        await db.commit()
    await message.answer(
        "Все твои записи удалены. 💫\n\n"
        "Если захочешь начать заново — просто напиши сюда.\n"
        "Дневник всегда открыт.",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message_handler(commands=["subscribe"])
async def subscribe(message: types.Message):
    prices = [
        LabeledPrice(label="1 месяц", amount=9900),
        LabeledPrice(label="1 год", amount=89000),
    ]
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Luminary Journal — подписка",
        description="Доступ к дневнику на месяц или год. Все записи сохраняются навсегда.",
        payload="journal_sub",
        provider_token="",
        currency="XTR",
        prices=prices,
        start_parameter="journal_sub",
    )

@dp.pre_checkout_query_handler()
async def pre_checkout(query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT)
async def payment_success(message: types.Message):
    payment = message.successful_payment
    user_id = message.from_user.id
    days = 365 if payment.total_amount == 89000 else 30
    until = datetime.utcnow() + timedelta(days=days)
    async with aconnect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET subscribed = 1, subscription_until = ? WHERE user_id = ?",
            (until.isoformat(), user_id)
        )
        await db.commit()
    await message.answer("Спасибо за доверие. 💛\n\nДневник — твой.")

@dp.message_handler(lambda m: m.text and not m.text.startswith('/'))
async def handle_message(message: types.Message):
    text = message.text.strip()
    if text.lower() in ["без имени", "не хочу", "нет", "никак"]:
        soft_name = None
        async with aconnect(DB_PATH) as db:
            await db.execute("UPDATE users SET soft_name = ? WHERE user_id = ?", (soft_name, message.from_user.id))
            await db.commit()
        prefix = get_addressing(soft_name)
        await message.answer(
            f"{prefix}дневник открыт. 🌿\n\n"
            "Пиши сюда всё, что живёт внутри — в любое время.\n"
            "А завтра утром тебя ждёт первая аффирмация."
        )
    elif text in ["/terms", "/privacy", "/subscribe", "/delete_all"]:
        return
    else:
        # Сохраняем запись
        async with aconnect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO entries (user_id, text) VALUES (?, ?)",
                (message.from_user.id, text)
            )
            await db.execute(
                "UPDATE users SET last_entry = ? WHERE user_id = ?",
                (datetime.utcnow().isoformat(), message.from_user.id)
            )
            await db.commit()
        await message.answer("Записано. ✨")

# === SCHEDULER ===
async def send_daily_affirmation():
    async with aconnect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE agreed = 1")
        users = await cursor.fetchall()
    for (user_id,) in users:
        try:
            text = await get_unique_affirmation(user_id)
            await bot.send_message(user_id, text)
        except Exception:
            pass

def setup_scheduler():
    scheduler = AsyncIOScheduler(timezone=os.getenv("TIMEZONE", "UTC"))
    scheduler.add_job(send_daily_affirmation, CronTrigger(hour=8, minute=0))
    scheduler.start()

# === MAIN ===
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    setup_scheduler()
    executor.start_polling(dp, skip_updates=True)