# -*- coding: utf-8 -*-

import os
import re
import asyncio
import logging
import secrets
from typing import Optional

import asyncpg

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# آیدی مالک‌ها
OWNER_IDS = {
    8753850861,
    8552447077,
}

CHANNEL_USERNAME = "@BET_Tekhhh"
GROUP_USERNAME = "@BET_TAKbotcv"

CHANNEL_URL = "https://t.me/BET_Tekhhh"
GROUP_URL = "https://t.me/BET_TAKbotcv"

# کانالی که درخواست برداشت نمایشی داخل آن ثبت می‌شود
WITHDRAW_CHANNEL = "@BET_Tekhhh"

UNIT = "DOGS موج بات"

REFERRAL_REWARD = 45
MIN_GAME_AMOUNT = 100
MIN_WITHDRAW = 1000

# حداکثر ۵ بازی فعال در هر گپ
MAX_ACTIVE_GAMES = 5

# پاداش برد
WIN_REWARD = 180

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("BET_TEK")

pool: Optional[asyncpg.Pool] = None

# بازی‌های فعال داخل RAM
games = {}

# برای جلوگیری از تداخل همزمان
games_lock = asyncio.Lock()


# =========================================================
# NORMALIZE
# =========================================================

def normalize_digits(text: str) -> str:
    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )
    return str(text).translate(table)


def normalize_text(text: str) -> str:
    text = text.replace("ي", "ی")
    text = text.replace("ك", "ک")
    text = text.replace("\u200c", " ")
    text = text.replace("٫", ".")
    return " ".join(text.split())


def parse_number(text: str):
    text = normalize_digits(text)
    text = text.replace(",", "")
    text = text.replace("٬", "")
    text = text.strip()

    if not text.isdigit():
        return None

    return int(text)


def user_mention(user_id: int, name: str) -> str:
    name = name or "کاربر"
    return f'<a href="tg://user?id={user_id}">{name}</a>'


# =========================================================
# DATABASE
# =========================================================

async def init_db():
    global pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL تنظیم نشده است."
        )

    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=30,
    )

    async with pool.acquire() as con:

        await con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance BIGINT NOT NULL DEFAULT 0,
                referrer_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        await con.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id BIGSERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referred_id BIGINT UNIQUE NOT NULL,
                reward BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        await con.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount BIGINT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        await con.execute("""
            CREATE TABLE IF NOT EXISTS transfers (
                id BIGSERIAL PRIMARY KEY,
                sender_id BIGINT NOT NULL,
                receiver_id BIGINT NOT NULL,
                amount BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        logger.info("Database initialized.")


async def ensure_user(
    tg_user,
    referrer_id: Optional[int] = None
):
    async with pool.acquire() as con:

        await con.execute(
            """
            INSERT INTO users
                (user_id, username, first_name)
            VALUES
                ($1, $2, $3)
            ON CONFLICT(user_id)
            DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name
            """,
            tg_user.id,
            tg_user.username,
            tg_user.first_name or "کاربر",
        )

        if referrer_id and referrer_id != tg_user.id:

            already = await con.fetchval(
                """
                SELECT 1
                FROM referrals
                WHERE referred_id=$1
                """,
                tg_user.id
            )

            if already:
                return

            ref_exists = await con.fetchval(
                """
                SELECT 1
                FROM users
                WHERE user_id=$1
                """,
                referrer_id
            )

            if not ref_exists:
                return

            await con.execute(
                """
                INSERT INTO referrals
                    (referrer_id, referred_id, reward)
                VALUES
                    ($1, $2, $3)
                ON CONFLICT(referred_id) DO NOTHING
                """,
                referrer_id,
                tg_user.id,
                REFERRAL_REWARD,
            )

            await con.execute(
                """
                UPDATE users
                SET balance=balance+$1
                WHERE user_id=$2
                """,
                REFERRAL_REWARD,
                referrer_id,
            )

            await con.execute(
                """
                UPDATE users
                SET referrer_id=$1
                WHERE user_id=$2
                """,
                referrer_id,
                tg_user.id,
            )


async def get_user(user_id: int):
    async with pool.acquire() as con:
        return await con.fetchrow(
            """
            SELECT *
            FROM users
            WHERE user_id=$1
            """,
            user_id,
        )


async def get_balance(user_id: int) -> int:
    async with pool.acquire() as con:
        value = await con.fetchval(
            """
            SELECT balance
            FROM users
            WHERE user_id=$1
            """,
            user_id,
        )

        return int(value or 0)


async def add_balance(
    user_id: int,
    amount: int
):
    async with pool.acquire() as con:
        await con.execute(
            """
            UPDATE users
            SET balance=balance+$1
            WHERE user_id=$2
            """,
            amount,
            user_id,
        )


async def remove_balance(
    user_id: int,
    amount: int
) -> bool:

    async with pool.acquire() as con:

        row = await con.fetchrow(
            """
            UPDATE users
            SET balance=balance-$1
            WHERE user_id=$2
            AND balance >= $1
            RETURNING balance
            """,
            amount,
            user_id,
        )

        return row is not None


# =========================================================
# FORCE JOIN
# =========================================================

async def check_member(
    bot,
    user_id: int,
    username: str
):

    try:

        member = await bot.get_chat_member(
            username,
            user_id
        )

        return member.status in (
            "member",
            "administrator",
            "creator",
        )

    except Exception:

        return False


def join_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 عضویت در کانال",
                url=CHANNEL_URL
            )
        ],
        [
            InlineKeyboardButton(
                "👥 عضویت در گپ",
                url=GROUP_URL
            )
        ],
        [
            InlineKeyboardButton(
                "✅ بررسی عضویت",
                callback_data="check_join"
            )
        ],
    ])


async def require_join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    channel_ok = await check_member(
        context.bot,
        user.id,
        CHANNEL_USERNAME
    )

    group_ok = await check_member(
        context.bot,
        user.id,
        GROUP_USERNAME
    )

    if channel_ok and group_ok:
        return True

    text = (
        "⛔ برای استفاده از ربات ابتدا باید "
        "در کانال و گپ عضو شوید."
    )

    if update.callback_query:

        await update.callback_query.answer(
            "عضویت کامل نیست.",
            show_alert=True
        )

        await update.callback_query.message.reply_text(
            text,
            reply_markup=join_keyboard()
        )

    elif update.message:

        await update.message.reply_text(
            text,
            reply_markup=join_keyboard()
        )

    return False


# =========================================================
# MAIN MENU
# =========================================================

def main_menu():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "💰 موجودی",
                callback_data="balance"
            ),
            InlineKeyboardButton(
                "👤 حساب من",
                callback_data="account"
            ),
        ],

        [
            InlineKeyboardButton(
                "💸 برداشت",
                callback_data="withdraw"
            ),
            InlineKeyboardButton(
                "🔁 انتقال",
                callback_data="transfer_help"
            ),
        ],

        [
            InlineKeyboardButton(
                "👥 زیرمجموعه",
                callback_data="referral"
            ),
            InlineKeyboardButton(
                "ℹ️ راهنما",
                callback_data="help"
            ),
        ],

    ])


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    referrer_id = None

    if context.args:

        arg = context.args[0]

        if arg.startswith("ref_"):

            try:
                referrer_id = int(arg[4:])
            except ValueError:
                pass

    await ensure_user(
        user,
        referrer_id
    )

    if not await require_join(
        update,
        context
    ):
        return

    balance = await get_balance(
        user.id
    )

    await update.message.reply_text(
        f"سلام {user.first_name} 👋\n\n"
        f"🤖 به DOGS موج بات خوش آمدی.\n\n"
        f"💰 موجودی:\n"
        f"{balance:,} {UNIT}\n\n"
        f"🎮 بازی‌ها داخل گپ انجام می‌شوند.",
        reply_markup=main_menu()
    )


# =========================================================
# BALANCE
# =========================================================

async def show_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    await ensure_user(user)

    balance = await get_balance(
        user.id
    )

    text = (
        f"💰 موجودی شما:\n\n"
        f"{balance:,} {UNIT}"
    )

    if update.callback_query:

        await update.callback_query.answer()

        await update.callback_query.message.reply_text(
            text
        )

    else:

        await update.message.reply_text(
            text
        )


# =========================================================
# HELP
# =========================================================

async def help_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = f"""
📚 راهنمای ربات

💰 موجودی:
موجودی
م

🎲 فرد و زوج:
100 فرد
100 زوج

🎳 بولینگ:
1 بولینگ 100

🎲 تاس:
1 تاس 100

🎯 دارت:
1 دارت 100

🤖 اگر دستور بازی را بدون ریپلای بفرستی:
بازی با ربات ساخته می‌شود.

👥 اگر دستور را روی پیام یک کاربر ریپلای کنی:
بازی با همان دوست ساخته می‌شود.

ترتیب بازی با ربات:
1️⃣ اول کاربر می‌اندازد.
2️⃣ بعد ربات می‌اندازد.

ترتیب بازی با دوست:
1️⃣ اول سازنده می‌اندازد.
2️⃣ بعد حریف می‌اندازد.

حداقل مقدار نمایشی بازی:
{MIN_GAME_AMOUNT:,} {UNIT}

حداکثر بازی فعال در هر گپ:
{MAX_ACTIVE_GAMES}

🎁 پاداش برد:
{WIN_REWARD:,} {UNIT}

👥 پاداش زیرمجموعه:
{REFERRAL_REWARD:,} {UNIT}

⚠️ {UNIT} فقط واحد داخلی و غیرنقدی این ربات است.
"""

    await update.message.reply_text(
        text
    )


# =========================================================
# GAME HELPERS
# =========================================================

GAME_TYPES = {
    "بولینگ": "🎳",
    "تاس": "🎲",
    "دارت": "🎯",
}


def active_game_count(chat_id: int):

    return sum(
        1
        for game in games.values()
        if game["chat_id"] == chat_id
        and game["status"]
        in (
            "waiting_creator",
            "waiting_opponent",
        )
    )


def make_game_id():

    return secrets.token_hex(5)


async def send_game_dice(
    bot,
    chat_id: int,
    game_type: str
):

    emoji = GAME_TYPES[game_type]

    message = await bot.send_dice(
        chat_id=chat_id,
        emoji=emoji
    )

    return message.dice.value


# =========================================================
# EVEN / ODD
# =========================================================

async def even_odd_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    amount: int,
    choice: str
):

    chat = update.effective_chat
    user = update.effective_user

    if active_game_count(chat.id) >= MAX_ACTIVE_GAMES:

        await update.message.reply_text(
            f"❌ حداکثر {MAX_ACTIVE_GAMES} بازی فعال است."
        )

        return

    dice_message = await context.bot.send_dice(
        chat_id=chat.id,
        emoji="🎲"
    )

    value = dice_message.dice.value

    result = (
        "زوج"
        if value % 2 == 0
        else "فرد"
    )

    if result == choice:

        await add_balance(
            user.id,
            WIN_REWARD
        )

        result_text = (
            f"🎉 برنده شدی!\n"
            f"🎁 +{WIN_REWARD:,} {UNIT}"
        )

    else:

        result_text = (
            "❌ این بار برنده نشدی."
        )

    balance = await get_balance(
        user.id
    )

    await context.bot.send_message(
        chat.id,

        f"🎲 بازی فرد / زوج\n\n"

        f"👤 بازیکن: "
        f"{user_mention(user.id, user.first_name)}\n"

        f"💠 مقدار نمایشی: "
        f"{amount:,} {UNIT}\n"

        f"🎯 انتخاب: {choice}\n"

        f"🎲 نتیجه تاس: {value}\n"
        f"📌 نتیجه: {result}\n\n"

        f"{result_text}\n\n"

        f"💰 موجودی: "
        f"{balance:,} {UNIT}",

        parse_mode="HTML"
    )


# =========================================================
# BOT GAME
# =========================================================

async def create_bot_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    game_type: str,
    amount: int
):

    chat = update.effective_chat
    user = update.effective_user

    if active_game_count(chat.id) >= MAX_ACTIVE_GAMES:

        await update.message.reply_text(
            f"❌ حداکثر {MAX_ACTIVE_GAMES} بازی فعال در گپ وجود دارد."
        )

        return

    game_id = make_game_id()

    games[game_id] = {

        "id": game_id,

        "chat_id": chat.id,

        "type": game_type,

        "amount": amount,

        "mode": "bot",

        "creator_id": user.id,

        "creator_name": user.first_name,

        "opponent_id": None,

        "opponent_name": "ربات",

        "creator_value": None,

        "opponent_value": None,

        "status": "waiting_creator",

    }

    emoji = GAME_TYPES[game_type]

    await update.message.reply_text(

        f"🎮 بازی {game_type} با ربات ساخته شد.\n\n"

        f"👤 سازنده: {user.first_name}\n"

        f"💠 مقدار نمایشی: "
        f"{amount:,} {UNIT}\n\n"

        f"{emoji} اول خودت {game_type} را بینداز.\n"

        f"⏳ بعد از پرتاب تو، ربات می‌اندازد.\n\n"

        f"⚠️ این بازی غیرنقدی است."
    )


# =========================================================
# FRIEND GAME
# =========================================================

async def create_friend_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    game_type: str,
    amount: int
):

    chat = update.effective_chat
    creator = update.effective_user

    if active_game_count(chat.id) >= MAX_ACTIVE_GAMES:

        await update.message.reply_text(
            f"❌ حداکثر {MAX_ACTIVE_GAMES} بازی فعال است."
        )

        return

    reply = update.message.reply_to_message

    if not reply:

        await update.message.reply_text(
            "❌ برای بازی با دوست باید دستور را "
            "روی پیام حریف ریپلای کنی.\n\n"

            "مثال:\n"
            "1 بولینگ 100"
        )

        return

    opponent = reply.from_user

    if opponent.is_bot:

        await update.message.reply_text(
            "❌ نمی‌توانی یک ربات را به عنوان دوست انتخاب کنی."
        )

        return

    if opponent.id == creator.id:

        await update.message.reply_text(
            "❌ نمی‌توانی خودت را حریف انتخاب کنی."
        )

        return

    game_id = make_game_id()

    games[game_id] = {

        "id": game_id,

        "chat_id": chat.id,

        "type": game_type,

        "amount": amount,

        "mode": "friend",

        "creator_id": creator.id,

        "creator_name": creator.first_name,

        "opponent_id": opponent.id,

        "opponent_name": opponent.first_name,

        "creator_value": None,

        "opponent_value": None,

        "status": "waiting_creator",

    }

    await update.message.reply_text(

        f"🎮 بازی {game_type} ساخته شد!\n\n"

        f"👤 سازنده: "
        f"{user_mention(creator.id, creator.first_name)}\n"

        f"👥 حریف: "
        f"{user_mention(opponent.id, opponent.first_name)}\n"

        f"💠 مقدار نمایشی: "
        f"{amount:,} {UNIT}\n\n"

        f"🎯 اول نوبت سازنده است.\n"

        f"⏳ {creator.first_name} باید "
        f"{game_type} بیندازد.\n\n"

        f"بعد از سازنده، نوبت حریف می‌شود.",

        parse_mode="HTML"
    )


# =========================================================
# RECEIVE DICE
# =========================================================

async def dice_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.message

    if not message or not message.dice:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    emoji = message.dice.emoji
    value = message.dice.value

    emoji_to_type = {
        "🎳": "بولینگ",
        "🎲": "تاس",
        "🎯": "دارت",
    }

    game_type = emoji_to_type.get(
        emoji
    )

    if not game_type:
        return

    # بازی مناسب را پیدا می‌کنیم
    selected_game = None

    for game in games.values():

        if game["chat_id"] != chat_id:
            continue

        if game["type"] != game_type:
            continue

        if game["status"] not in (
            "waiting_creator",
            "waiting_opponent",
        ):
            continue

        if game["status"] == "waiting_creator":

            if game["creator_id"] == user_id:

                selected_game = game
                break

        elif game["status"] == "waiting_opponent":

            if game["opponent_id"] == user_id:

                selected_game = game
                break

    if not selected_game:
        return

    game = selected_game

    # ------------------------------
    # اول سازنده
    # ------------------------------

    if game["status"] == "waiting_creator":

        game["creator_value"] = value

        # بازی با ربات
        if game["mode"] == "bot":

            game["status"] = "waiting_bot"

            await context.bot.send_message(
                chat_id,

                f"✅ نتیجه شما ثبت شد: {value}\n\n"
                f"🤖 حالا ربات {game_type} می‌اندازد..."
            )

            bot_value = await send_game_dice(
                context.bot,
                chat_id,
                game_type
            )

            game["opponent_value"] = bot_value

            await finish_game(
                context,
                game
            )

            return

        # بازی با دوست
        game["status"] = "waiting_opponent"

        await context.bot.send_message(

            chat_id,

            f"✅ پرتاب سازنده ثبت شد: {value}\n\n"

            f"🎯 حالا نوبت حریف است.\n"

            f"👤 حریف: "
            f"{user_mention(game['opponent_id'], game['opponent_name'])}\n\n"

            f"لطفاً {game_type} بینداز.",

            parse_mode="HTML"
        )

        return

    # ------------------------------
    # حریف
    # ------------------------------

    if game["status"] == "waiting_opponent":

        if game["opponent_id"] != user_id:
            return

        game["opponent_value"] = value

        await finish_game(
            context,
            game
        )


# =========================================================
# FINISH GAME
# =========================================================

async def finish_game(
    context: ContextTypes.DEFAULT_TYPE,
    game
):

    creator_value = game["creator_value"]
    opponent_value = game["opponent_value"]

    creator_id = game["creator_id"]
    opponent_id = game["opponent_id"]

    creator_name = game["creator_name"]
    opponent_name = game["opponent_name"]

    text = (
        f"🏁 نتیجه {game['type']}\n\n"

        f"👤 {creator_name}: "
        f"{creator_value}\n"
    )

    if game["mode"] == "bot":

        text += (
            f"🤖 ربات: "
            f"{opponent_value}\n\n"
        )

    else:

        text += (
            f"👤 {opponent_name}: "
            f"{opponent_value}\n\n"
        )

    winner_id = None
    winner_name = None

    if creator_value > opponent_value:

        winner_id = creator_id
        winner_name = creator_name

    elif opponent_value > creator_value:

        if game["mode"] == "friend":

            winner_id = opponent_id
            winner_name = opponent_name

    if winner_id:

        await add_balance(
            winner_id,
            WIN_REWARD
        )

        new_balance = await get_balance(
            winner_id
        )

        text += (
            f"🏆 برنده: {winner_name}\n\n"

            f"🎁 پاداش: "
            f"+{WIN_REWARD:,} {UNIT}\n"

            f"💰 موجودی جدید: "
            f"{new_balance:,} {UNIT}"
        )

    else:

        text += (
            "🤝 نتیجه مساوی شد.\n\n"
            "هیچ پاداشی در حالت مساوی داده نشد."
        )

    await context.bot.send_message(
        game["chat_id"],
        text
    )

    games.pop(
        game["id"],
        None
    )


# =========================================================
# TRANSFER
# =========================================================

async def transfer_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP
    ):

        await update.message.reply_text(
            "❌ انتقال فقط داخل گپ فعال است."
        )

        return

    user = update.effective_user

    reply = update.message.reply_to_message

    if not reply:

        await update.message.reply_text(
            "برای انتقال روی پیام کاربر ریپلای کن.\n\n"
            "مثال:\n"
            "انتقال 500"
        )

        return

    receiver = reply.from_user

    if receiver.is_bot:

        await update.message.reply_text(
            "❌ گیرنده نمی‌تواند ربات باشد."
        )

        return

    if receiver.id == user.id:

        await update.message.reply_text(
            "❌ نمی‌توانی به خودت انتقال بدهی."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "❌ مقدار را وارد کن.\n"
            "مثال: انتقال 500"
        )

        return

    amount = parse_number(
        context.args[0]
    )

    if amount is None or amount <= 0:

        await update.message.reply_text(
            "❌ مقدار نامعتبر است."
        )

        return

    success = await remove_balance(
        user.id,
        amount
    )

    if not success:

        balance = await get_balance(
            user.id
        )

        await update.message.reply_text(
            f"❌ موجودی کافی نیست.\n\n"
            f"💰 موجودی: "
            f"{balance:,} {UNIT}"
        )

        return

    await add_balance(
        receiver.id,
        amount
    )

    async with pool.acquire() as con:

        await con.execute(
            """
            INSERT INTO transfers
                (sender_id, receiver_id, amount)
            VALUES
                ($1, $2, $3)
            """,
            user.id,
            receiver.id,
            amount
        )

    await update.message.reply_text(

        f"✅ انتقال انجام شد.\n\n"

        f"👤 گیرنده: "
        f"{receiver.first_name}\n"

        f"💰 مقدار: "
        f"{amount:,} {UNIT}\n\n"

        f"💳 موجودی شما: "
        f"{await get_balance(user.id):,} {UNIT}"
    )


# =========================================================
# WITHDRAW - DEMO ONLY
# =========================================================

async def withdraw_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data["waiting_withdraw"] = True

    text = (
        f"💸 برداشت {UNIT}\n\n"

        f"تعداد را وارد کنید.\n"

        f"حداقل برداشت: "
        f"{MIN_WITHDRAW:,} {UNIT}\n\n"

        f"⚠️ این برداشت فقط درخواست نمایشی است "
        f"و هیچ پرداخت واقعی انجام نمی‌شود."
    )

    if update.callback_query:

        await update.callback_query.answer()

        await update.callback_query.message.reply_text(
            text
        )

    else:

        await update.message.reply_text(
            text
        )


async def process_withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.user_data.get(
        "waiting_withdraw"
    ):
        return False

    amount = parse_number(
        update.message.text
    )

    if amount is None:

        await update.message.reply_text(
            "❌ فقط عدد وارد کن."
        )

        return True

    if amount < MIN_WITHDRAW:

        await update.message.reply_text(
            f"❌ حداقل برداشت "
            f"{MIN_WITHDRAW:,} {UNIT} است."
        )

        return True

    user = update.effective_user

    success = await remove_balance(
        user.id,
        amount
    )

    if not success:

        balance = await get_balance(
            user.id
        )

        await update.message.reply_text(
            f"❌ موجودی کافی نیست.\n\n"
            f"💰 موجودی: "
            f"{balance:,} {UNIT}"
        )

        context.user_data[
            "waiting_withdraw"
        ] = False

        return True

    async with pool.acquire() as con:

        row = await con.fetchrow(
            """
            INSERT INTO withdrawals
                (user_id, amount)
            VALUES
                ($1, $2)
            RETURNING id
            """,
            user.id,
            amount
        )

    request_id = row["id"]

    # ثبت در کانال
    try:

        await context.bot.send_message(

            WITHDRAW_CHANNEL,

            f"🔔 درخواست برداشت جدید\n\n"

            f"👤 آیدی کاربر: "
            f"`{user.id}`\n"

            f"💰 مقدار: "
            f"{amount:,} {UNIT}\n"

            f"🆔 شماره درخواست: "
            f"`{request_id}`\n"

            f"📌 وضعیت: در انتظار بررسی\n\n"

            f"⚠️ این درخواست غیرنقدی و نمایشی است.",

            parse_mode="Markdown"
        )

    except Exception as error:

        logger.error(
            "Withdraw channel error: %s",
            error
        )

    context.user_data[
        "waiting_withdraw"
    ] = False

    await update.message.reply_text(

        "✅ برداشت شما در کانال ثبت شد.\n\n"

        f"💰 مقدار: "
        f"{amount:,} {UNIT}\n"

        f"🆔 درخواست: "
        f"{request_id}"
    )

    return True


# =========================================================
# REFERRAL
# =========================================================

async def referral(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    me = await context.bot.get_me()

    link = (
        f"https://t.me/"
        f"{me.username}"
        f"?start=ref_{user.id}"
    )

    text = (
        f"👥 زیرمجموعه\n\n"

        f"🎁 پاداش هر دعوت: "
        f"{REFERRAL_REWARD} {UNIT}\n\n"

        f"🔗 لینک دعوت شما:\n"
        f"{link}"
    )

    await update.message.reply_text(
        text
    )


# =========================================================
# ADMIN
# =========================================================

def is_owner(user_id: int):

    return user_id in OWNER_IDS


async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ دسترسی ندارید."
        )

        return

    await update.message.reply_text(

        "🛠 پنل مدیریت\n\n"

        "➕ اضافه کردن موجودی:\n"
        "/add ID AMOUNT\n\n"

        "➖ کسر موجودی:\n"
        "/sub ID AMOUNT\n\n"

        "💰 مشاهده موجودی:\n"
        "/bal ID\n\n"

        "📊 آمار:\n"
        "/stats"
    )


async def admin_add(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 2:

        await update.message.reply_text(
            "/add ID AMOUNT"
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        amount = parse_number(
            context.args[1]
        )

        if amount is None or amount <= 0:
            raise ValueError

    except:

        await update.message.reply_text(
            "❌ اطلاعات اشتباه است."
        )

        return

    await add_balance(
        user_id,
        amount
    )

    await update.message.reply_text(

        f"✅ موجودی اضافه شد.\n\n"

        f"➕ {amount:,} {UNIT}\n"

        f"💰 موجودی جدید: "
        f"{await get_balance(user_id):,} {UNIT}"
    )


async def admin_sub(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 2:

        await update.message.reply_text(
            "/sub ID AMOUNT"
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        amount = parse_number(
            context.args[1]
        )

        if amount is None or amount <= 0:
            raise ValueError

    except:

        await update.message.reply_text(
            "❌ اطلاعات اشتباه است."
        )

        return

    success = await remove_balance(
        user_id,
        amount
    )

    if not success:

        await update.message.reply_text(
            "❌ کاربر وجود ندارد یا موجودی کافی نیست."
        )

        return

    await update.message.reply_text(

        f"✅ موجودی کسر شد.\n\n"

        f"➖ {amount:,} {UNIT}\n"

        f"💰 موجودی جدید: "
        f"{await get_balance(user_id):,} {UNIT}"
    )


async def admin_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await update.message.reply_text(
            "/bal ID"
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

    except:

        await update.message.reply_text(
            "❌ ID اشتباه است."
        )

        return

    user = await get_user(
        user_id
    )

    if not user:

        await update.message.reply_text(
            "❌ کاربر پیدا نشد."
        )

        return

    await update.message.reply_text(

        f"👤 {user['first_name']}\n"
        f"🆔 {user_id}\n"
        f"💰 {user['balance']:,} {UNIT}"
    )


async def admin_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    async with pool.acquire() as con:

        users = await con.fetchval(
            "SELECT COUNT(*) FROM users"
        )

        total = await con.fetchval(
            "SELECT COALESCE(SUM(balance),0) FROM users"
        )

        referrals_count = await con.fetchval(
            "SELECT COUNT(*) FROM referrals"
        )

        withdrawals = await con.fetchval(
            """
            SELECT COUNT(*)
            FROM withdrawals
            WHERE status='pending'
            """
        )

    await update.message.reply_text(

        "📊 آمار ربات\n\n"

        f"👤 کاربران: {users:,}\n"
        f"💰 مجموع موجودی: {total:,} {UNIT}\n"
        f"👥 زیرمجموعه‌ها: {referrals_count:,}\n"
        f"💸 برداشت‌های در انتظار: {withdrawals:,}\n"
        f"🎮 بازی فعال: {len(games):,}"
    )


# =========================================================
# CALLBACKS
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query.data == "check_join":

        await query.answer()

        if await require_join(
            update,
            context
        ):

            await query.message.reply_text(
                "✅ عضویت شما تأیید شد.",
                reply_markup=main_menu()
            )

        return

    if not await require_join(
        update,
        context
    ):
        return

    if query.data == "balance":

        await query.answer()

        await show_balance(
            update,
            context
        )

    elif query.data == "account":

        await query.answer()

        user = await get_user(
            update.effective_user.id
        )

        await query.message.reply_text(

            f"👤 حساب کاربری\n\n"

            f"نام: {user['first_name']}\n"

            f"آیدی: {user['user_id']}\n"

            f"💰 موجودی: "
            f"{user['balance']:,} {UNIT}"
        )

    elif query.data == "withdraw":

        await query.answer()

        await withdraw_start(
            update,
            context
        )

    elif query.data == "transfer_help":

        await query.answer()

        await query.message.reply_text(

            "🔁 انتقال داخل گپ\n\n"

            "روی پیام کاربر ریپلای کن و بنویس:\n\n"

            "انتقال 500"
        )

    elif query.data == "referral":

        await query.answer()

        user = update.effective_user

        me = await context.bot.get_me()

        link = (
            f"https://t.me/"
            f"{me.username}"
            f"?start=ref_{user.id}"
        )

        await query.message.reply_text(

            f"👥 زیرمجموعه\n\n"

            f"🎁 پاداش دعوت: "
            f"{REFERRAL_REWARD} {UNIT}\n\n"

            f"🔗 لینک دعوت:\n"
            f"{link}"
        )

    elif query.data == "help":

        await query.answer()

        await query.message.reply_text(
            "برای راهنما از /help استفاده کن."
        )


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.message.text:
        return

    user = update.effective_user

    await ensure_user(
        user
    )

    # برداشت در انتظار
    if context.user_data.get(
        "waiting_withdraw"
    ):

        handled = await process_withdraw(
            update,
            context
        )

        if handled:
            return

    text = normalize_text(
        update.message.text
    )

    lower = text.lower()

    # موجودی
    if lower in (
        "موجودی",
        "م",
        "balance"
    ):

        await show_balance(
            update,
            context
        )

        return

    # انتقال
    if lower.startswith(
        "انتقال"
    ):

        parts = text.split()

        context.args = parts[1:]

        await transfer_command(
            update,
            context
        )

        return

    # بازی‌ها فقط در گروه
    if update.effective_chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP
    ):
        return

    if not await require_join(
        update,
        context
    ):
        return

    parts = text.split()

    # -----------------------------------------
    # فرد / زوج
    # -----------------------------------------

    if len(parts) == 2:

        amount = parse_number(
            parts[0]
        )

        choice = parts[1].lower()

        if (
            amount is not None
            and amount >= MIN_GAME_AMOUNT
            and choice in (
                "فرد",
                "زوج"
            )
        ):

            await even_odd_game(
                update,
                context,
                amount,
                choice
            )

            return

    # -----------------------------------------
    # بولینگ / تاس / دارت
    # -----------------------------------------

    if len(parts) == 3:

        first = parse_number(
            parts[0]
        )

        game_type = parts[1].lower()

        amount = parse_number(
            parts[2]
        )

        if (
            first == 1
            and game_type in GAME_TYPES
            and amount is not None
            and amount >= MIN_GAME_AMOUNT
        ):

            if update.message.reply_to_message:

                await create_friend_game(
                    update,
                    context,
                    game_type,
                    amount
                )

            else:

                await create_bot_game(
                    update,
                    context,
                    game_type,
                    amount
                )

            return


# =========================================================
# COMMANDS
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await help_text(
        update,
        context
    )


async def balance_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await show_balance(
        update,
        context
    )


async def referral_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await referral(
        update,
        context
    )


# =========================================================
# POST INIT / SHUTDOWN
# =========================================================

async def post_init(
    application: Application
):

    await init_db()


async def post_shutdown(
    application: Application
):

    global pool

    if pool:

        await pool.close()

        pool = None


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN در ENV تنظیم نشده است."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(
        CommandHandler(
            "balance",
            balance_command
        )
    )

    application.add_handler(
        CommandHandler(
            "referral",
            referral_command
        )
    )

    application.add_handler(
        CommandHandler(
            "withdraw",
            withdraw_start
        )
    )

    # Admin
    application.add_handler(
        CommandHandler(
            "admin",
            admin_panel
        )
    )

    application.add_handler(
        CommandHandler(
            "add",
            admin_add
        )
    )

    application.add_handler(
        CommandHandler(
            "sub",
            admin_sub
        )
    )

    application.add_handler(
        CommandHandler(
            "bal",
            admin_balance
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            admin_stats
        )
    )

    # Buttons
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # تاس / بولینگ / دارت
    application.add_handler(
        MessageHandler(
            filters.Dice.ALL,
            dice_handler
        )
    )

    # متن
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    logger.info(
        "BET TeK started."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
