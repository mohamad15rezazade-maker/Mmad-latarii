 # -*- coding: utf-8 -*-
"""
DOGS موج بات
Python 3.10+
Install:
    pip install python-telegram-bot==22.5

ENV:
    BOT_TOKEN=توکن_ربات
"""

import os
import sqlite3
import random
import logging
import asyncio
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# SETTINGS
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

CHANNEL_USERNAME = "@BET_Tekhhh"
GROUP_USERNAME = "@BET_TAKbotcv"

CHANNEL_URL = "https://t.me/BET_Tekhhh"
GROUP_URL = "https://t.me/BET_TAKbotcv"

DB_FILE = "dogs_moj_bot.db"

# حداکثر ۵ بازی همزمان
MAX_GAMES = 5

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("DOGS_MOJ")


# =========================
# DATABASE
# =========================

db = sqlite3.connect(
    DB_FILE,
    check_same_thread=False,
    timeout=30,
)

db.row_factory = sqlite3.Row

db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA busy_timeout=30000")


def init_db():
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            message_id INTEGER,
            game_type TEXT,
            creator_id INTEGER,
            opponent_id INTEGER,
            status TEXT DEFAULT 'waiting',
            created_at TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER,
            receiver_id INTEGER,
            amount INTEGER,
            created_at TEXT
        )
    """)

    db.commit()


init_db()


# =========================
# HELPERS
# =========================

def normalize_digits(text: str) -> str:
    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )
    return text.translate(table)


def get_user(user_id: int):
    return db.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()


def ensure_user(user):
    if not user:
        return

    exists = get_user(user.id)

    if exists:
        db.execute("""
            UPDATE users
            SET username=?, first_name=?
            WHERE user_id=?
        """, (
            user.username,
            user.first_name,
            user.id,
        ))
    else:
        db.execute("""
            INSERT INTO users
            (user_id, username, first_name, balance, created_at)
            VALUES (?, ?, ?, 0, ?)
        """, (
            user.id,
            user.username,
            user.first_name,
            datetime.now().isoformat(),
        ))

    db.commit()


def balance(user_id: int) -> int:
    row = get_user(user_id)
    return int(row["balance"]) if row else 0


def change_balance(user_id: int, amount: int):
    db.execute(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (amount, user_id)
    )
    db.commit()


def format_dogs(amount: int) -> str:
    return f"{amount:,} DOGS"


# =========================
# FORCE JOIN
# =========================

async def is_member(bot, chat_username: str, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(
            chat_id=chat_username,
            user_id=user_id,
        )

        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )

    except Exception as e:
        logger.warning(
            "Membership check failed %s %s: %s",
            chat_username,
            user_id,
            e,
        )
        return False


async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user

    if not user:
        return False

    joined_channel = await is_member(
        context.bot,
        CHANNEL_USERNAME,
        user.id,
    )

    joined_group = await is_member(
        context.bot,
        GROUP_USERNAME,
        user.id,
    )

    if joined_channel and joined_group:
        return True

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 عضویت در کانال",
                url=CHANNEL_URL,
            )
        ],
        [
            InlineKeyboardButton(
                "👥 عضویت در گپ",
                url=GROUP_URL,
            )
        ],
        [
            InlineKeyboardButton(
                "✅ بررسی عضویت",
                callback_data="check_join",
            )
        ],
    ]

    text = (
        "🔒 برای استفاده از ربات ابتدا در کانال و گپ عضو شوید.\n\n"
        "📢 کانال: @BET_Tekhhh\n"
        "👥 گپ: @BET_TAKbotcv"
    )

    if update.callback_query:
        await update.callback_query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.effective_message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    return False


# =========================
# MAIN MENU
# =========================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 موجودی DOGS", callback_data="balance"),
            InlineKeyboardButton("👤 حساب من", callback_data="account"),
        ],
        [
            InlineKeyboardButton("🎮 بازی‌ها", callback_data="games"),
            InlineKeyboardButton("🔄 انتقال DOGS", callback_data="transfer_help"),
        ],
        [
            InlineKeyboardButton("📖 راهنما", callback_data="help"),
        ],
    ])


async def send_main_menu(update: Update):
    await update.effective_message.reply_text(
        "🐶 به DOGS موج بات خوش آمدید.\n\n"
        "موجودی شما به صورت DOGS موج بات نمایش داده می‌شود.",
        reply_markup=main_keyboard(),
    )


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    ensure_user(user)

    if not await check_join(update, context):
        return

    await send_main_menu(update)


# =========================
# BALANCE
# =========================

async def show_balance(update: Update):
    user = update.effective_user

    await update.effective_message.reply_text(
        f"💰 موجودی DOGS موج بات:\n\n"
        f"🐶 {format_dogs(balance(user.id))}"
    )


# =========================
# ACCOUNT
# =========================

async def show_account(update: Update):
    user = update.effective_user
    b = balance(user.id)

    username = (
        f"@{user.username}"
        if user.username
        else "ندارد"
    )

    await update.effective_message.reply_text(
        "👤 حساب شما\n\n"
        f"🆔 آیدی: {user.id}\n"
        f"👤 یوزرنیم: {username}\n"
        f"💰 موجودی DOGS موج بات: {format_dogs(b)}"
    )


# =========================
# GAMES MENU
# =========================

def games_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎲 فرد / زوج",
                callback_data="game_evenodd"
            )
        ],
        [
            InlineKeyboardButton(
                "🎳 بولینگ",
                callback_data="game_bowling"
            ),
            InlineKeyboardButton(
                "🎲 تاس",
                callback_data="game_dice"
            ),
        ],
        [
            InlineKeyboardButton(
                "🎯 دارت",
                callback_data="game_dart"
            )
        ],
    ])


async def show_games(update: Update):
    await update.effective_message.reply_text(
        "🎮 بازی‌های موجود:\n\n"
        "🔹 فرد / زوج\n"
        "🔹 بولینگ\n"
        "🔹 تاس\n"
        "🔹 دارت\n\n"
        "این بازی‌ها بدون شرط‌بندی و پرداخت بر اساس نتیجه هستند.",
        reply_markup=games_keyboard(),
    )


# =========================
# EVEN / ODD
# =========================

async def even_odd_game(update: Update):
    user = update.effective_user

    result = random.randint(1, 6)

    if result % 2 == 0:
        result_type = "زوج"
    else:
        result_type = "فرد"

    await update.effective_message.reply_text(
        "🎲 نتیجه فرد / زوج\n\n"
        f"🎲 عدد تاس: {result}\n"
        f"📌 نتیجه: {result_type}\n\n"
        "این بازی فقط برای سرگرمی است."
    )


# =========================
# DICE
# =========================

async def dice_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    msg = await context.bot.send_dice(
        chat_id=update.effective_chat.id,
        emoji="🎲",
    )

    value = msg.dice.value

    await asyncio.sleep(2)

    await update.effective_message.reply_text(
        f"🎲 نتیجه تاس برای {user.first_name}:\n\n"
        f"عدد: {value}"
    )


# =========================
# BOWLING
# =========================

async def bowling_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    msg = await context.bot.send_dice(
        chat_id=update.effective_chat.id,
        emoji="🎳",
    )

    value = msg.dice.value

    await asyncio.sleep(2)

    await update.effective_message.reply_text(
        f"🎳 نتیجه بولینگ برای {user.first_name}:\n\n"
        f"امتیاز پرتاب: {value}"
    )


# =========================
# DART
# =========================

async def dart_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    msg = await context.bot.send_dice(
        chat_id=update.effective_chat.id,
        emoji="🎯",
    )

    value = msg.dice.value

    await asyncio.sleep(2)

    await update.effective_message.reply_text(
        f"🎯 نتیجه دارت برای {user.first_name}:\n\n"
        f"امتیاز پرتاب: {value}"
    )


# =========================
# TRANSFER
# =========================

async def transfer_help(update: Update):
    await update.effective_message.reply_text(
        "🔄 انتقال DOGS موج بات\n\n"
        "برای انتقال در گپ:\n"
        "1️⃣ روی پیام شخص موردنظر Reply بزنید.\n"
        "2️⃣ مقدار را بنویسید.\n\n"
        "مثال:\n"
        "500\n"
        "یا\n"
        "۵۰۰"
    )


async def handle_transfer(update: Update):
    message = update.effective_message
    sender = update.effective_user

    if not message.reply_to_message:
        return False

    text = normalize_digits(message.text.strip())

    if not text.isdigit():
        return False

    amount = int(text)

    if amount <= 0:
        return False

    target = message.reply_to_message.from_user

    if not target:
        return False

    if target.id == sender.id:
        await message.reply_text(
            "❌ نمی‌توانید به خودتان انتقال دهید."
        )
        return True

    ensure_user(target)

    sender_balance = balance(sender.id)

    if sender_balance < amount:
        await message.reply_text(
            "❌ موجودی کافی نیست."
        )
        return True

    change_balance(sender.id, -amount)
    change_balance(target.id, amount)

    db.execute("""
        INSERT INTO transfers
        (sender_id, receiver_id, amount, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        sender.id,
        target.id,
        amount,
        datetime.now().isoformat(),
    ))

    db.commit()

    await message.reply_text(
        "✅ انتقال انجام شد.\n\n"
        f"💸 مقدار: {format_dogs(amount)}\n"
        f"👤 گیرنده: {target.first_name}\n\n"
        f"💰 موجودی جدید شما: {format_dogs(balance(sender.id))}"
    )

    return True


# =========================
# TEXT COMMANDS
# =========================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.text:
        return

    user = update.effective_user

    ensure_user(user)

    if not await check_join(update, context):
        return

    text = normalize_digits(
        message.text.strip().lower()
    )

    # انتقال با ریپلای
    if await handle_transfer(update):
        return

    # موجودی
    if text in (
        "م",
        "موجودی",
        "balance",
        "بالانس",
    ):
        await show_balance(update)
        return

    # فرد
    if text in (
        "فرد",
        "زوج",
    ):
        await even_odd_game(update)
        return

    # بولینگ
    if "بولینگ" in text:
        await bowling_game(update, context)
        return

    # تاس
    if text == "تاس":
        await dice_game(update, context)
        return

    # دارت
    if "دارت" in text:
        await dart_game(update, context)
        return


# =========================
# CALLBACKS
# =========================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    user = update.effective_user

    ensure_user(user)

    if query.data == "check_join":
        if await check_join(update, context):
            await query.message.edit_text(
                "✅ عضویت شما تأیید شد.\n\n"
                "🐶 به DOGS موج بات خوش آمدید.",
                reply_markup=main_keyboard(),
            )
        return

    if not await check_join(update, context):
        return

    if query.data == "balance":
        await query.message.reply_text(
            f"💰 موجودی DOGS موج بات:\n\n"
            f"🐶 {format_dogs(balance(user.id))}"
        )

    elif query.data == "account":
        username = (
            f"@{user.username}"
            if user.username
            else "ندارد"
        )

        await query.message.reply_text(
            "👤 حساب من\n\n"
            f"🆔 آیدی: {user.id}\n"
            f"👤 یوزرنیم: {username}\n"
            f"💰 موجودی: {format_dogs(balance(user.id))}"
        )

    elif query.data == "games":
        await query.message.reply_text(
            "🎮 بازی‌ها:",
            reply_markup=games_keyboard(),
        )

    elif query.data == "game_evenodd":
        await even_odd_game(update)

    elif query.data == "game_bowling":
        await bowling_game(update, context)

    elif query.data == "game_dice":
        await dice_game(update, context)

    elif query.data == "game_dart":
        await dart_game(update, context)

    elif query.data == "transfer_help":
        await query.message.reply_text(
            "🔄 انتقال DOGS\n\n"
            "در گپ روی پیام کاربر Reply بزنید و مقدار را ارسال کنید.\n\n"
            "مثال:\n"
            "500"
        )

    elif query.data == "help":
        await query.message.reply_text(
            "📖 راهنمای DOGS موج بات\n\n"
            "💰 م یا موجودی → نمایش موجودی\n"
            "🔄 انتقال → Reply روی پیام کاربر + مقدار\n"
            "🎲 فرد / زوج → بازی تاس فرد یا زوج\n"
            "🎳 بولینگ → پرتاب بولینگ\n"
            "🎲 تاس → پرتاب تاس\n"
            "🎯 دارت → پرتاب دارت\n\n"
            "📢 کانال:\n"
            f"{CHANNEL_URL}\n\n"
            "👥 گپ:\n"
            f"{GROUP_URL}"
        )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception(
        "Unhandled exception:",
        exc_info=context.error,
    )


# =========================
# MAIN
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN در Environment تنظیم نشده است."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CallbackQueryHandler(callbacks)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    application.add_error_handler(error_handler)

    print("DOGS موج بات started...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
