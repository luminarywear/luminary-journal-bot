import asyncio
import os
import random
import hashlib
import threading
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

# === HEALTH CHECK (внешний будильник) ===
from aiohttp import web

async def health_check(request):
    return web.Response(text="OK")

def start_health_server():
    async def run_server():
        app = web.Application()
        app.router.add_get('/health', health_check)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv("PORT", 10000))
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        await asyncio.Event().wait()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_server())

threading.Thread(target=start_health_server, daemon=True).start()

# === ОСНОВНОЙ КОД ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is required")

# === DATABASE ===
import asyncpg

async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                soft_name TEXT,
                agreed BOOLEAN DEFAULT FALSE,
                subscribed BOOLEAN DEFAULT FALSE,
                subscription_until TIMESTAMP,
                trial_until TIMESTAMP,
                last_entry TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                entry_type TEXT DEFAULT 'free'
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_affirmations (
                user_id BIGINT,
                affirmation_hash TEXT,
                sent_at TIMESTAMP DEFAULT NOW()
            )
        """)
    finally:
        await conn.close()

async def execute_query(query, *params):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        if query.strip().upper().startswith("SELECT"):
            return await conn.fetch(query, *params)
        else:
            await conn.execute(query, *params)
    finally:
        await conn.close()

# === ПРОВЕРКА ДОСТУПА ===
async def check_access(user_id: int) -> bool:
    user = await execute_query(
        "SELECT trial_until, subscribed, subscription_until FROM users WHERE user_id = $1",
        user_id
    )
    if not user:
        return False
    
    now = datetime.utcnow()
    trial_active = user[0]["trial_until"] and user[0]["trial_until"] > now
    sub_active = user[0]["subscribed"] and user[0]["subscription_until"] > now
    
    return trial_active or sub_active

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

async def get_unique_affirmation(user_id: int):
    since = datetime.utcnow() - timedelta(days=180)
    rows = await execute_query(
        "SELECT affirmation_hash FROM sent_affirmations WHERE user_id = $1 AND sent_at > $2",
        user_id, since
    )
    used_hashes = {row["affirmation_hash"] for row in rows}
    
    for _ in range(15):
        text, hash_ = generate_affirmation()
        if hash_ not in used_hashes:
            await execute_query(
                "INSERT INTO sent_affirmations (user_id, affirmation_hash, sent_at) VALUES ($1, $2, $3)",
                user_id, hash_, datetime.utcnow()
            )
            return text
    
    text, hash_ = generate_affirmation()
    await execute_query(
        "INSERT INTO sent_affirmations (user_id, affirmation_hash, sent_at) VALUES ($1, $2, $3)",
        user_id, hash_, datetime.utcnow()
    )
    return text

# === HANDLERS ===
router = Router()

def get_addressing(soft_name):
    return f"{soft_name}, " if soft_name else ""

@router.message(F.text == "/start")
async def cmd_start(message: Message):
    now = datetime.utcnow()
    trial_end = now + timedelta(days=32)
    
    await execute_query("""
        INSERT INTO users (user_id, username, trial_until, agreed)
        VALUES ($1, $2, $3, FALSE)
        ON CONFLICT (user_id) DO UPDATE 
        SET username = $2
    """, message.from_user.id, message.from_user.username, trial_end)
    
    await message.answer(
        "Привет. Это твой дневник — место, где можно быть собой.\n\n"
        "У тебя есть <b>32 дня</b>, чтобы попробовать всё бесплатно.\n\n"
        "Если дневник станет тебе дорог — после пробного периода "
        "подписка стоит <b>120 ₽/мес</b>.\n\n"
        "Перед началом — ознакомься с нашим "
        "<a href='https://luminarywear.ru/journal/terms.html'>пользовательским соглашением</a>.\n\n"
        "Если ты согласен(а) — напиши «Да».",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

@router.message(F.text.lower().in_({"да", "yes", "согласен"}))
async def handle_agreement(message: Message):
    if not await check_access(message.from_user.id):
        await message.answer("Пробный период завершён. Чтобы продолжить, оформи подписку.")
        return
        
    await execute_query("UPDATE users SET agreed = TRUE WHERE user_id = $1", message.from_user.id)
    await message.answer(
        "Спасибо. 💛\n\n"
        "А теперь — как мне к тебе обращаться?\n"
        "Напиши имя, в котором ты чувствуешь себя собой.\n\n"
        "Например: <b>Аня, Леша, Марина</b>…\n"
        "Или просто скажи «без имени» — и я буду писать так, будто мы с тобой наедине, но без слов.",
        parse_mode="HTML"
    )

@router.message(F.text == "/terms")
async def show_terms(message: Message):
    await message.answer(
        "<b>Пользовательское соглашение</b>\n\n"
        "• Возраст: от 14 лет (без согласия родителей).\n"
        "• Это твоё пространство — записи принадлежат только тебе.\n"
        "• Мы не удаляем данные автоматически.\n"
        "• Приватность: никаких email, телефона, геолокации.\n"
        "• Подписка: 32 дня бесплатно, потом — 120 ₽/мес.\n\n"
        "Полная версия: https://luminarywear.ru/journal/terms.html",
        parse_mode="HTML"
    )

@router.message(F.text == "/privacy")
async def show_privacy(message: Message):
    await message.answer(
        "<b>Политика конфиденциальности</b>\n\n"
        "• Собираем: Telegram ID, записи, мягкое имя (если дал).\n"
        "• Не делимся, не продаём, не анализируем.\n"
        "• Хочешь удалить всё? Напиши /delete_all.\n\n"
        "Полная версия: https://luminarywear.ru/journal/privacy.html",
        parse_mode="HTML"
    )

@router.message(F.text == "/delete_all")
async def delete_all_start(message: Message):
    if not await check_access(message.from_user.id):
        await message.answer("Пробный период завершён. Чтобы продолжить, оформи подписку.")
        return
        
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Да, удалить всё")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(
        "Ты хочешь удалить все свои записи из дневника?\n\n"
        "Это действие нельзя отменить. Твои слова исчезнут навсегда.\n\n"
        "Если ты уверен(а) — нажми кнопку ниже.",
        reply_markup=kb
    )

@router.message(F.text == "Да, удалить всё")
async def delete_all_confirm(message: Message):
    if not await check_access(message.from_user.id):
        await message.answer("Пробный период завершён. Чтобы продолжить, оформи подписку.")
        return
        
    await execute_query("DELETE FROM entries WHERE user_id = $1", message.from_user.id)
    await execute_query(
        "UPDATE users SET soft_name = NULL, last_entry = NULL WHERE user_id = $1",
        message.from_user.id
    )
    await message.answer(
        "Все твои записи удалены. 💫\n\n"
        "Если захочешь начать заново — просто напиши сюда.\n"
        "Дневник всегда открыт.",
        reply_markup=None
    )

@router.message(F.text & ~F.text.startswith("/"))
async def handle_message(message: Message):
    if not await check_access(message.from_user.id):
        await message.answer(
            "Пробный период завершён.\n\n"
            "Чтобы продолжить пользоваться дневником, оформи подписку:\n"
            "• <b>120 ₽</b> — на месяц\n\n"
            "👉 <a href='https://tinkoff.ru/qr/ВАША_ССЫЛКА'>Оплатить</a>\n\n"
            "После оплаты напиши сюда своё имя — и я восстановлю доступ.",
            parse_mode="HTML"
        )
        return

    text = message.text.strip()
    if text.lower() in ["без имени", "не хочу", "нет", "никак"]:
        soft_name = None
        await execute_query("UPDATE users SET soft_name = $1 WHERE user_id = $2", soft_name, message.from_user.id)
        prefix = get_addressing(soft_name)
        await message.answer(
            f"{prefix}дневник открыт. 🌿\n\n"
            "Пиши сюда всё, что живёт внутри — в любое время.\n"
            "А завтра утром тебя ждёт первая аффирмация."
        )
    elif text in ["/terms", "/privacy", "/delete_all"]:
        return
    else:
        # Сохраняем запись
        await execute_query(
            "INSERT INTO entries (user_id, text) VALUES ($1, $2)",
            message.from_user.id, text
        )
        await execute_query(
            "UPDATE users SET last_entry = NOW() WHERE user_id = $1",
            message.from_user.id
        )
        await message.answer("Записано. ✨")

# === SCHEDULER ===
async def send_daily_affirmation(bot: Bot):
    now = datetime.utcnow()
    users = await execute_query("""
        SELECT user_id FROM users 
        WHERE (trial_until > $1 OR (subscribed AND subscription_until > $1))
        AND agreed = TRUE
    """, now)
    for user in users:
        try:
            text = await get_unique_affirmation(user["user_id"])
            await bot.send_message(user["user_id"], text)
        except Exception:
            pass

def setup_scheduler(bot: Bot):
    scheduler = AsyncIOScheduler(timezone=os.getenv("TIMEZONE", "UTC"))
    scheduler.add_job(
        send_daily_affirmation,
        CronTrigger(hour=8, minute=0),
        args=[bot]
    )
    scheduler.start()

# === MAIN ===
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    setup_scheduler(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())