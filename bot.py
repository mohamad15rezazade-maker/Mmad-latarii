# -*- coding: utf-8 -*-

import os
import re
import sqlite3
import asyncio
import secrets
import logging
from contextlib import closing

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
# تنظیمات
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

OWNER_IDS = {
    8753850861,
    8552447077,
}

CHANNEL_USERNAME = "@BET_Tekhhh"
GROUP_USERNAME = "@BET_TAKbotcv"

CHANNEL_URL = "https://t.me/BET_Tekhhh"
GROUP_URL = "https://t.me/BET_TAKbotcv"

# کانال ثبت درخواست برداشت نمایشی
WITHDRAW_CHANNEL = "@BET_Tekhhh"

DB_FILE = "bot.db"

UNIT = "DOGS موج بات"

REFERRAL_REWARD = 45

MIN_GAME_AMOUNT = 100
MIN_WITHDRAW = 1000

WIN_REWARD = 180

MAX_ACTIVE_GAMES = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("BET_TEK")

db_lock = asyncio.Lock()

# بازی‌های فعال
games = {}


# =========================================================
# اعداد فارسی / عربی
# =========================================================

def normalize_digits(text):
    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )
    return str(text).translate(table)


def parse_number(text):
    text = normalize_digits(str(text))
    text = text.replace(",", "")
    text = text.replace("٬", "")
    text = text.strip()

    if not text.isdigit():
        return None

    return int(text)


def clean_text(text):
    text = str(text)
    text = text.replace("ي", "ی")
    text = text.replace("ك", "ک")
    text = text.replace("\u200c", " ")
    return " ".join(text.split())


# =========================================================
# DATABASE
# =========================================================

def get_db():
    con = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )

    con.row_factory = sqlite3.Row

    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")

    return con


def init_db():

    with closing(get_db()) as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER NOT NULL DEFAULT 0,
                referrer_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER UNIQUE NOT NULL,
                reward INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.commit()


async def ensure_user(user, referrer_id=None):

    async with db_lock:

        with closing(get_db()) as db:

            row = db.execute(
                "SELECT user_id FROM users WHERE user_id=?",
                (user.id,)
            ).fetchone()

            if not row:

                db.execute("""
                    INSERT INTO users
                    (user_id, username, first_name, balance, referrer_id)
                    VALUES (?, ?, ?, 0, ?)
                """, (
                    user.id,
                    user.username,
                    user.first_name or "کاربر",
                    None
                ))

                db.commit()

            else:

                db.execute("""
                    UPDATE users
                    SET username=?,
                        first_name=?
                    WHERE user_id=?
                """, (
                    user.username,
                    user.first_name or "کاربر",
                    user.id
                ))

                db.commit()

            # پاداش دعوت فقط یک بار
            if (
                referrer_id
                and referrer_id != user.id
            ):

                referral_exists = db.execute("""
                    SELECT id
                    FROM referrals
                    WHERE referred_id=?
                """, (user.id,)).fetchone()

                if not referral_exists:

                    referrer_exists = db.execute("""
                        SELECT user_id
                        FROM users
                        WHERE user_id=?
                    """, (referrer_id,)).fetchone()

                    if referrer_exists:

                        db.execute("""
                            INSERT INTO referrals
                            (referrer_id, referred_id, reward)
                            VALUES (?, ?, ?)
                        """, (
                            referrer_id,
                            user.id,
                            REFERRAL_REWARD
                        ))

                        db.execute("""
                            UPDATE users
                            SET balance = balance + ?
                            WHERE user_id=?
                        """, (
                            REFERRAL_REWARD,
                            referrer_id
                        ))

                        db.execute("""
                            UPDATE users
                            SET referrer_id=?
                            WHERE user_id=?
                        """, (
                            referrer_id,
                            user.id
                        ))

                        db.commit()


async def get_balance(user_id):

    async with db_lock:

        with closing(get_db()) as db:

            row = db.execute("""
                SELECT balance
                FROM users
                WHERE user_id=?
            """, (user_id,)).fetchone()

            if not row:
                return 0

            return int(row["balance"])


async def add_balance(user_id, amount):

    async with db_lock:

        with closing(get_db()) as db:

            db.execute("""
                UPDATE users
                SET balance = balance + ?
                WHERE user_id=?
            """, (
                amount,
                user_id
            ))

            db.commit()


async def remove_balance(user_id, amount):

    async with db_lock:

        with closing(get_db()) as db:

            row = db.execute("""
                SELECT balance
                FROM users
                WHERE user_id=?
            """, (user_id,)).fetchone()

            if not row:
                return False

            balance = int(row["balance"])

            if balance < amount:
                return False

            db.execute("""
                UPDATE users
                SET balance = balance - ?
                WHERE user_id=?
            """, (
                amount,
                user_id
            ))

            db.commit()

            return True


# =========================================================
# عضویت اجباری
# =========================================================

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
        ]
    ])


async def check_member(bot, user_id, chat):

    try:

        member = await bot.get_chat_member(
            chat,
            user_id
        )

        return member.status in (
            "member",
            "administrator",
            "creator"
        )

    except Exception as e:

        logger.warning(
            "Membership check failed: %s",
            e
        )

        return False


async def require_join(update, context):

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
        "در کانال و گپ عضو شوی."
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
# منوی اصلی
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
            )
        ],

        [
            InlineKeyboardButton(
                "💸 برداشت",
                callback_data="withdraw"
            ),
            InlineKeyboardButton(
                "🔁 انتقال",
                callback_data="transfer_help"
            )
        ],

        [
            InlineKeyboardButton(
                "👥 زیرمجموعه",
                callback_data="referral"
            ),
            InlineKeyboardButton(
                "ℹ️ راهنما",
                callback_data="help"
            )
        ]

    ])


# =========================================================
# START
# =========================================================

async def start(update, context):

    user = update.effective_user

    referrer_id = None

    if context.args:

        arg = context.args[0]

        if arg.startswith("ref_"):

            try:
                referrer_id = int(arg[4:])
            except:
                referrer_id = None

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
# موجودی
# =========================================================

async def show_balance(update, context):

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
# راهنما
# =========================================================

async def show_help(update, context):

    text = f"""
📚 راهنمای DOGS موج بات

💰 موجودی:
موجودی
م

🎲 فرد:
100 فرد

🎲 زوج:
100 زوج

🎳 بولینگ:
1 بولینگ 100

🎲 تاس:
1 تاس 100

🎯 دارت:
1 دارت 100

👥 بازی با دوست:
روی پیام دوست ریپلای کن و بنویس:
1 بولینگ 100

🔁 انتقال داخل گپ:
روی پیام کاربر ریپلای کن:
انتقال 500

🎁 پاداش دعوت:
{REFERRAL_REWARD:,} {UNIT}

🏆 پاداش برد:
{WIN_REWARD:,} {UNIT}

💠 حداقل بازی:
{MIN_GAME_AMOUNT:,} {UNIT}

💸 حداقل درخواست برداشت:
{MIN_WITHDRAW:,} {UNIT}

⚠️ {UNIT} واحد داخلی و غیرنقدی ربات است.
"""

    await update.message.reply_text(
        text
    )


# =========================================================
# فرد / زوج
# =========================================================

async def even_odd_game(
    update,
    context,
    amount,
    choice
):

    user = update.effective_user

    dice = await context.bot.send_dice(
        update.effective_chat.id,
        emoji="🎲"
    )

    value = dice.dice.value

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

        reward_text = (
            f"🏆 برنده شدی!\n"
            f"🎁 +{WIN_REWARD:,} {UNIT}"
        )

    else:

        reward_text = (
            "❌ این بار برنده نشدی."
        )

    balance = await get_balance(
        user.id
    )

    await context.bot.send_message(

        update.effective_chat.id,

        f"🎲 بازی فرد / زوج\n\n"

        f"👤 {user.first_name}\n"

        f"💠 مقدار نمایشی: "
        f"{amount:,} {UNIT}\n"

        f"🎯 انتخاب: {choice}\n"

        f"🎲 عدد تاس: {value}\n"

        f"📌 نتیجه: {result}\n\n"

        f"{reward_text}\n\n"

        f"💰 موجودی: "
        f"{balance:,} {UNIT}"
    )


# =========================================================
# بازی‌ها
# =========================================================

GAME_EMOJI = {
    "بولینگ": "🎳",
    "تاس": "🎲",
    "دارت": "🎯"
}


def active_game_count(chat_id):

    return sum(
        1
        for game in games.values()
        if game["chat_id"] == chat_id
        and game["status"] in (
            "waiting_creator",
            "waiting_opponent"
        )
    )


def new_game_id():

    return secrets.token_hex(5)


async def create_game(
    update,
    context,
    game_type,
    amount
):

    chat = update.effective_chat
    creator = update.effective_user

    if active_game_count(chat.id) >= MAX_ACTIVE_GAMES:

        await update.message.reply_text(
            f"❌ حداکثر {MAX_ACTIVE_GAMES} بازی فعال است."
        )

        return

    reply = update.message.reply_to_message

    if reply:

        opponent = reply.from_user

        if opponent.is_bot:

            await update.message.reply_text(
                "❌ ربات نمی‌تواند حریف باشد."
            )

            return

        if opponent.id == creator.id:

            await update.message.reply_text(
                "❌ نمی‌توانی خودت را حریف انتخاب کنی."
            )

            return

        mode = "friend"

        opponent_id = opponent.id
        opponent_name = opponent.first_name

    else:

        mode = "bot"

        opponent_id = None
        opponent_name = "ربات"

    game_id = new_game_id()

    games[game_id] = {

        "id": game_id,

        "chat_id": chat.id,

        "type": game_type,

        "amount": amount,

        "mode": mode,

        "creator_id": creator.id,

        "creator_name": creator.first_name,

        "opponent_id": opponent_id,

        "opponent_name": opponent_name,

        "creator_value": None,

        "opponent_value": None,

        "status": "waiting_creator"
    }

    emoji = GAME_EMOJI[game_type]

    if mode == "bot":

        text = (
            f"🎮 بازی {game_type} با ربات ساخته شد.\n\n"

            f"👤 سازنده: {creator.first_name}\n"

            f"💠 مقدار نمایشی: "
            f"{amount:,} {UNIT}\n\n"

            f"{emoji} اول خودت {game_type} را بینداز.\n\n"

            f"بعد از تو ربات می‌اندازد."
        )

    else:

        text = (
            f"🎮 بازی {game_type} با دوست ساخته شد.\n\n"

            f"👤 سازنده: {creator.first_name}\n"

            f"👥 حریف: {opponent_name}\n"

            f"💠 مقدار نمایشی: "
            f"{amount:,} {UNIT}\n\n"

            f"{emoji} اول سازنده باید بیندازد.\n"

            f"بعد نوبت حریف است."
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# دریافت تاس / بولینگ / دارت
# =========================================================

async def dice_handler(update, context):

    message = update.message

    if not message or not message.dice:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    emoji = message.dice.emoji
    value = message.dice.value

    reverse = {
        "🎳": "بولینگ",
        "🎲": "تاس",
        "🎯": "دارت"
    }

    game_type = reverse.get(emoji)

    if not game_type:
        return

    selected = None

    for game in list(games.values()):

        if game["chat_id"] != chat_id:
            continue

        if game["type"] != game_type:
            continue

        if game["status"] == "waiting_creator":

            if game["creator_id"] == user_id:

                selected = game
                break

        elif game["status"] == "waiting_opponent":

            if game["opponent_id"] == user_id:

                selected = game
                break

    if not selected:
        return

    game = selected

    # --------------------------
    # پرتاب سازنده
    # --------------------------

    if game["status"] == "waiting_creator":

        game["creator_value"] = value

        # بازی با ربات
        if game["mode"] == "bot":

            game["status"] = "waiting_bot"

            await context.bot.send_message(

                chat_id,

                f"✅ عدد شما: {value}\n\n"
                f"🤖 حالا ربات {game_type} می‌اندازد..."
            )

            bot_message = await context.bot.send_dice(
                chat_id,
                emoji=emoji
            )

            game["opponent_value"] = (
                bot_message.dice.value
            )

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

            f"🎯 نوبت حریف:\n"
            f"{game['opponent_name']}\n\n"

            f"لطفاً {game_type} بینداز."
        )

        return

    # --------------------------
    # پرتاب حریف
    # --------------------------

    if game["status"] == "waiting_opponent":

        if game["opponent_id"] != user_id:
            return

        game["opponent_value"] = value

        await finish_game(
            context,
            game
        )


# =========================================================
# پایان بازی
# =========================================================

async def finish_game(
    context,
    game
):

    creator_value = game["creator_value"]
    opponent_value = game["opponent_value"]

    creator_id = game["creator_id"]
    opponent_id = game["opponent_id"]

    creator_name = game["creator_name"]
    opponent_name = game["opponent_name"]

    winner_id = None
    winner_name = None

    if creator_value > opponent_value:

        winner_id = creator_id
        winner_name = creator_name

    elif opponent_value > creator_value:

        if game["mode"] == "friend":

            winner_id = opponent_id
            winner_name = opponent_name

    text = (
        f"🏁 نتیجه {game['type']}\n\n"

        f"👤 {creator_name}: "
        f"{creator_value}\n"

        f"👤 {opponent_name}: "
        f"{opponent_value}\n\n"
    )

    if winner_id:

        await add_balance(
            winner_id,
            WIN_REWARD
        )

        balance = await get_balance(
            winner_id
        )

        text += (
            f"🏆 برنده: {winner_name}\n\n"

            f"🎁 پاداش:\n"
            f"+{WIN_REWARD:,} {UNIT}\n\n"

            f"💰 موجودی جدید:\n"
            f"{balance:,} {UNIT}"
        )

    else:

        text += (
            "🤝 مساوی شد.\n\n"
            "پاداشی داده نشد."
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
# انتقال
# =========================================================

async def do_transfer(
    update,
    amount
):

    sender = update.effective_user

    reply = update.message.reply_to_message

    if not reply:

        await update.message.reply_text(
            "❌ برای انتقال باید روی پیام کاربر ریپلای کنی."
        )

        return

    receiver = reply.from_user

    if receiver.is_bot:

        await update.message.reply_text(
            "❌ نمی‌توانی به ربات انتقال بدهی."
        )

        return

    if receiver.id == sender.id:

        await update.message.reply_text(
            "❌ نمی‌توانی به خودت انتقال بدهی."
        )

        return

    if amount <= 0:

        await update.message.reply_text(
            "❌ مقدار نامعتبر است."
        )

        return

    success = await remove_balance(
        sender.id,
        amount
    )

    if not success:

        balance = await get_balance(
            sender.id
        )

        await update.message.reply_text(
            f"❌ موجودی کافی نیست.\n\n"
            f"💰 موجودی: "
            f"{balance:,} {UNIT}"
        )

        return

    await ensure_user(
        receiver
    )

    await add_balance(
        receiver.id,
        amount
    )

    async with db_lock:

        with closing(get_db()) as db:

            db.execute("""
                INSERT INTO transfers
                (sender_id, receiver_id, amount)
                VALUES (?, ?, ?)
            """, (
                sender.id,
                receiver.id,
                amount
            ))

            db.commit()

    sender_balance = await get_balance(
        sender.id
    )

    await update.message.reply_text(

        f"✅ انتقال انجام شد.\n\n"

        f"👤 گیرنده: {receiver.first_name}\n"

        f"💰 مقدار: "
        f"{amount:,} {UNIT}\n\n"

        f"💳 موجودی شما:\n"
        f"{sender_balance:,} {UNIT}"
    )


# =========================================================
# برداشت نمایشی
# =========================================================

async def withdraw_start(update, context):

    context.user_data[
        "waiting_withdraw"
    ] = True

    text = (
        f"💸 درخواست برداشت\n\n"

        f"حداقل: "
        f"{MIN_WITHDRAW:,} {UNIT}\n\n"

        f"مقدار را به صورت عدد بفرست."
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
    update,
    context
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

    balance = await get_balance(
        user.id
    )

    if balance < amount:

        await update.message.reply_text(
            f"❌ موجودی کافی نیست.\n\n"
            f"💰 موجودی: "
            f"{balance:,} {UNIT}"
        )

        context.user_data[
            "waiting_withdraw"
        ] = False

        return True

    async with db_lock:

        with closing(get_db()) as db:

            cur = db.execute("""
                INSERT INTO withdrawals
                (user_id, amount, status)
                VALUES (?, ?, 'pending')
            """, (
                user.id,
                amount
            ))

            request_id = cur.lastrowid

            db.commit()

    # ثبت درخواست در کانال
    try:

        await context.bot.send_message(

            WITHDRAW_CHANNEL,

            f"🔔 درخواست برداشت نمایشی\n\n"

            f"👤 آیدی کاربر:\n"
            f"`{user.id}`\n\n"

            f"📱 یوزرنیم:\n"
            f"@{user.username if user.username else 'ندارد'}\n\n"

            f"💰 مقدار:\n"
            f"{amount:,} {UNIT}\n\n"

            f"🆔 شماره درخواست:\n"
            f"`{request_id}`\n\n"

            f"📌 وضعیت: در انتظار بررسی\n\n"

            f"⚠️ پرداخت واقعی انجام نمی‌شود.",

            parse_mode="Markdown"
        )

    except Exception as e:

        logger.error(
            "Withdraw channel error: %s",
            e
        )

    context.user_data[
        "waiting_withdraw"
    ] = False

    await update.message.reply_text(

        "✅ برداشت شما در کانال ثبت شد.\n\n"

        f"💰 مقدار: "
        f"{amount:,} {UNIT}\n"

        f"🆔 شماره درخواست: "
        f"{request_id}"
    )

    return True


# =========================================================
# زیرمجموعه
# =========================================================

async def referral(update, context):

    user = update.effective_user

    me = await context.bot.get_me()

    link = (
        f"https://t.me/"
        f"{me.username}"
        f"?start=ref_{user.id}"
    )

    await update.message.reply_text(

        f"👥 سیستم دعوت\n\n"

        f"🎁 پاداش هر دعوت:\n"
        f"{REFERRAL_REWARD:,} {UNIT}\n\n"

        f"🔗 لینک دعوت شما:\n"
        f"{link}"
    )


# =========================================================
# پنل مدیریت
# =========================================================

def is_owner(user_id):

    return user_id in OWNER_IDS


def admin_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📋 موجودی کاربران",
                callback_data="admin_users"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 آمار",
                callback_data="admin_stats"
            )
        ],

        [
            InlineKeyboardButton(
                "💸 برداشت‌ها",
                callback_data="admin_withdrawals"
            )
        ]

    ])


async def admin(update, context):

    if not is_owner(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "⛔ دسترسی ندارید."
        )
        return

    await update.message.reply_text(
        "🛠 پنل مدیریت",
        reply_markup=admin_keyboard()
    )


async def admin_add(update, context):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 2:

        await update.message.reply_text(
            "مثال:\n"
            "/add 123456789 500"
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        amount = parse_number(
            context.args[1]
        )

    except:

        await update.message.reply_text(
            "❌ اطلاعات اشتباه است."
        )

        return

    if amount is None or amount <= 0:

        await update.message.reply_text(
            "❌ مقدار اشتباه است."
        )

        return

    await add_balance(
        user_id,
        amount
    )

    balance = await get_balance(
        user_id
    )

    await update.message.reply_text(

        f"✅ موجودی اضافه شد.\n\n"

        f"➕ {amount:,} {UNIT}\n"

        f"💰 موجودی جدید:\n"
        f"{balance:,} {UNIT}"
    )


async def admin_sub(update, context):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 2:

        await update.message.reply_text(
            "مثال:\n"
            "/sub 123456789 500"
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        amount = parse_number(
            context.args[1]
        )

    except:

        await update.message.reply_text(
            "❌ اطلاعات اشتباه است."
        )

        return

    if amount is None or amount <= 0:

        await update.message.reply_text(
            "❌ مقدار اشتباه است."
        )

        return

    success = await remove_balance(
        user_id,
        amount
    )

    if not success:

        await update.message.reply_text(
            "❌ موجودی کافی نیست یا کاربر وجود ندارد."
        )

        return

    balance = await get_balance(
        user_id
    )

    await update.message.reply_text(

        f"✅ موجودی کم شد.\n\n"

        f"➖ {amount:,} {UNIT}\n"

        f"💰 موجودی جدید:\n"
        f"{balance:,} {UNIT}"
    )


async def admin_balance(update, context):

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

    async with db_lock:

        with closing(get_db()) as db:

            row = db.execute("""
                SELECT *
                FROM users
                WHERE user_id=?
            """, (
                user_id,
            )).fetchone()

    if not row:

        await update.message.reply_text(
            "❌ کاربر پیدا نشد."
        )

        return

    username = (
        f"@{row['username']}"
        if row["username"]
        else "بدون یوزرنیم"
    )

    await update.message.reply_text(

        f"👤 {row['first_name']}\n"
        f"🆔 {row['user_id']}\n"
        f"📱 {username}\n\n"
        f"💰 {row['balance']:,} {UNIT}"
    )


# =========================================================
# لیست موجودی کاربران
# =========================================================

async def admin_users_list(
    update,
    context
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    async with db_lock:

        with closing(get_db()) as db:

            rows = db.execute("""
                SELECT user_id,
                       username,
                       first_name,
                       balance
                FROM users
                ORDER BY balance DESC, user_id ASC
            """).fetchall()

    if not rows:

        await update.callback_query.message.reply_text(
            "📋 هنوز کاربری ثبت نشده."
        )

        return

    lines = [
        "📋 موجودی کاربران",
        ""
    ]

    for index, row in enumerate(
        rows,
        start=1
    ):

        if row["username"]:

            username = (
                "@"
                + row["username"]
            )

        else:

            username = "بدون یوزرنیم"

        lines.append(
            f"{index}_ "
            f"{row['balance']:,} "
            f"{UNIT} "
            f"{username}"
        )

    # تلگرام محدودیت طول پیام دارد
    chunks = []

    current = ""

    for line in lines:

        if len(current) + len(line) + 1 > 3800:

            chunks.append(current)

            current = line

        else:

            if current:
                current += "\n"

            current += line

    if current:
        chunks.append(current)

    for chunk in chunks:

        await update.callback_query.message.reply_text(
            chunk
        )


# =========================================================
# آمار مدیریت
# =========================================================

async def admin_stats_callback(
    update,
    context
):

    async with db_lock:

        with closing(get_db()) as db:

            users = db.execute(
                "SELECT COUNT(*) AS c FROM users"
            ).fetchone()["c"]

            total = db.execute(
                "SELECT COALESCE(SUM(balance),0) AS s FROM users"
            ).fetchone()["s"]

            referrals = db.execute(
                "SELECT COUNT(*) AS c FROM referrals"
            ).fetchone()["c"]

            withdrawals = db.execute("""
                SELECT COUNT(*) AS c
                FROM withdrawals
                WHERE status='pending'
            """).fetchone()["c"]

    await update.callback_query.message.reply_text(

        f"📊 آمار ربات\n\n"

        f"👤 کاربران: {users:,}\n"

        f"💰 مجموع موجودی: "
        f"{total:,} {UNIT}\n"

        f"👥 دعوت‌ها: {referrals:,}\n"

        f"💸 برداشت در انتظار: "
        f"{withdrawals:,}\n"

        f"🎮 بازی فعال: "
        f"{len(games):,}"
    )


# =========================================================
# لیست برداشت‌ها
# =========================================================

async def admin_withdrawals_callback(
    update,
    context
):

    async with db_lock:

        with closing(get_db()) as db:

            rows = db.execute("""
                SELECT id, user_id, amount, status, created_at
                FROM withdrawals
                ORDER BY id DESC
                LIMIT 20
            """).fetchall()

    if not rows:

        await update.callback_query.message.reply_text(
            "💸 درخواست برداشتی ثبت نشده."
        )

        return

    lines = [
        "💸 آخرین درخواست‌های برداشت",
        ""
    ]

    for row in rows:

        lines.append(
            f"#{row['id']} | "
            f"ID: {row['user_id']} | "
            f"{row['amount']:,} {UNIT} | "
            f"{row['status']}"
        )

    await update.callback_query.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# CALLBACK
# =========================================================

async def callback_handler(
    update,
    context
):

    query = update.callback_query

    data = query.data

    if data == "check_join":

        if await require_join(
            update,
            context
        ):

            await query.answer(
                "✅ عضویت تأیید شد."
            )

            await query.message.reply_text(
                "✅ حالا می‌توانی از ربات استفاده کنی.",
                reply_markup=main_menu()
            )

        return

    # پنل مدیریت
    if data.startswith("admin_"):

        if not is_owner(
            update.effective_user.id
        ):

            await query.answer(
                "⛔ دسترسی ندارید.",
                show_alert=True
            )

            return

        await query.answer()

        if data == "admin_users":

            await admin_users_list(
                update,
                context
            )

        elif data == "admin_stats":

            await admin_stats_callback(
                update,
                context
            )

        elif data == "admin_withdrawals":

            await admin_withdrawals_callback(
                update,
                context
            )

        return

    if not await require_join(
        update,
        context
    ):
        return

    await query.answer()

    if data == "balance":

        await show_balance(
            update,
            context
        )

    elif data == "account":

        user = update.effective_user

        async with db_lock:

            with closing(get_db()) as db:

                row = db.execute("""
                    SELECT *
                    FROM users
                    WHERE user_id=?
                """, (
                    user.id,
                )).fetchone()

        username = (
            "@"
            + row["username"]
            if row["username"]
            else "بدون یوزرنیم"
        )

        await query.message.reply_text(

            f"👤 حساب من\n\n"

            f"نام: {row['first_name']}\n"

            f"🆔 آیدی: {row['user_id']}\n"

            f"📱 یوزرنیم: {username}\n\n"

            f"💰 موجودی:\n"
            f"{row['balance']:,} {UNIT}"
        )

    elif data == "withdraw":

        await withdraw_start(
            update,
            context
        )

    elif data == "transfer_help":

        await query.message.reply_text(

            "🔁 انتقال داخل گپ\n\n"

            "روی پیام کاربر ریپلای کن و بنویس:\n\n"

            "انتقال 500"
        )

    elif data == "referral":

        user = update.effective_user

        me = await context.bot.get_me()

        link = (
            f"https://t.me/"
            f"{me.username}"
            f"?start=ref_{user.id}"
        )

        await query.message.reply_text(

            f"👥 زیرمجموعه\n\n"

            f"🎁 پاداش دعوت:\n"
            f"{REFERRAL_REWARD:,} {UNIT}\n\n"

            f"🔗 لینک دعوت:\n"
            f"{link}"
        )

    elif data == "help":

        await query.message.reply_text(
            f"برای راهنما /help را بزن."
        )


# =========================================================
# پیام‌های متنی
# =========================================================

async def text_handler(
    update,
    context
):

    if not update.message:
        return

    text = update.message.text

    if not text:
        return

    user = update.effective_user

    await ensure_user(user)

    # برداشت
    if context.user_data.get(
        "waiting_withdraw"
    ):

        handled = await process_withdraw(
            update,
            context
        )

        if handled:
            return

    text = clean_text(text)

    normalized = normalize_digits(
        text
    )

    lower = normalized.lower()

    # موجودی
    if lower in (
        "م",
        "موجودی",
        "balance"
    ):

        await show_balance(
            update,
            context
        )

        return

    # انتقال
    transfer_match = re.fullmatch(
        r"انتقال\s*([0-9]+)",
        normalized
    )

    if transfer_match:

        amount = int(
            transfer_match.group(1)
        )

        await do_transfer(
            update,
            amount
        )

        return

    # بازی فقط داخل گپ
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

    # -------------------------
    # فرد / زوج
    # -------------------------

    match = re.fullmatch(
        r"([0-9]+)\s*(فرد|زوج)",
        normalized
    )

    if match:

        amount = int(
            match.group(1)
        )

        choice = match.group(2)

        if amount < MIN_GAME_AMOUNT:

            await update.message.reply_text(
                f"❌ حداقل بازی "
                f"{MIN_GAME_AMOUNT:,} {UNIT} است."
            )

            return

        await even_odd_game(
            update,
            context,
            amount,
            choice
        )

        return

    # -------------------------
    # بولینگ / تاس / دارت
    # -------------------------

    match = re.fullmatch(
        r"1\s*(بولینگ|تاس|دارت)\s*([0-9]+)",
        normalized
    )

    if match:

        game_type = match.group(1)

        amount = int(
            match.group(2)
        )

        if amount < MIN_GAME_AMOUNT:

            await update.message.reply_text(
                f"❌ حداقل بازی "
                f"{MIN_GAME_AMOUNT:,} {UNIT} است."
            )

            return

        await create_game(
            update,
            context,
            game_type,
            amount
        )

        return


# =========================================================
# COMMANDS
# =========================================================

async def help_command(update, context):
    await show_help(update, context)


async def balance_command(update, context):
    await show_balance(update, context)


async def referral_command(update, context):
    await referral(update, context)


# =========================================================
# START BOT
# =========================================================

async def post_init(application):

    init_db()

    logger.info(
        "SQLite database initialized."
    )


async def post_shutdown(application):

    logger.info(
        "Bot stopped."
    )


def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN تنظیم نشده است."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # commands
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

    # admin
    application.add_handler(
        CommandHandler(
            "admin",
            admin
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

    # callbacks
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
        "BET TeK bot is starting..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
