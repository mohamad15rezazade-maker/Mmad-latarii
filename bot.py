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
# CONFIG
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

WITHDRAW_CHANNEL = "@BET_Tekhhh"

DB_FILE = "bot.db"

UNIT = "DOGS موج بات"

MIN_GAME_AMOUNT = 100
MIN_WITHDRAW = 1000

WIN_REWARD = 180
REFERRAL_REWARD = 45

MAX_ACTIVE_GAMES = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("BET_TEK")

db_lock = asyncio.Lock()

# بازی‌های در انتظار/فعال
games = {}

# =========================================================
# DATABASE
# =========================================================

def get_db():
    db = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


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

            if row:

                db.execute(
                    """
                    UPDATE users
                    SET username=?, first_name=?
                    WHERE user_id=?
                    """,
                    (
                        user.username,
                        user.first_name or "کاربر",
                        user.id
                    )
                )

            else:

                db.execute(
                    """
                    INSERT INTO users
                    (
                        user_id,
                        username,
                        first_name,
                        balance,
                        referrer_id
                    )
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    (
                        user.id,
                        user.username,
                        user.first_name or "کاربر",
                        referrer_id
                    )
                )

            # پاداش دعوت فقط یک بار
            if referrer_id and referrer_id != user.id:

                already = db.execute(
                    """
                    SELECT id
                    FROM referrals
                    WHERE referred_id=?
                    """,
                    (user.id,)
                ).fetchone()

                if not already:

                    referrer = db.execute(
                        """
                        SELECT user_id
                        FROM users
                        WHERE user_id=?
                        """,
                        (referrer_id,)
                    ).fetchone()

                    if referrer:

                        db.execute(
                            """
                            INSERT INTO referrals
                            (referrer_id, referred_id, reward)
                            VALUES (?, ?, ?)
                            """,
                            (
                                referrer_id,
                                user.id,
                                REFERRAL_REWARD
                            )
                        )

                        db.execute(
                            """
                            UPDATE users
                            SET balance=balance+?
                            WHERE user_id=?
                            """,
                            (
                                REFERRAL_REWARD,
                                referrer_id
                            )
                        )

                        db.execute(
                            """
                            UPDATE users
                            SET referrer_id=?
                            WHERE user_id=?
                            """,
                            (
                                referrer_id,
                                user.id
                            )
                        )

            db.commit()


async def get_user(user_id):

    async with db_lock:
        with closing(get_db()) as db:

            return db.execute(
                """
                SELECT *
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()


async def get_balance(user_id):

    async with db_lock:
        with closing(get_db()) as db:

            row = db.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            return int(row["balance"]) if row else 0


async def add_balance(user_id, amount):

    async with db_lock:
        with closing(get_db()) as db:

            db.execute(
                """
                UPDATE users
                SET balance=balance+?
                WHERE user_id=?
                """,
                (amount, user_id)
            )

            db.commit()


async def remove_balance(user_id, amount):

    async with db_lock:
        with closing(get_db()) as db:

            row = db.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if not row:
                return False

            balance = int(row["balance"])

            if balance < amount:
                return False

            db.execute(
                """
                UPDATE users
                SET balance=balance-?
                WHERE user_id=?
                """,
                (amount, user_id)
            )

            db.commit()

            return True


# =========================================================
# DIGITS
# =========================================================

def normalize_digits(text):

    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )

    return str(text).translate(table)


def parse_number(text):

    text = normalize_digits(text)
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
# JOIN
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


async def check_member(bot, user_id, chat_username):

    try:

        member = await bot.get_chat_member(
            chat_username,
            user_id
        )

        return member.status in (
            "member",
            "administrator",
            "creator"
        )

    except Exception as e:

        logger.warning(
            "Membership error: %s",
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
        "⛔ برای استفاده از ربات باید "
        "در کانال و گپ عضو باشی."
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
# BALANCE
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
# GAME SETTINGS
# =========================================================

GAME_EMOJI = {
    "تاس": "🎲",
    "بولینگ": "🎳",
    "دارت": "🎯"
}


def active_game_count(chat_id):

    return sum(
        1
        for game in games.values()
        if game["chat_id"] == chat_id
        and game["status"] not in (
            "finished",
            "cancelled"
        )
    )


def make_game_id():

    return secrets.token_hex(6)


def game_choice_keyboard(game_id):

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🎮 بازی با ربات",
                callback_data=f"game_bot:{game_id}"
            )
        ],

        [
            InlineKeyboardButton(
                "👥 بازی با دوستان",
                callback_data=f"game_friend:{game_id}"
            )
        ],

        [
            InlineKeyboardButton(
                "❌ لغو بازی",
                callback_data=f"game_cancel:{game_id}"
            )
        ]

    ])


def join_game_keyboard(game_id):

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🎮 پیوستن به بازی",
                callback_data=f"game_join:{game_id}"
            )
        ],

        [
            InlineKeyboardButton(
                "❌ لغو بازی",
                callback_data=f"game_cancel:{game_id}"
            )
        ]

    ])


# =========================================================
# CREATE GAME
# =========================================================

async def create_pending_game(
    update,
    context,
    game_type,
    amount,
    count=1
):

    chat = update.effective_chat
    user = update.effective_user

    # حداکثر 5 بازی همزمان
    current = active_game_count(
        chat.id
    )

    if current + count > MAX_ACTIVE_GAMES:

        available = MAX_ACTIVE_GAMES - current

        if available <= 0:

            await update.message.reply_text(
                "❌ در حال حاضر ۵ بازی فعال است."
            )

        else:

            await update.message.reply_text(
                f"❌ فقط {available} جای بازی باقی مانده."
            )

        return

    # حداقل مبلغ
    if amount < MIN_GAME_AMOUNT:

        await update.message.reply_text(
            f"❌ حداقل مبلغ هر بازی "
            f"{MIN_GAME_AMOUNT:,} {UNIT} است."
        )

        return

    # مهم:
    # برای چند بازی، مجموع مبلغ را بررسی می‌کنیم.
    total = amount * count

    balance = await get_balance(
        user.id
    )

    if balance < total:

        await update.message.reply_text(

            f"❌ موجودی کافی نیست.\n\n"

            f"💰 موجودی شما: "
            f"{balance:,} {UNIT}\n"

            f"💠 مبلغ لازم برای "
            f"{count} بازی: "
            f"{total:,} {UNIT}"
        )

        return

    # برای هر بازی یک شناسه جدا
    created = []

    for i in range(count):

        game_id = make_game_id()

        games[game_id] = {

            "id": game_id,

            "chat_id": chat.id,

            "type": game_type,

            "amount": amount,

            "creator_id": user.id,

            "creator_name": user.first_name,

            "creator_username": user.username,

            "opponent_id": None,

            "opponent_name": None,

            "creator_value": None,

            "opponent_value": None,

            "mode": None,

            "status": "choosing",

            "created": asyncio.get_running_loop().time()
        }

        created.append(
            game_id
        )

    # اگر فقط یک بازی باشد
    if count == 1:

        game = games[created[0]]

        await update.message.reply_text(

            f"🎮 بازی {game_type} آماده شد.\n\n"

            f"💠 مبلغ بازی: "
            f"{amount:,} {UNIT}\n\n"

            f"یکی از گزینه‌ها را انتخاب کن:",

            reply_markup=game_choice_keyboard(
                game["id"]
            )
        )

        return

    # اگر چند بازی باشد
    # هر بازی جداگانه پیام می‌گیرد
    for number, game_id in enumerate(
        created,
        start=1
    ):

        game = games[game_id]

        await update.message.reply_text(

            f"🎮 بازی شماره {number}\n\n"

            f"🎯 نوع: {game_type}\n"

            f"💠 مبلغ: "
            f"{amount:,} {UNIT}\n\n"

            f"یکی از گزینه‌ها را انتخاب کن:",

            reply_markup=game_choice_keyboard(
                game_id
            )
        )


# =========================================================
# START BOT GAME
# =========================================================

async def start_bot_game(
    query,
    context,
    game
):

    user_id = query.from_user.id

    # فقط سازنده
    if user_id != game["creator_id"]:

        await query.answer(
            "⛔ فقط سازنده بازی می‌تواند انتخاب کند.",
            show_alert=True
        )

        return

    # دوباره موجودی چک شود
    balance = await get_balance(
        user_id
    )

    amount = game["amount"]

    if balance < amount:

        games.pop(
            game["id"],
            None
        )

        await query.answer(
            "موجودی کافی نیست.",
            show_alert=True
        )

        await query.message.edit_text(
            "❌ بازی لغو شد چون موجودی کافی نیست."
        )

        return

    # مبلغ از موجودی کم می‌شود
    success = await remove_balance(
        user_id,
        amount
    )

    if not success:

        await query.answer(
            "موجودی کافی نیست.",
            show_alert=True
        )

        return

    game["mode"] = "bot"
    game["status"] = "waiting_creator"

    await query.answer(
        "🎮 بازی با ربات شروع شد."
    )

    await query.message.edit_text(

        f"🎮 بازی با ربات\n\n"

        f"💠 مبلغ بازی: "
        f"{amount:,} {UNIT}\n\n"

        f"👤 {game['creator_name']} اول بازی می‌کند.\n\n"

        f"🎯 حالا {game['type']} را بینداز."
    )


# =========================================================
# START FRIEND GAME
# =========================================================

async def start_friend_game(
    query,
    context,
    game
):

    user_id = query.from_user.id

    if user_id != game["creator_id"]:

        await query.answer(
            "⛔ فقط سازنده بازی می‌تواند این گزینه را بزند.",
            show_alert=True
        )

        return

    balance = await get_balance(
        user_id
    )

    amount = game["amount"]

    if balance < amount:

        games.pop(
            game["id"],
            None
        )

        await query.answer(
            "موجودی کافی نیست.",
            show_alert=True
        )

        await query.message.edit_text(
            "❌ بازی لغو شد چون موجودی کافی نیست."
        )

        return

    game["mode"] = "friend"
    game["status"] = "waiting_opponent"

    await query.answer(
        "👥 بازی منتظر دوست است."
    )

    await query.message.edit_text(

        f"👥 بازی با دوستان\n\n"

        f"👤 سازنده: "
        f"{game['creator_name']}\n"

        f"💠 مبلغ بازی: "
        f"{amount:,} {UNIT}\n\n"

        f"⏳ منتظر یک نفر دیگر هستیم.\n\n"

        f"یک کاربر دیگر باید روی "
        f"«🎮 پیوستن به بازی» بزند."
    )

    # پیام جدا برای ورود بازیکن
    await context.bot.send_message(

        game["chat_id"],

        f"👥 یک بازی {game['type']} منتظر بازیکن است.\n\n"
        f"💠 مبلغ: {amount:,} {UNIT}\n\n"
        f"اگر می‌خواهی وارد بازی شوی، دکمه زیر را بزن.",

        reply_markup=join_game_keyboard(
            game["id"]
        )
    )


# =========================================================
# JOIN FRIEND GAME
# =========================================================

async def join_friend_game(
    query,
    context,
    game
):

    user = query.from_user

    # سازنده نمی‌تواند خودش وارد شود
    if user.id == game["creator_id"]:

        await query.answer(
            "❌ خودت نمی‌توانی وارد بازی خودت شوی.",
            show_alert=True
        )

        return

    if game["status"] != "waiting_opponent":

        await query.answer(
            "❌ این بازی دیگر قابل ورود نیست.",
            show_alert=True
        )

        return

    await ensure_user(user)

    amount = game["amount"]

    balance = await get_balance(
        user.id
    )

    if balance < amount:

        await query.answer(
            "❌ موجودی کافی نیست.",
            show_alert=True
        )

        return

    # مبلغ بازیکن دوم هم کم می‌شود
    success = await remove_balance(
        user.id,
        amount
    )

    if not success:

        await query.answer(
            "❌ موجودی کافی نیست.",
            show_alert=True
        )

        return

    game["opponent_id"] = user.id
    game["opponent_name"] = user.first_name
    game["status"] = "waiting_creator"

    await query.answer(
        "✅ وارد بازی شدی."
    )

    await query.message.edit_text(
        "✅ بازیکن وارد بازی شد."
    )

    await context.bot.send_message(

        game["chat_id"],

        f"🎮 بازیکن دوم وارد شد!\n\n"

        f"👤 بازیکن اول: "
        f"{game['creator_name']}\n"

        f"👤 بازیکن دوم: "
        f"{game['opponent_name']}\n\n"

        f"💠 مبلغ هر بازیکن: "
        f"{amount:,} {UNIT}\n\n"

        f"🎯 اول نوبت "
        f"{game['creator_name']} است.\n\n"

        f"{GAME_EMOJI[game['type']]} "
        f"{game['type']} را بینداز."
    )


# =========================================================
# CANCEL GAME
# =========================================================

async def cancel_game(
    query,
    context,
    game
):

    user_id = query.from_user.id

    if user_id != game["creator_id"]:

        await query.answer(
            "⛔ فقط سازنده می‌تواند بازی را لغو کند.",
            show_alert=True
        )

        return

    games.pop(
        game["id"],
        None
    )

    await query.answer(
        "❌ بازی لغو شد."
    )

    try:

        await query.message.edit_text(
            "❌ این بازی توسط سازنده لغو شد."
        )

    except:

        pass


# =========================================================
# DICE HANDLER
# =========================================================

async def dice_handler(
    update,
    context
):

    message = update.message

    if not message or not message.dice:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    emoji = message.dice.emoji
    value = message.dice.value

    reverse = {
        "🎲": "تاس",
        "🎳": "بولینگ",
        "🎯": "دارت"
    }

    game_type = reverse.get(
        emoji
    )

    if not game_type:
        return

    selected = None

    for game in list(games.values()):

        if game["chat_id"] != chat_id:
            continue

        if game["type"] != game_type:
            continue

        if game["status"] != "waiting_creator":
            continue

        # بازیکن اول
        if (
            game["creator_value"] is None
            and user_id == game["creator_id"]
        ):

            selected = game
            break

        # بازیکن دوم
        if (
            game["mode"] == "friend"
            and game["creator_value"] is not None
            and game["opponent_value"] is None
            and user_id == game["opponent_id"]
        ):

            selected = game
            break

    if not selected:
        return

    game = selected

    # -----------------------------------------
    # پرتاب بازیکن اول
    # -----------------------------------------

    if (
        game["creator_value"] is None
        and user_id == game["creator_id"]
    ):

        game["creator_value"] = value

        # ربات
        if game["mode"] == "bot":

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

        # دوست
        await context.bot.send_message(

            chat_id,

            f"✅ عدد {game['creator_name']}: "
            f"{value}\n\n"

            f"🎯 حالا نوبت "
            f"{game['opponent_name']} است."
        )

        return

    # -----------------------------------------
    # پرتاب بازیکن دوم
    # -----------------------------------------

    if (
        game["mode"] == "friend"
        and game["opponent_id"] == user_id
        and game["opponent_value"] is None
    ):

        game["opponent_value"] = value

        await finish_game(
            context,
            game
        )


# =========================================================
# FINISH GAME
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
    opponent_name = (
        game["opponent_name"]
        if game["opponent_name"]
        else "ربات"
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

    text = (
        f"🏁 نتیجه بازی {game['type']}\n\n"

        f"👤 {creator_name}: "
        f"{creator_value}\n"

        f"🤖 {opponent_name}: "
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

            f"🏆 برنده: "
            f"{winner_name}\n\n"

            f"🎁 پاداش:\n"
            f"+{WIN_REWARD:,} {UNIT}\n\n"

            f"💰 موجودی جدید:\n"
            f"{balance:,} {UNIT}"
        )

    else:

        # مساوی:
        # مبلغ هر دو نفر برگردانده می‌شود
        await add_balance(
            creator_id,
            game["amount"]
        )

        if game["mode"] == "friend" and opponent_id:

            await add_balance(
                opponent_id,
                game["amount"]
            )

        text += (
            "🤝 بازی مساوی شد.\n\n"
            "💰 مبلغ بازی به بازیکنان برگشت داده شد."
        )

    games.pop(
        game["id"],
        None
    )

    await context.bot.send_message(
        game["chat_id"],
        text
    )


# =========================================================
# EVEN / ODD
# =========================================================

async def even_odd_game(
    update,
    context,
    amount,
    choice
):

    user = update.effective_user

    balance = await get_balance(
        user.id
    )

    if balance < amount:

        await update.message.reply_text(
            f"❌ موجودی کافی نیست.\n\n"
            f"💰 موجودی: "
            f"{balance:,} {UNIT}\n"
            f"💠 مبلغ بازی: "
            f"{amount:,} {UNIT}"
        )

        return

    # مبلغ بازی کم شود
    success = await remove_balance(
        user.id,
        amount
    )

    if not success:

        await update.message.reply_text(
            "❌ موجودی کافی نیست."
        )

        return

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

        result_text = (
            f"🏆 برنده شدی!\n"
            f"🎁 +{WIN_REWARD:,} {UNIT}"
        )

    else:

        result_text = (
            "❌ این بار برنده نشدی."
        )

    new_balance = await get_balance(
        user.id
    )

    await context.bot.send_message(

        update.effective_chat.id,

        f"🎲 بازی فرد / زوج\n\n"

        f"👤 {user.first_name}\n"

        f"💠 مبلغ بازی: "
        f"{amount:,} {UNIT}\n"

        f"🎯 انتخاب: {choice}\n"

        f"🎲 عدد تاس: {value}\n"

        f"📌 نتیجه: {result}\n\n"

        f"{result_text}\n\n"

        f"💰 موجودی: "
        f"{new_balance:,} {UNIT}"
    )


# =========================================================
# TRANSFER
# =========================================================

async def do_transfer(
    update,
    amount
):

    sender = update.effective_user

    if update.effective_chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP
    ):

        await update.message.reply_text(
            "❌ انتقال را داخل گپ انجام بده."
        )

        return

    reply = update.message.reply_to_message

    if not reply:

        await update.message.reply_text(
            "❌ روی پیام کاربر ریپلای کن."
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

    await ensure_user(receiver)

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

    await add_balance(
        receiver.id,
        amount
    )

    async with db_lock:

        with closing(get_db()) as db:

            db.execute(
                """
                INSERT INTO transfers
                (sender_id, receiver_id, amount)
                VALUES (?, ?, ?)
                """,
                (
                    sender.id,
                    receiver.id,
                    amount
                )
            )

            db.commit()

    sender_balance = await get_balance(
        sender.id
    )

    await update.message.reply_text(

        f"✅ انتقال انجام شد.\n\n"

        f"👤 گیرنده: "
        f"{receiver.first_name}\n"

        f"💰 مقدار: "
        f"{amount:,} {UNIT}\n\n"

        f"💳 موجودی شما:\n"
        f"{sender_balance:,} {UNIT}"
    )


# =========================================================
# WITHDRAW
# =========================================================

async def process_withdraw(
    update,
    context
):

    if update.effective_chat.type != ChatType.PRIVATE:
        return False

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

        context.user_data.pop(
            "waiting_withdraw",
            None
        )

        return True

    async with db_lock:

        with closing(get_db()) as db:

            cur = db.execute(
                """
                INSERT INTO withdrawals
                (user_id, amount, status)
                VALUES (?, ?, 'pending')
                """,
                (
                    user.id,
                    amount
                )
            )

            request_id = cur.lastrowid

            db.commit()

    try:

        await context.bot.send_message(

            WITHDRAW_CHANNEL,

            f"🔔 درخواست برداشت\n\n"

            f"👤 آیدی:\n"
            f"`{user.id}`\n\n"

            f"📱 یوزرنیم:\n"
            f"@{user.username if user.username else 'ندارد'}\n\n"

            f"💰 مقدار:\n"
            f"{amount:,} {UNIT}\n\n"

            f"🆔 درخواست:\n"
            f"`{request_id}`\n\n"

            f"📌 وضعیت: در انتظار بررسی\n\n"

            f"⚠️ این واحد، داخلی و غیرنقدی است.",

            parse_mode="Markdown"
        )

    except Exception as e:

        logger.error(
            "Withdrawal error: %s",
            e
        )

    context.user_data.pop(
        "waiting_withdraw",
        None
    )

    await update.message.reply_text(

        "✅ برداشت شما در کانال ثبت شد.\n\n"

        f"💰 مقدار: "
        f"{amount:,} {UNIT}\n"

        f"🆔 شماره درخواست: "
        f"{request_id}"
    )

    return True


# =========================================================
# REFERRAL
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

        f"👥 زیرمجموعه\n\n"

        f"🎁 پاداش هر دعوت:\n"
        f"{REFERRAL_REWARD:,} {UNIT}\n\n"

        f"🔗 لینک دعوت:\n"
        f"{link}"
    )


# =========================================================
# ADMIN
# =========================================================

def is_owner(user_id):
    return user_id in OWNER_IDS


def admin_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "➕ افزایش موجودی",
                callback_data="admin_add"
            ),
            InlineKeyboardButton(
                "➖ کسر موجودی",
                callback_data="admin_sub"
            )
        ],

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
            ),
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
            "/add ID AMOUNT"
        )

        return

    try:

        user_id = int(
            normalize_digits(
                context.args[0]
            )
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

    target = await get_user(
        user_id
    )

    if not target:

        await update.message.reply_text(
            "❌ کاربر پیدا نشد."
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

        f"👤 {target['first_name']}\n"
        f"🆔 {user_id}\n\n"

        f"➕ {amount:,} {UNIT}\n\n"

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
            "/sub ID AMOUNT"
        )

        return

    try:

        user_id = int(
            normalize_digits(
                context.args[0]
            )
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

    target = await get_user(
        user_id
    )

    if not target:

        await update.message.reply_text(
            "❌ کاربر پیدا نشد."
        )

        return

    success = await remove_balance(
        user_id,
        amount
    )

    if not success:

        await update.message.reply_text(
            "❌ موجودی کافی نیست."
        )

        return

    balance = await get_balance(
        user_id
    )

    await update.message.reply_text(

        f"✅ موجودی کم شد.\n\n"

        f"👤 {target['first_name']}\n"
        f"🆔 {user_id}\n\n"

        f"➖ {amount:,} {UNIT}\n\n"

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
            normalize_digits(
                context.args[0]
            )
        )

    except:

        await update.message.reply_text(
            "❌ آیدی اشتباه است."
        )

        return

    target = await get_user(
        user_id
    )

    if not target:

        await update.message.reply_text(
            "❌ کاربر پیدا نشد."
        )

        return

    username = (
        f"@{target['username']}"
        if target["username"]
        else "بدون یوزرنیم"
    )

    await update.message.reply_text(

        f"👤 {target['first_name']}\n"
        f"🆔 {user_id}\n"
        f"📱 {username}\n\n"
        f"💰 {target['balance']:,} {UNIT}"
    )


async def admin_users_list(
    update,
    context
):

    async with db_lock:

        with closing(get_db()) as db:

            rows = db.execute(
                """
                SELECT
                    user_id,
                    username,
                    first_name,
                    balance
                FROM users
                ORDER BY balance DESC, user_id ASC
                """
            ).fetchall()

    if not rows:

        await update.callback_query.message.reply_text(
            "📋 کاربری وجود ندارد."
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

        username = (
            f"@{row['username']}"
            if row["username"]
            else "بدون یوزرنیم"
        )

        lines.append(
            f"{index}_ "
            f"{row['balance']:,} "
            f"{UNIT} "
            f"{username}"
        )

    current = ""

    for line in lines:

        if len(current) + len(line) > 3800:

            await update.callback_query.message.reply_text(
                current
            )

            current = ""

        current += line + "\n"

    if current:

        await update.callback_query.message.reply_text(
            current
        )


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
                """
                SELECT COALESCE(SUM(balance),0) AS s
                FROM users
                """
            ).fetchone()["s"]

            referrals = db.execute(
                """
                SELECT COUNT(*) AS c
                FROM referrals
                """
            ).fetchone()["c"]

            withdrawals = db.execute(
                """
                SELECT COUNT(*) AS c
                FROM withdrawals
                WHERE status='pending'
                """
            ).fetchone()["c"]

    await update.callback_query.message.reply_text(

        f"📊 آمار\n\n"

        f"👤 کاربران: {users:,}\n"

        f"💰 مجموع موجودی: "
        f"{total:,} {UNIT}\n"

        f"👥 دعوت‌ها: "
        f"{referrals:,}\n"

        f"💸 برداشت‌های در انتظار: "
        f"{withdrawals:,}\n"

        f"🎮 بازی‌های فعال: "
        f"{len(games):,}"
    )


async def admin_withdrawals_callback(
    update,
    context
):

    async with db_lock:

        with closing(get_db()) as db:

            rows = db.execute(
                """
                SELECT
                    id,
                    user_id,
                    amount,
                    status
                FROM withdrawals
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()

    if not rows:

        await update.callback_query.message.reply_text(
            "💸 درخواست برداشتی وجود ندارد."
        )

        return

    text = "💸 درخواست‌های اخیر\n\n"

    for row in rows:

        text += (
            f"#{row['id']} | "
            f"ID {row['user_id']} | "
            f"{row['amount']:,} {UNIT} | "
            f"{row['status']}\n"
        )

    await update.callback_query.message.reply_text(
        text
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

    # -------------------------
    # عضویت
    # -------------------------

    if data == "check_join":

        if await require_join(
            update,
            context
        ):

            await query.answer(
                "✅ عضویت تأیید شد."
            )

            await query.message.reply_text(
                "✅ آماده‌ای.",
                reply_markup=main_menu()
            )

        return

    # -------------------------
    # بازی
    # -------------------------

    if data.startswith("game_"):

        parts = data.split(":")

        action = parts[0]
        game_id = parts[1]

        game = games.get(
            game_id
        )

        if not game:

            await query.answer(
                "❌ این بازی دیگر وجود ندارد.",
                show_alert=True
            )

            return

        if action == "game_bot":

            await start_bot_game(
                query,
                context,
                game
            )

            return

        if action == "game_friend":

            await start_friend_game(
                query,
                context,
                game
            )

            return

        if action == "game_join":

            await join_friend_game(
                query,
                context,
                game
            )

            return

        if action == "game_cancel":

            await cancel_game(
                query,
                context,
                game
            )

            return

    # -------------------------
    # پنل مدیریت
    # -------------------------

    if data.startswith("admin_"):

        if not is_owner(
            query.from_user.id
        ):

            await query.answer(
                "⛔ دسترسی ندارید.",
                show_alert=True
            )

            return

        await query.answer()

        if data == "admin_add":

            context.user_data[
                "admin_action"
            ] = "add"

            await query.message.reply_text(

                "➕ افزایش موجودی\n\n"

                "به این شکل بفرست:\n\n"

                "ID مقدار\n\n"

                "مثال:\n"
                "123456789 500"
            )

            return

        if data == "admin_sub":

            context.user_data[
                "admin_action"
            ] = "sub"

            await query.message.reply_text(

                "➖ کسر موجودی\n\n"

                "به این شکل بفرست:\n\n"

                "ID مقدار\n\n"

                "مثال:\n"
                "123456789 500"
            )

            return

        if data == "admin_users":

            await admin_users_list(
                update,
                context
            )

            return

        if data == "admin_stats":

            await admin_stats_callback(
                update,
                context
            )

            return

        if data == "admin_withdrawals":

            await admin_withdrawals_callback(
                update,
                context
            )

            return

    # -------------------------
    # سایر دکمه‌ها
    # -------------------------

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

        return

    if data == "account":

        user = await get_user(
            query.from_user.id
        )

        username = (
            f"@{user['username']}"
            if user["username"]
            else "بدون یوزرنیم"
        )

        await query.message.reply_text(

            f"👤 حساب من\n\n"

            f"نام: {user['first_name']}\n"

            f"🆔 آیدی: {user['user_id']}\n"

            f"📱 یوزرنیم: {username}\n\n"

            f"💰 موجودی:\n"
            f"{user['balance']:,} {UNIT}"
        )

        return

    if data == "withdraw":

        if query.message.chat.type != ChatType.PRIVATE:

            await query.message.reply_text(
                "💸 برای برداشت به پیوی ربات برو."
            )

            return

        context.user_data[
            "waiting_withdraw"
        ] = True

        await query.message.reply_text(

            f"💸 درخواست برداشت\n\n"

            f"حداقل: "
            f"{MIN_WITHDRAW:,} {UNIT}\n\n"

            f"مقدار را بفرست."
        )

        return

    if data == "transfer_help":

        await query.message.reply_text(

            "🔁 انتقال داخل گپ\n\n"

            "روی پیام کاربر ریپلای کن و بنویس:\n\n"

            "انتقال 500"
        )

        return

    if data == "referral":

        user = query.from_user

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

            f"🔗 لینک:\n"
            f"{link}"
        )

        return

    if data == "help":

        await query.message.reply_text(
            "برای راهنما /help را بزن."
        )

        return


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(
    update,
    context
):

    if not update.message:
        return

    if not update.message.text:
        return

    user = update.effective_user

    await ensure_user(user)

    text = clean_text(
        update.message.text
    )

    normalized = normalize_digits(
        text
    )

    # =====================================================
    # ADMIN BUTTON INPUT
    # =====================================================

    admin_action = context.user_data.get(
        "admin_action"
    )

    if (
        admin_action
        and update.effective_chat.type == ChatType.PRIVATE
        and is_owner(user.id)
    ):

        parts = normalized.split()

        if len(parts) != 2:

            await update.message.reply_text(
                "❌ فرمت اشتباه است.\n\n"
                "مثال:\n"
                "123456789 500"
            )

            return

        try:

            target_id = int(
                parts[0]
            )

            amount = int(
                parts[1]
            )

        except:

            await update.message.reply_text(
                "❌ آیدی و مقدار باید عدد باشند."
            )

            return

        if amount <= 0:

            await update.message.reply_text(
                "❌ مقدار باید بیشتر از صفر باشد."
            )

            return

        target = await get_user(
            target_id
        )

        if not target:

            await update.message.reply_text(
                "❌ کاربر ثبت نشده."
            )

            context.user_data.pop(
                "admin_action",
                None
            )

            return

        if admin_action == "add":

            await add_balance(
                target_id,
                amount
            )

            result = (
                f"➕ اضافه شد: "
                f"{amount:,} {UNIT}"
            )

        else:

            success = await remove_balance(
                target_id,
                amount
            )

            if not success:

                await update.message.reply_text(
                    "❌ موجودی کافی نیست."
                )

                context.user_data.pop(
                    "admin_action",
                    None
                )

                return

            result = (
                f"➖ کسر شد: "
                f"{amount:,} {UNIT}"
            )

        new_balance = await get_balance(
            target_id
        )

        context.user_data.pop(
            "admin_action",
            None
        )

        await update.message.reply_text(

            f"✅ انجام شد.\n\n"

            f"👤 {target['first_name']}\n"
            f"🆔 {target_id}\n\n"

            f"{result}\n\n"

            f"💰 موجودی جدید:\n"
            f"{new_balance:,} {UNIT}"
        )

        return

    # =====================================================
    # WITHDRAW ONLY PRIVATE
    # =====================================================

    if (
        update.effective_chat.type == ChatType.PRIVATE
        and context.user_data.get(
            "waiting_withdraw"
        )
    ):

        handled = await process_withdraw(
            update,
            context
        )

        if handled:
            return

    # =====================================================
    # BALANCE
    # =====================================================

    if normalized.lower() in (
        "م",
        "موجودی",
        "balance"
    ):

        await show_balance(
            update,
            context
        )

        return

    # =====================================================
    # TRANSFER
    # =====================================================

    match = re.fullmatch(
        r"انتقال\s*([0-9]+)",
        normalized
    )

    if match:

        amount = int(
            match.group(1)
        )

        await do_transfer(
            update,
            amount
        )

        return

    # =====================================================
    # بازی‌ها فقط داخل گپ
    # =====================================================

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

    # =====================================================
    # فرد / زوج
    # مثال:
    # 100 فرد
    # 100 زوج
    # =====================================================

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

    # =====================================================
    # بازی تاس / بولینگ / دارت
    #
    # مثال:
    # 1 تاس 100
    # 5 تاس 100
    # 3 بولینگ 200
    # =====================================================

    match = re.fullmatch(
        r"([0-9]+)\s*(بولینگ|تاس|دارت)\s*([0-9]+)",
        normalized
    )

    if match:

        count = int(
            match.group(1)
        )

        game_type = match.group(2)

        amount = int(
            match.group(3)
        )

        if count < 1:

            await update.message.reply_text(
                "❌ تعداد بازی باید حداقل ۱ باشد."
            )

            return

        # بیشتر از 5 بازی در یک پیام قبول نمی‌شود
        if count > MAX_ACTIVE_GAMES:

            await update.message.reply_text(
                f"❌ حداکثر تعداد بازی "
                f"{MAX_ACTIVE_GAMES} تا است."
            )

            return

        if amount < MIN_GAME_AMOUNT:

            await update.message.reply_text(
                f"❌ حداقل مبلغ هر بازی "
                f"{MIN_GAME_AMOUNT:,} {UNIT} است."
            )

            return

        await create_pending_game(
            update,
            context,
            game_type,
            amount,
            count
        )

        return


# =========================================================
# COMMANDS
# =========================================================

async def help_command(
    update,
    context
):

    await update.message.reply_text(

        f"""
📚 راهنمای بازی

🎲 فرد:
100 فرد

🎲 زوج:
100 زوج

🎮 تاس:
1 تاس 100

🎳 بولینگ:
1 بولینگ 100

🎯 دارت:
1 دارت 100

🔥 چند بازی همزمان:
5 تاس 100

یعنی ۵ بازی جداگانه،
هرکدام با مبلغ ۱۰۰.

حداکثر بازی فعال:
{MAX_ACTIVE_GAMES}

حداقل مبلغ هر بازی:
{MIN_GAME_AMOUNT:,} {UNIT}

🏆 پاداش برد:
{WIN_REWARD:,} {UNIT}

👥 بازی دوستان:
بعد از انتخاب بازی با دوستان،
یک نفر دیگر باید وارد شود.

⚠️ {UNIT} واحد داخلی و غیرنقدی ربات است.
"""
    )


async def balance_command(
    update,
    context
):

    await show_balance(
        update,
        context
    )


async def referral_command(
    update,
    context
):

    await referral(
        update,
        context
    )


# =========================================================
# APPLICATION
# =========================================================

async def post_init(application):

    init_db()

    logger.info(
        "Database initialized."
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

    # Withdraw
    application.add_handler(
        CommandHandler(
            "withdraw",
            lambda update, context:
            withdraw_command(update, context)
        )
    )

    # Admin
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

    # Buttons
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # Dice / Bowling / Dart
    application.add_handler(
        MessageHandler(
            filters.Dice.ALL,
            dice_handler
        )
    )

    # Text
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


# =========================================================
# WITHDRAW COMMAND
# =========================================================

async def withdraw_command(
    update,
    context
):

    if update.effective_chat.type != ChatType.PRIVATE:

        await update.message.reply_text(
            "💸 برای برداشت به پیوی ربات برو."
        )

        return

    context.user_data[
        "waiting_withdraw"
    ] = True

    await update.message.reply_text(

        f"💸 درخواست برداشت\n\n"

        f"حداقل برداشت:\n"
        f"{MIN_WITHDRAW:,} {UNIT}\n\n"

        f"مقدار را بفرست."
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
