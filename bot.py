# Telegram Auto Join + Welcome Bot with Backup Channels, Extra Links, Fixed Ad & Google Sheet Sync
# Python 3.10+ | python-telegram-bot 22.x

import logging
import os
import sqlite3
import time
from html import escape
import requests

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, ChatJoinRequestHandler, ChatMemberHandler,
    CommandHandler, CallbackQueryHandler, ContextTypes,
)
from telegram.request import HTTPXRequest

BOT_TOKEN = "9085444422:AAFZtyBKErXiHt7er9jqowfZp_pI2Zyav2Q"
OWNER_IDS = {5635425261, 8808819557, 8848826033}  # Admin IDs

# Apnar Google Apps Script Web App URL
GAS_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbxrOge9Ni3S-4-lkNSBJ5tB1Shl6Wf7atg-MdMXe89uIi4uePDTafpTc7ddttXoVFI/exec"

# Data base file path
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()
DB_FILE = os.path.join(BASE_DIR, "join_manager.db")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("telegram-auto-join")


def db():
    con = sqlite3.connect(DB_FILE)
    con.execute("""CREATE TABLE IF NOT EXISTS chats (
        chat_id INTEGER PRIMARY KEY, title TEXT, username TEXT, chat_type TEXT,
        mode TEXT DEFAULT 'auto', status TEXT DEFAULT 'active', added_by INTEGER, added_at INTEGER
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS join_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER,
        user_id INTEGER, action TEXT, created_at INTEGER
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS chat_settings (
        chat_id INTEGER PRIMARY KEY, welcome_text TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS banned_users (
        user_id INTEGER PRIMARY KEY, banned_at INTEGER
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS bot_users (
        user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, joined_at INTEGER
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS extra_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, url TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS backup_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, url TEXT
    )""")
    
    cols = {row[1] for row in con.execute("PRAGMA table_info(chats)")}
    if "status" not in cols:
        con.execute("ALTER TABLE chats ADD COLUMN status TEXT DEFAULT 'active'")
    if "username" not in cols:
        con.execute("ALTER TABLE chats ADD COLUMN username TEXT DEFAULT ''")
    con.commit()
    return con


def send_to_google_sheet(user):
    if not GAS_WEB_APP_URL:
        return
    try:
        # Username, UID ebong Display Name format kora
        username = f"@{user.username}" if user.username else "N/A"
        first_name = user.first_name or ""
        last_name = user.last_name or ""
        display_name = (first_name + " " + last_name).strip()
        if not display_name:
            display_name = user.full_name or "No Name"

        data = {
            "user_id": user.id,             # A Column: UID / Chat ID
            "username": username,          # B Column: Username
            "display_name": display_name   # C Column: Display Name
        }
        requests.post(GAS_WEB_APP_URL, json=data, timeout=5)
    except Exception as e:
        log.error(f"Google Sheet sync error: {e}")


def save_bot_user(user):
    try:
        con = db()
        uname = user.username or ""
        fname = user.full_name or "No Name"
        con.execute(
            "INSERT INTO bot_users(user_id, full_name, username, joined_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username",
            (user.id, fname, uname, int(time.time()))
        )
        con.commit()
        con.close()
        send_to_google_sheet(user)
    except Exception as e:
        log.error(f"Save bot user error: {e}")


def save_chat(chat, added_by_user_id=0):
    con = db()
    row = con.execute("SELECT added_by FROM chats WHERE chat_id=?", (chat.id,)).fetchone()
    current_added_by = row[0] if row and row[0] else added_by_user_id
    chat_username = chat.username or ""
    
    con.execute(
        "INSERT INTO chats(chat_id,title,username,chat_type,mode,status,added_by,added_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, username=excluded.username, chat_type=excluded.chat_type, added_by=?",
        (chat.id, chat.title or str(chat.id), chat_username, chat.type, "auto", "active", current_added_by, int(time.time()), current_added_by),
    )
    con.commit()
    con.close()


def log_action(chat_id, user_id, action):
    con = db()
    con.execute(
        "INSERT INTO join_logs(chat_id,user_id,action,created_at) VALUES(?,?,?,?)",
        (chat_id, user_id, action, int(time.time())),
    )
    con.commit()
    con.close()


def welcome_template(chat_id):
    con = db()
    row = con.execute("SELECT welcome_text FROM chat_settings WHERE chat_id=?", (chat_id,)).fetchone()
    con.close()
    return row[0] if row and row[0] else "🎉 Shagotom {mention}!\n\n<b>{group}</b>-e apnake antorikbhave shagotom ❤️"


def is_admin_auth(user_id):
    return user_id in OWNER_IDS


def is_user_banned(user_id):
    con = db()
    row = con.execute("SELECT 1 FROM banned_users WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return row is not None


def get_extra_links():
    try:
        con = db()
        rows = con.execute("SELECT id, title, url FROM extra_links WHERE url LIKE 'http%'").fetchall()
        con.close()
        return rows
    except Exception:
        return []

def get_backup_channels():
    try:
        con = db()
        rows = con.execute("SELECT id, title, url FROM backup_channels WHERE url LIKE 'http%'").fetchall()
        con.close()
        return rows
    except Exception:
        return []


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    bot_username = context.bot.username

    if is_user_banned(user.id):
        await update.effective_message.reply_text("🚫 Dukkhito, apnake ei bot theke ban kora hoyeche!")
        return

    save_bot_user(user)

    if chat.type in ("group", "supergroup"):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Group-e join request pathan", url=f"https://t.me/{bot_username}?start=start")]
        ])
        await update.effective_message.reply_text(
            f"Hello {user.full_name}!\nGroup-e cholte thakar jonno niche click korun:",
            reply_markup=keyboard
        )
        return

    args = context.args
    if args and args[0].startswith("join_"):
        await update.effective_message.reply_text(
            "👋 Shagotom!\n\nApnar group ba channel request safolvabe approve o bot start kora hoyeche! 🎉"
        )
        return

    username = f"@{user.username}" if user.username else user.full_name
    
    if is_admin_auth(user.id):
        await update.effective_message.reply_text(
            f"Hello {username} bhai! Admin panel-e apnake shagotom.\n\n"
            "/admin - Admin panel\n"
            "/chats - Connected chat list\n"
            "/stats - Bot stats"
        )
    else:
        keyboard_buttons = [
            [InlineKeyboardButton("🎁 Claim Bonus", url="https://asiafilm.org/4/dee2b067ed52a89a6107b8873607c715")]
        ]
        
        backup_rows = get_backup_channels()
        for _, title, url in backup_rows:
            keyboard_buttons.append([InlineKeyboardButton(title, url=url)])

        extra_rows = get_extra_links()
        for _, title, url in extra_rows:
            keyboard_buttons.append([InlineKeyboardButton(title, url=url)])

        user_keyboard = InlineKeyboardMarkup(keyboard_buttons)

        await update.effective_message.reply_text(
            f"👋 Hello {username}!\n\n"
            "Amader auto join request approve bot-e apnake shagotom janai! ❤️\n"
            "Nicher button theke apnar bonus claim kore nite paren:",
            reply_markup=user_keyboard,
            disable_web_page_preview=True
        )


async def my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    change = update.my_chat_member
    chat = change.chat
    status = change.new_chat_member.status
    added_by = change.from_user.id

    if status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER):
        save_chat(chat, added_by_user_id=added_by)
    elif status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        con = db()
        con.execute("DELETE FROM chats WHERE chat_id=?", (chat.id,))
        con.commit()
        con.close()


async def join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    chat, user = req.chat, req.from_user

    if is_user_banned(user.id):
        return

    save_bot_user(user)

    con = db()
    chat_row = con.execute("SELECT status FROM chats WHERE chat_id=?", (chat.id,)).fetchone()
    con.close()
    
    if chat_row and chat_row[0] == 'banned':
        return

    save_chat(chat)

    try:
        await context.bot.approve_chat_join_request(chat.id, user.id)
        log_action(chat.id, user.id, "auto-approved")
    except TelegramError:
        log_action(chat.id, user.id, "approval-failed")
        return

    try:
        user_keyboard_buttons = [
            [InlineKeyboardButton("🎁 Claim Bonus", url="https://asiafilm.org/4/dee2b067ed52a89a6107b8873607c715")]
        ]
        user_keyboard = InlineKeyboardMarkup(user_keyboard_buttons)

        await context.bot.send_message(
            user.id,
            f"👋 Hello {escape(user.full_name)},\n\nApnar join request approve kora hoyeche! 🎉",
            parse_mode=ParseMode.HTML,
            reply_markup=user_keyboard
        )
    except TelegramError:
        pass


def main():
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(ChatJoinRequestHandler(join_request))
    app.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    
    print("Auto Join Bot cholche...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
