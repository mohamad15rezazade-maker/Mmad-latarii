# -*- coding: utf-8 -*-

import os
import re
import sqlite3
import asyncio
import secrets
import logging
from contextlib import closing

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# برد = مبلغ × 1.8
WIN_MULTIPLIER = 1.8

REFERRAL_REWARD = 45

# حداکثر تعداد پرتاب در هر بازی
MAX_ROLL_COUNT = 3

# آستانه گل شدن در بسکتبال (تاس >= 3 = گل)
BASKETBALL_GOAL_MIN = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("BET_TEK")

db_lock = asyncio.Lock()

# =========================================================
# GAMES
# =========================================================

games = {}

GAME_EMOJI = {
    "تاس": "🎲",
    "بولینگ": "🎳",
    "دارت": "🎯",
    "بسکتبال": "🎲",
}

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

                    ref = db.execute(
                        """
                        SELECT user_id
                        FROM users
                        WHERE user_id=?
                        """,
                        (referrer_id,)
                    ).fetchone()

                    if ref:

                        db.execute(
                            """
                            INSERT INTO referrals
                            (
                                referrer_id,
                                referred_id,
                                reward
                            )
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

            if not row:
                return 0

            return int(row["balance"])


async def add_balance(user_id, amount):

    async with db_lock:

        with closing(get_db()) as db:

            db.execute(
                """
                UPDATE users
                SET balance=balance+?
                WHERE user_id=?
                """,
                (
                    amount,
                    user_id
                )
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

            if int(row["balance"]) < amount:
                return False

            db.execute(
                """
                UPDATE users
                SET balance=balance-?
                WHERE user_id=?
                """,
                (
                    amount,
                    user_id
                )
            )

            db.commit()

            return True


# =========================================================
# NUMBER HELPERS
# =========================================================

def normalize_digits(text):

    return str(text).translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789"
        )
    )


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
# WIN REWARD
# =========================================================

def calculate_win_reward(amount):

    return int(amount * WIN_MULTIPLIER)


# =========================================================
# BASKETBALL HELPERS
# =========================================================

def is_basketball_goal(value):

    return value >= BASKETBALL_GOAL_MIN


def count_basketball_goals(rolls):

    return sum(
        1
        for value in rolls
        if is_basketball_goal(value)
    )


# =========================================================
# JOIN SYSTEM
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
            "Join check error: %s",
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

    else:

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

async def show_balance(update, context):

    user = update.effective_user

    balance = await get_balance(
        user.id
    )

    text = (
        f"💰 موجودی شما:\n\n"
        f"{balance:,} {UNIT}"
    )

    if update.callback_query:

        await update.callback_query.message.reply_text(
            text
        )

    else:

        await update.message.reply_text(
            text
        )


# =========================================================
# GAME SYSTEM
# =========================================================

def new_game_id():

    return secrets.token_hex(6)


def choice_keyboard(game_id):

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

async def create_game(
    update,
    context,
    count,
    game_type,
    amount
):

    chat_id = update.effective_chat.id
    user = update.effective_user

    if count > MAX_ROLL_COUNT:

        await update.message.reply_text(
            f"❌ حداکثر تعداد پرتاب {MAX_ROLL_COUNT} است."
        )

        return

    if count < 1:

        await update.message.reply_text(
            "❌ تعداد پرتاب باید حداقل ۱ باشد."
        )

        return

    if amount < MIN_GAME_AMOUNT:

        await update.message.reply_text(
            f"❌ حداقل مبلغ بازی "
            f"{MIN_GAME_AMOUNT:,} {UNIT} است."
        )

        return

    balance = await get_balance(
        user.id
    )

    if balance < amount:

        await update.message.reply_text(

            f"❌ موجودی کافی نیست.\n\n"

            f"💰 موجودی شما: "
            f"{balance:,} {UNIT}\n"

            f"💠 مبلغ بازی: "
            f"{amount:,} {UNIT}"
        )

        return

    game_id = new_game_id()

    games[game_id] = {

        "id": game_id,

        "chat_id": chat_id,

        "type": game_type,

        "emoji": GAME_EMOJI[game_type],

        "amount": amount,

        "roll_count": count,

        "creator_id": user.id,

        "creator_name": user.first_name,

        "opponent_id": None,

        "opponent_name": None,

        "creator_rolls": [],

        "opponent_rolls": [],

        "mode": None,

        "status": "choosing",

        "message_id": update.message.message_id
    }

    if game_type == "بسکتبال":

        extra = (
            f"🏀 قانون بسکتبال:\n"
            f"تاس ۳، ۴، ۵، ۶ = گل ✅\n"
            f"تاس ۱ یا ۲ = بیرون ❌\n"
            f"هرکی گل بیشتر = برنده\n"
            f"اگه صفر گل بزنی = باختی\n\n"
        )

    else:

        extra = ""

    await update.message.reply_text(

        f"🎮 بازی جدید ساخته شد\n\n"

        f"🎯 نوع: {game_type}\n"

        f"🔢 تعداد پرتاب هر نفر: {count}\n"

        f"💠 مبلغ بازی: "
        f"{amount:,} {UNIT}\n"

        f"🏆 برد: "
        f"{calculate_win_reward(amount):,} {UNIT}\n\n"

        f"{extra}"

        f"یکی از گزینه‌ها را انتخاب کن:",

        reply_markup=choice_keyboard(
            game_id
        )
    )


# =========================================================
# BOT GAME
# =========================================================

async def start_bot_game(
    query,
    context,
    game
):

    if query.from_user.id != game["creator_id"]:

        await query.answer(
            "⛔ فقط سازنده بازی می‌تواند انتخاب کند.",
            show_alert=True
        )

        return

    if game["status"] != "choosing":

        await query.answer(
            "❌ این بازی قبلاً شروع شده.",
            show_alert=True
        )

        return

    amount = game["amount"]

    success = await remove_balance(
        game["creator_id"],
        amount
    )

    if not success:

        games.pop(
            game["id"],
            None
        )

        await query.answer(
            "❌ موجودی کافی نیست.",
            show_alert=True
        )

        await query.message.edit_text(
            "❌ بازی به دلیل کمبود موجودی لغو شد."
        )

        return

    game["mode"] = "bot"
    game["status"] = "creator_turn"

    await query.answer(
        "🎮 بازی شروع شد."
    )

    await query.message.edit_text(

        f"🎮 بازی با ربات شروع شد!\n\n"

        f"🎯 نوع: {game['type']}\n"

        f"🔢 تعداد پرتاب: "
        f"{game['roll_count']}\n"

        f"💠 مبلغ: "
        f"{amount:,} {UNIT}\n"

        f"🏆 برد: "
        f"{calculate_win_reward(amount):,} {UNIT}\n\n"

        f"👤 شما باید "
        f"{game['roll_count']} بار "
        f"{game['type']} بیندازید.\n\n"

        f"پرتاب ۱ از "
        f"{game['roll_count']}"
    )


# =========================================================
# FRIEND GAME
# =========================================================

async def start_friend_game(
    query,
    context,
    game
):

    if query.from_user.id != game["creator_id"]:

        await query.answer(
            "⛔ فقط سازنده بازی می‌تواند انتخاب کند.",
            show_alert=True
        )

        return

    if game["status"] != "choosing":

        await query.answer(
            "❌ این بازی قبلاً شروع شده.",
            show_alert=True
        )

        return

    success = await remove_balance(
        game["creator_id"],
        game["amount"]
    )

    if not success:

        games.pop(
            game["id"],
            None
        )

        await query.answer(
            "❌ موجودی کافی نیست.",
            show_alert=True
        )

        await query.message.edit_text(
            "❌ بازی به دلیل کمبود موجودی لغو شد."
        )

        return

    game["mode"] = "friend"
    game["status"] = "waiting_opponent"

    await query.answer(
        "👥 بازی منتظر حریف است."
    )

    await query.message.edit_text(

        f"👥 بازی با دوستان\n\n"

        f"🎯 نوع: {game['type']}\n"

        f"🔢 تعداد پرتاب هر نفر: "
        f"{game['roll_count']}\n"

        f"💠 مبلغ: "
        f"{game['amount']:,} {UNIT}\n"

        f"🏆 برد: "
        f"{calculate_win_reward(game['amount']):,} {UNIT}\n\n"

        f"👤 سازنده: "
        f"{game['creator_name']}\n\n"

        f"⏳ منتظر بازیکن دوم..."
    )

    await context.bot.send_message(

        game["chat_id"],

        f"👥 یک بازی جدید آماده است!\n\n"

        f"🎯 {game['type']}\n"

        f"🔢 {game['roll_count']} پرتاب برای هر نفر\n"

        f"💠 مبلغ: "
        f"{game['amount']:,} {UNIT}\n"

        f"🏆 برد: "
        f"{calculate_win_reward(game['amount']):,} {UNIT}\n\n"

        f"👤 سازنده: "
        f"{game['creator_name']}\n\n"

        f"برای ورود روی دکمه زیر بزن:",

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

    if game["status"] != "waiting_opponent":

        await query.answer(
            "❌ این بازی دیگر منتظر بازیکن نیست.",
            show_alert=True
        )

        return

    if user.id == game["creator_id"]:

        await query.answer(
            "❌ نمی‌توانی وارد بازی خودت شوی.",
            show_alert=True
        )

        return

    await ensure_user(user)

    balance = await get_balance(
        user.id
    )

    if balance < game["amount"]:

        await query.answer(
            "❌ موجودی کافی نیست.",
            show_alert=True
        )

        return

    success = await remove_balance(
        user.id,
        game["amount"]
    )

    if not success:

        await query.answer(
            "❌ موجودی کافی نیست.",
            show_alert=True
        )

        return

    game["opponent_id"] = user.id
    game["opponent_name"] = user.first_name

    game["status"] = "creator_turn"

    await query.answer(
        "✅ وارد بازی شدی."
    )

    await query.message.edit_text(
        "✅ حریف وارد بازی شد."
    )

    await context.bot.send_message(

        game["chat_id"],

        f"🎮 بازی شروع شد!\n\n"

        f"👤 سازنده: "
        f"{game['creator_name']}\n"

        f"👤 حریف: "
        f"{game['opponent_name']}\n\n"

        f"🎯 نوع: {game['type']}\n"

        f"🔢 تعداد پرتاب هر نفر: "
        f"{game['roll_count']}\n"

        f"💠 مبلغ: "
        f"{game['amount']:,} {UNIT}\n\n"

        f"🎲 اول نوبت "
        f"{game['creator_name']} است.\n\n"

        f"پرتاب ۱ از "
        f"{game['roll_count']}"
    )


# =========================================================
# CANCEL GAME
# =========================================================

async def cancel_game(
    query,
    context,
    game
):

    if query.from_user.id != game["creator_id"]:

        await query.answer(
            "⛔ فقط سازنده می‌تواند بازی را لغو کند.",
            show_alert=True
        )

        return

    if game["mode"] == "bot":

        if game["status"] in (
            "creator_turn",
            "opponent_turn"
        ):

            await add_balance(
                game["creator_id"],
                game["amount"]
            )

    elif game["mode"] == "friend":

        if game["status"] == "waiting_opponent":

            await add_balance(
                game["creator_id"],
                game["amount"]
            )

        elif game["status"] in (
            "creator_turn",
            "opponent_turn"
        ):

            await add_balance(
                game["creator_id"],
                game["amount"]
            )

            if game["opponent_id"]:

                await add_balance(
                    game["opponent_id"],
                    game["amount"]
                )

    games.pop(
        game["id"],
        None
    )

    await query.answer(
        "❌ بازی لغو شد."
    )

    try:

        await query.message.edit_text(
            "❌ بازی لغو شد."
        )

    except Exception:
        pass


# =========================================================
# PROCESS DICE / BOWLING / DART
# =========================================================

async def process_roll(update, context):

    message = update.message

    if not message:
        return

    if not message.dice:
        return

    emoji = message.dice.emoji
    value = message.dice.value

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if emoji != "🎲" and emoji != "🎳" and emoji != "🎯":
        return

    selected = None

    for game in games.values():

        if game["chat_id"] != chat_id:
            continue

        if game["emoji"] != emoji:
            continue

        if game["status"] not in (
            "creator_turn",
            "opponent_turn"
        ):
            continue

        if (
            game["status"] == "creator_turn"
            and user_id == game["creator_id"]
        ):

            selected = game
            break

        if (
            game["status"] == "opponent_turn"
            and game["mode"] == "friend"
            and user_id == game["opponent_id"]
        ):

            selected = game
            break

    if not selected:
        return

    game = selected

    # =====================================================
    # CREATOR
    # =====================================================

    if game["status"] == "creator_turn":

        game["creator_rolls"].append(
            value
        )

        current = len(
            game["creator_rolls"]
        )

        max_rolls = game["roll_count"]

        # =========================
        # بسکتبال: محاسبه گل
        # =========================

        if game["type"] == "بسکتبال":

            goal = is_basketball_goal(value)

            goal_icon = "✅ گل" if goal else "❌ بیرون"

            goals_total = count_basketball_goals(
                game["creator_rolls"]
            )

            progress_text = (
                f"🏀 پرتاب {current} از {max_rolls}\n\n"

                f"👤 {game['creator_name']}: "
                f"{value} → {goal_icon}\n"

                f"🥅 گل‌های فعلی: "
                f"{goals_total}\n\n"
            )

        else:

            total = sum(
                game["creator_rolls"]
            )

            progress_text = (
                f"🎲 پرتاب {current} از {max_rolls}\n\n"

                f"👤 {game['creator_name']}: "
                f"{value}\n"

                f"➕ مجموع فعلی: "
                f"{total}\n\n"
            )

        if current < max_rolls:

            await context.bot.send_message(

                chat_id,

                progress_text +

                f"🎯 پرتاب بعدی را انجام بده."
            )

            return

        # =================================================
        # BOT MODE
        # =================================================

        if game["mode"] == "bot":

            await context.bot.send_message(

                chat_id,

                progress_text +

                f"🤖 حالا ربات {max_rolls} بار "
                f"{game['type']} می‌اندازد..."
            )

            for _ in range(max_rolls):

                bot_message = await context.bot.send_dice(
                    chat_id,
                    emoji=game["emoji"]
                )

                bot_value = bot_message.dice.value

                game["opponent_rolls"].append(
                    bot_value
                )

                await asyncio.sleep(0.8)

            await finish_game(
                context,
                game
            )

            return

        # =================================================
        # FRIEND MODE
        # =================================================

        game["status"] = "opponent_turn"

        await context.bot.send_message(

            chat_id,

            progress_text +

            f"🎯 حالا نوبت "
            f"{game['opponent_name']} است.\n\n"

            f"پرتاب ۱ از {max_rolls}"
        )

        return

    # =====================================================
    # OPPONENT
    # =====================================================

    if (
        game["status"] == "opponent_turn"
        and game["mode"] == "friend"
        and user_id == game["opponent_id"]
    ):

        game["opponent_rolls"].append(
            value
        )

        current = len(
            game["opponent_rolls"]
        )

        max_rolls = game["roll_count"]

        if game["type"] == "بسکتبال":

            goal = is_basketball_goal(value)

            goal_icon = "✅ گل" if goal else "❌ بیرون"

            goals_total = count_basketball_goals(
                game["opponent_rolls"]
            )

            progress_text = (
                f"🏀 پرتاب {current} از {max_rolls}\n\n"

                f"👤 {game['opponent_name']}: "
                f"{value} → {goal_icon}\n"

                f"🥅 گل‌های فعلی: "
                f"{goals_total}\n\n"
            )

        else:

            total = sum(
                game["opponent_rolls"]
            )

            progress_text = (
                f"🎲 پرتاب {current} از {max_rolls}\n\n"

                f"👤 {game['opponent_name']}: "
                f"{value}\n"

                f"➕ مجموع فعلی: "
                f"{total}\n\n"
            )

        if current < max_rolls:

            await context.bot.send_message(

                chat_id,

                progress_text +

                f"🎯 پرتاب بعدی را انجام بده."
            )

            return

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

    reward = calculate_win_reward(
        game["amount"]
    )

    opponent_title = (
        "🤖 ربات"
        if game["mode"] == "bot"
        else f"👤 {game['opponent_name']}"
    )

    # =====================================================
    # بسکتبال
    # =====================================================

    if game["type"] == "بسکتبال":

        creator_goals = count_basketball_goals(
            game["creator_rolls"]
        )

        opponent_goals = count_basketball_goals(
            game["opponent_rolls"]
        )

        creator_rolls = " + ".join(
            map(
                str,
                game["creator_rolls"]
            )
        )

        opponent_rolls = " + ".join(
            map(
                str,
                game["opponent_rolls"]
            )
        )

        text = (

            f"🏁 نتیجه بازی بسکتبال\n\n"

            f"🎯 {game['type']}\n"

            f"🔢 {game['roll_count']} پرتاب\n"

            f"💠 مبلغ بازی: "
            f"{game['amount']:,} {UNIT}\n"

            f"🏆 پاداش برد: "
            f"{reward:,} {UNIT}\n\n"

            f"👤 {game['creator_name']}:\n"

            f"🎲 {creator_rolls}\n"

            f"🥅 گل‌ها: "
            f"{creator_goals}\n\n"

            f"{opponent_title}:\n"

            f"🎲 {opponent_rolls}\n"

            f"🥅 گل‌ها: "
            f"{opponent_goals}\n\n"
        )

        # کاربر صفر گل زده → می‌بازه
        if creator_goals == 0:

            text += (
                "❌ شما هیچ گلی نزدید!\n"
                "❌ باخت.\n"
            )

            if game["mode"] == "friend":

                await add_balance(
                    game["opponent_id"],
                    reward
                )

                new_balance = await get_balance(
                    game["opponent_id"]
                )

                text += (
                    f"\n🏆 برنده: "
                    f"{game['opponent_name']}\n"

                    f"🎁 جایزه: "
                    f"+{reward:,} {UNIT}\n"

                    f"💰 موجودی جدید برنده: "
                    f"{new_balance:,} {UNIT}"
                )

        elif creator_goals > opponent_goals:

            await add_balance(
                game["creator_id"],
                reward
            )

            new_balance = await get_balance(
                game["creator_id"]
            )

            text += (

                f"🏆 برنده: "
                f"{game['creator_name']}\n\n"

                f"🎁 جایزه برد:\n"
                f"+{reward:,} {UNIT}\n\n"

                f"💰 موجودی جدید:\n"
                f"{new_balance:,} {UNIT}"
            )

        elif opponent_goals > creator_goals:

            if game["mode"] == "bot":

                text += (
                    "🤖 ربات برنده شد.\n\n"
                    "❌ این بار برنده نشدی."
                )

            else:

                await add_balance(
                    game["opponent_id"],
                    reward
                )

                new_balance = await get_balance(
                    game["opponent_id"]
                )

                text += (

                    f"🏆 برنده: "
                    f"{game['opponent_name']}\n\n"

                    f"🎁 جایزه برد:\n"
                    f"+{reward:,} {UNIT}\n\n"

                    f"💰 موجودی جدید برنده:\n"
                    f"{new_balance:,} {UNIT}"
                )

        else:

            # مساوی
            await add_balance(
                game["creator_id"],
                game["amount"]
            )

            if game["mode"] == "friend":

                await add_balance(
                    game["opponent_id"],
                    game["amount"]
                )

            text += (

                "🤝 بازی مساوی شد.\n\n"

                "💰 مبلغ بازی به بازیکنان "
                "برگشت داده شد."
            )

        games.pop(
            game["id"],
            None
        )

        await context.bot.send_message(
            game["chat_id"],
            text
        )

        return

    # =====================================================
    # بقیه بازی‌ها (تاس، بولینگ، دارت)
    # =====================================================

    creator_total = sum(
        game["creator_rolls"]
    )

    opponent_total = sum(
        game["opponent_rolls"]
    )

    creator_rolls = " + ".join(
        map(
            str,
            game["creator_rolls"]
        )
    )

    opponent_rolls = " + ".join(
        map(
            str,
            game["opponent_rolls"]
        )
    )

    text = (

        f"🏁 نتیجه بازی\n\n"

        f"🎯 {game['type']}\n"

        f"🔢 {game['roll_count']} پرتاب\n"

        f"💠 مبلغ بازی: "
        f"{game['amount']:,} {UNIT}\n"

        f"🏆 پاداش برد: "
        f"{reward:,} {UNIT}\n\n"

        f"👤 {game['creator_name']}:\n"

        f"🎲 {creator_rolls}\n"

        f"➕ مجموع: "
        f"{creator_total}\n\n"

        f"{opponent_title}:\n"

        f"🎲 {opponent_rolls}\n"

        f"➕ مجموع: "
        f"{opponent_total}\n\n"
    )

    if creator_total > opponent_total:

        await add_balance(
            game["creator_id"],
            reward
        )

        new_balance = await get_balance(
            game["creator_id"]
        )

        text += (

            f"🏆 برنده: "
            f"{game['creator_name']}\n\n"

            f"🎁 جایزه برد:\n"
            f"+{reward:,} {UNIT}\n\n"

            f"💰 موجودی جدید:\n"
            f"{new_balance:,} {UNIT}"
        )

    elif opponent_total > creator_total:

        if game["mode"] == "bot":

            text += (
                "🤖 ربات برنده شد.\n\n"
                "❌ این بار برنده نشدی."
            )

        else:

            await add_balance(
                game["opponent_id"],
                reward
            )

            new_balance = await get_balance(
                game["opponent_id"]
            )

            text += (

                f"🏆 برنده: "
                f"{game['opponent_name']}\n\n"

                f"🎁 جایزه برد:\n"
                f"+{reward:,} {UNIT}\n\n"

                f"💰 موجودی جدید برنده:\n"
                f"{new_balance:,} {UNIT}"
            )

    else:

        await add_balance(
            game["creator_id"],
            game["amount"]
        )

        if game["mode"] == "friend":

            await add_balance(
                game["opponent_id"],
                game["amount"]
            )

        text += (

            "🤝 بازی مساوی شد.\n\n"

            "💰 مبلغ بازی به بازیکنان "
            "برگشت داده شد."
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

    if amount < MIN_GAME_AMOUNT:

        await update.message.reply_text(
            f"❌ حداقل مبلغ بازی "
            f"{MIN_GAME_AMOUNT:,} {UNIT} است."
        )

        return

    if balance < amount:

        await update.message.reply_text(

            f"❌ موجودی کافی نیست.\n\n"

            f"💰 موجودی: "
            f"{balance:,} {UNIT}"
        )

        return

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

    reward = calculate_win_reward(
        amount
    )

    if result == choice:

        await add_balance(
            user.id,
            reward
        )

        result_text = (

            f"🏆 برنده شدی!\n"

            f"🎁 +{reward:,} {UNIT}"
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

        f"💠 مبلغ بازی: "
        f"{amount:,} {UNIT}\n"

        f"🏆 جایزه برد: "
        f"{reward:,} {UNIT}\n\n"

        f"🎯 انتخاب: {choice}\n"

        f"🎲 عدد: {value}\n"

        f"📌 نتیجه: {result}\n\n"

        f"{result_text}\n\n"

        f"💰 موجودی: "
        f"{new_balance:,} {UNIT}"
    )


# =========================================================
# TRANSFER
# =========================================================

async def do_transfer(update, amount):

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

    await ensure_user(
        receiver
    )

    success = await remove_balance(
        sender.id,
        amount
    )

    if not success:

        await update.message.reply_text(
            "❌ موجودی کافی نیست."
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
                (
                    sender_id,
                    receiver_id,
                    amount
                )
                VALUES (?, ?, ?)
                """,
                (
                    sender.id,
                    receiver.id,
                    amount
                )
            )

            db.commit()

    await update.message.reply_text(

        f"✅ انتقال انجام شد.\n\n"

        f"👤 گیرنده: "
        f"{receiver.first_name}\n"

        f"💰 مقدار: "
        f"{amount:,} {UNIT}\n\n"

        f"💳 موجودی شما: "
        f"{await get_balance(sender.id):,} {UNIT}"
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

    # کم کردن موجودی
    await remove_balance(user.id, amount)

    async with db_lock:

        with closing(get_db()) as db:

            cur = db.execute(
                """
                INSERT INTO withdrawals
                (
                    user_id,
                    amount,
                    status
                )
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

            f"🆔 آیدی: `{user.id}`\n"

            f"📱 یوزرنیم: "
            f"@{user.username if user.username else 'ندارد'}\n\n"

            f"💰 مقدار: "
            f"{amount:,} {UNIT}\n"

            f"🆔 درخواست: `{request_id}`\n\n"

            f"📌 وضعیت: در انتظار بررسی",

            parse_mode="Markdown"
        )

    except Exception as e:

        logger.error(
            "Withdraw channel error: %s",
            e
        )

    context.user_data.pop(
        "waiting_withdraw",
        None
    )

    await update.message.reply_text(

        f"✅ برداشت شما در کانال ثبت شد.\n\n"

        f"💰 مقدار: "
        f"{amount:,} {UNIT}\n"

        f"🆔 درخواست: "
        f"{request_id}"
    )

    return True


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

        f"حداقل برداشت: "
        f"{MIN_WITHDRAW:,} {UNIT}\n\n"

        f"مقدار را بفرست."
    )


# =========================================================
# REFERRAL
# =========================================================

async def referral(update, context):

    user = update.effective_user

    me = await context.bot.get_me()

    link = (
        f"https://t.me/{me.username}"
        f"?start=ref_{user.id}"
    )

    await update.message.reply_text(

        f"👥 زیرمجموعه\n\n"

        f"🎁 پاداش دعوت: "
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

    if not amount or amount <= 0:

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

    await update.message.reply_text(

        f"✅ موجودی اضافه شد.\n\n"

        f"👤 {target['first_name']}\n"

        f"🆔 {user_id}\n"

        f"➕ {amount:,} {UNIT}\n\n"

        f"💰 موجودی جدید:\n"

        f"{await get_balance(user_id):,} {UNIT}"
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

    if not amount or amount <= 0:

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

    if not await remove_balance(
        user_id,
        amount
    ):

        await update.message.reply_text(
            "❌ موجودی کافی نیست."
        )

        return

    await update.message.reply_text(

        f"✅ موجودی کم شد.\n\n"

        f"👤 {target['first_name']}\n"

        f"🆔 {user_id}\n"

        f"➖ {amount:,} {UNIT}\n\n"

        f"💰 موجودی جدید:\n"

        f"{await get_balance(user_id):,} {UNIT}"
    )


async def admin_balance(
    update,
    context
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


# =========================================================
# ADMIN USERS
# =========================================================

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

    text = "📋 موجودی کاربران\n\n"

    for i, row in enumerate(rows, 1):

        username = (
            f"@{row['username']}"
            if row["username"]
            else "بدون یوزرنیم"
        )

        line = (
            f"{i}_ "
            f"{row['balance']:,} "
            f"{UNIT} "
            f"{username}\n"
        )

        if len(text) + len(line) > 3800:

            await update.callback_query.message.reply_text(
                text
            )

            text = ""

        text += line

    if text:

        await update.callback_query.message.reply_text(
            text
        )


async def admin_stats(
    update,
    context
):

    async with db_lock:

        with closing(get_db()) as db:

            users = db.execute(
                """
                SELECT COUNT(*) c
                FROM users
                """
            ).fetchone()["c"]

            total = db.execute(
                """
                SELECT COALESCE(SUM(balance),0) s
                FROM users
                """
            ).fetchone()["s"]

            withdrawals = db.execute(
                """
                SELECT COUNT(*) c
                FROM withdrawals
                WHERE status='pending'
                """
            ).fetchone()["c"]

    await update.callback_query.message.reply_text(

        f"📊 آمار\n\n"

        f"👤 کاربران: {users:,}\n"

        f"💰 مجموع موجودی: "
        f"{total:,} {UNIT}\n"

        f"💸 برداشت‌های در انتظار: "
        f"{withdrawals:,}\n"

        f"🎮 بازی‌های فعال: "
        f"{len(games):,}"
    )


async def admin_withdrawals(
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
            "💸 درخواستی وجود ندارد."
        )

        return

    text = "💸 درخواست‌های برداشت\n\n"

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

    if data == "check_join":

        if await require_join(
            update,
            context
        ):

            await query.answer(
                "✅ عضویت تأیید شد."
            )

            await query.message.reply_text(
                "✅ عضویت تأیید شد.",
                reply_markup=main_menu()
            )

        return

    if data.startswith("game_"):

        action, game_id = data.split(
            ":",
            1
        )

        game = games.get(
            game_id
        )

        if not game:

            await query.answer(
                "❌ بازی پیدا نشد.",
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

            await admin_stats(
                update,
                context
            )

            return

        if data == "admin_withdrawals":

            await admin_withdrawals(
                update,
                context
            )

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

            f"نام: "
            f"{user['first_name']}\n"

            f"🆔 آیدی: "
            f"{user['user_id']}\n"

            f"📱 یوزرنیم: "
            f"{username}\n\n"

            f"💰 موجودی: "
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

            f"💸 برداشت\n\n"

            f"حداقل: "
            f"{MIN_WITHDRAW:,} {UNIT}\n\n"

            f"مقدار را بفرست."
        )

        return

    if data == "transfer_help":

        await query.message.reply_text(

            "🔁 روی پیام کاربر ریپلای کن و بنویس:\n\n"

            "انتقال 500"
        )

        return

    if data == "referral":

        user = query.from_user

        me = await context.bot.get_me()

        link = (
            f"https://t.me/{me.username}"
            f"?start=ref_{user.id}"
        )

        await query.message.reply_text(

            f"👥 دعوت دوستان\n\n"

            f"🎁 پاداش: "
            f"{REFERRAL_REWARD:,} {UNIT}\n\n"

            f"🔗 {link}"
        )

        return

    if data == "help":

        await query.message.reply_text(

            "📚 راهنما:\n\n"

            "🎲 1 تاس 100\n"
            "🎲 2 تاس 100\n"
            "🎲 3 تاس 100\n"
            "یک بازی با ۱ تا ۳ پرتاب برای هر نفر.\n\n"

            "🎳 بولینگ، 🎯 دارت و 🏀 بسکتبال هم "
            "با همین فرمت کار می‌کنند.\n\n"

            "🏀 قانون بسکتبال:\n"
            "تاس ۳+ = گل ✅\n"
            "تاس ۱-۲ = بیرون ❌\n"
            "هرکی گل بیشتر = برنده\n"
            "صفر گل = باخت\n\n"

            "🎮 بازی با ربات:\n"
            "شما N بار و ربات N بار.\n\n"

            "👥 بازی با دوستان:\n"
            "سازنده N بار و حریف N بار.\n\n"

            "🏆 فرمول برد:\n"
            "مبلغ بازی × ۱.۸\n\n"

            "⚠️ حداکثر تعداد پرتاب: ۳"
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

    await ensure_user(
        user
    )

    text = clean_text(
        update.message.text
    )

    normalized = normalize_digits(
        text
    )

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

        except ValueError:

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
                f"➕ {amount:,} {UNIT} اضافه شد."
            )

        else:

            if not await remove_balance(
                target_id,
                amount
            ):

                await update.message.reply_text(
                    "❌ موجودی کافی نیست."
                )

                context.user_data.pop(
                    "admin_action",
                    None
                )

                return

            result = (
                f"➖ {amount:,} {UNIT} کسر شد."
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

            f"{await get_balance(target_id):,} {UNIT}"
        )

        return

    if (
        update.effective_chat.type == ChatType.PRIVATE
        and context.user_data.get(
            "waiting_withdraw"
        )
    ):

        if await process_withdraw(
            update,
            context
        ):

            return

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

    match = re.fullmatch(
        r"([0-9]+)\s*(فرد|زوج)",
        normalized
    )

    if match:

        amount = int(
            match.group(1)
        )

        choice = match.group(2)

        await even_odd_game(
            update,
            context,
            amount,
            choice
        )

        return

    match = re.fullmatch(
        r"([0-9]+)\s*(بولینگ|تاس|دارت|بسکتبال)\s*([0-9]+)",
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

        if count > MAX_ROLL_COUNT:

            await update.message.reply_text(
                f"❌ حداکثر تعداد پرتاب {MAX_ROLL_COUNT} است."
            )

            return

        if count < 1:

            await update.message.reply_text(
                "❌ تعداد پرتاب باید حداقل ۱ باشد."
            )

            return

        if amount < MIN_GAME_AMOUNT:

            await update.message.reply_text(

                f"❌ حداقل مبلغ بازی "
                f"{MIN_GAME_AMOUNT:,} {UNIT} است."
            )

            return

        await create_game(
            update,
            context,
            count,
            game_type,
            amount
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

        "📚 راهنمای بازی\n\n"

        "🎲 1 تاس 100\n"
        "🎲 2 تاس 100\n"
        "🎲 3 تاس 100\n"
        "یک بازی با ۱ تا ۳ پرتاب برای هر نفر.\n\n"

        "🎳 بولینگ، 🎯 دارت و 🏀 بسکتبال هم "
        "با همین فرمت کار می‌کنند.\n\n"

        "🏀 قانون بسکتبال:\n"
        "تاس ۳+ = گل ✅\n"
        "تاس ۱-۲ = بیرون ❌\n"
        "هرکی گل بیشتر = برنده\n"
        "صفر گل = باخت\n\n"

        "🎮 بازی با ربات:\n"
        "شما N بار و ربات N بار.\n\n"

        "👥 بازی با دوستان:\n"
        "هر دو بازیکن N بار.\n\n"

        "🏆 فرمول برد:\n"
        "مبلغ بازی × ۱.۸\n\n"

        "⚠️ حداکثر تعداد پرتاب: ۳"
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

    user = update.effective_user

    me = await context.bot.get_me()

    link = (
        f"https://t.me/{me.username}"
        f"?start=ref_{user.id}"
    )

    await update.message.reply_text(

        f"👥 لینک دعوت:\n"
        f"{link}\n\n"

        f"🎁 پاداش: "
        f"{REFERRAL_REWARD:,} {UNIT}"
    )


# =========================================================
# MAIN
# =========================================================

async def post_init(
    application
):

    init_db()

    logger.info(
        "Database initialized."
    )


def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN تنظیم نشده است."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    app.add_handler(
        CommandHandler(
            "balance",
            balance_command
        )
    )

    app.add_handler(
        CommandHandler(
            "referral",
            referral_command
        )
    )

    app.add_handler(
        CommandHandler(
            "withdraw",
            withdraw_command
        )
    )

    app.add_handler(
        CommandHandler(
            "admin",
            admin
        )
    )

    app.add_handler(
        CommandHandler(
            "add",
            admin_add
        )
    )

    app.add_handler(
        CommandHandler(
            "sub",
            admin_sub
        )
    )

    app.add_handler(
        CommandHandler(
            "bal",
            admin_balance
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Dice.ALL,
            process_roll
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    logger.info(
        "BET TeK started."
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
