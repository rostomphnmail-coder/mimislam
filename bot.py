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
OWNER_IDS = {5635425261, 8808819557}  # Admin IDs

# Apnar Google Apps Script Web App URL
GAS_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbyjLKejQu63LD3kB8kYB5-AVaJps0s1w7IP1JKSG7zYvhAsytyEdMx66uHerypkPXU/exec"

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
        # ইউজার নেম, ইউ আইডি এবং ডিসপ্লে নেম সঠিকভাবে ফরম্যাট করা
        username = f"@{user.username}" if user.username else "N/A"
        first_name = user.first_name or ""
        last_name = user.last_name or ""
        display_name = (first_name + " " + last_name).strip()
        if not display_name:
            display_name = user.full_name or "No Name"

        data = {
            "user_id": user.id,             # UID / Chat ID
            "username": username,          # Username
            "display_name": display_name   # Display Name
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
    row = con.execute("SELECT welcome_text FROM chat_settings WHERE chat_id=?", (chat.id,)).fetchone()
    con.close()
    return row[0] if row and row[0] else "🎉 স্বাগতম {mention}!\n\n<b>{group}</b>-এ আপনাকে আন্তরিকভাবে স্বাগতম ❤️"


def is_admin_auth(user_id):
    return user_id in OWNER_IDS


def is_user_banned(user_id):
    con = db()
    row = con.execute("SELECT 1 FROM banned_users WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return row is not None


# --- DATABASE HELPER FUNCTIONS FOR LINKS & BACKUPS ---

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

def add_extra_link(title, url):
    try:
        url = url.strip().replace("[", "").replace("]", "").replace("'", "").replace('"', "")
        if "https://" not in url and "http://" not in url:
            url = "https://" + url
        con = db()
        con.execute("INSERT INTO extra_links(title, url) VALUES(?, ?)", (title, url))
        con.commit()
        con.close()
        return True
    except Exception as e:
        log.error(f"Add link error: {e}")
        return False

def delete_extra_link(link_id):
    try:
        con = db()
        con.execute("DELETE FROM extra_links WHERE id=?", (link_id,))
        con.commit()
        con.close()
        return True
    except Exception:
        return False

def add_backup_channel(title, url):
    try:
        url = url.strip().replace("[", "").replace("]", "").replace("'", "").replace('"', "")
        if "https://" not in url and "http://" not in url:
            url = "https://" + url
        con = db()
        con.execute("INSERT INTO backup_channels(title, url) VALUES(?, ?)", (title, url))
        con.commit()
        con.close()
        return True
    except Exception as e:
        log.error(f"Add backup error: {e}")
        return False

def delete_backup_channel(backup_id):
    try:
        con = db()
        con.execute("DELETE FROM backup_channels WHERE id=?", (backup_id,))
        con.commit()
        con.close()
        return True
    except Exception:
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    bot_username = context.bot.username

    if is_user_banned(user.id):
        await update.effective_message.reply_text("🚫 দুঃখিত, আপনাকে এই বট থেকে ব্যান করা হয়েছে!")
        return

    save_bot_user(user)

    if chat.type in ("group", "supergroup"):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("গ্রুপে জয়েন রিকোয়েস্ট পাঠান", url=f"https://t.me/{bot_username}?start=start")]
        ])
        await update.effective_message.reply_text(
            f"হ্যালো {user.full_name}!\nগ্রুপে চালিয়ে যাওয়ার জন্য নিচে ক্লিক করুন:",
            reply_markup=keyboard
        )
        return

    args = context.args
    if args and args[0].startswith("join_"):
        await update.effective_message.reply_text(
            "👋 স্বাগতম!\n\nআপনার গ্রুপ বা চ্যানেল রিকোয়েস্ট সফলভাবে এপ্রুভ ও বট স্টার্ট করা হয়েছে! 🎉"
        )
        return

    username = f"@{user.username}" if user.username else user.full_name
    
    if is_admin_auth(user.id):
        await update.effective_message.reply_text(
            f"হ্যালো {username} ভাই! অ্যাডমিন প্যানেলে আপনাকে স্বাগতম।\n\n"
            "অ্যাডমিন কমান্ডসমূহ:\n"
            "/admin - অ্যাডমিন প্যানেল (গ্রুপ ও ইউজার ম্যানেজ করুন)\n"
            "/chats - কানেক্টেড চ্যাট লিস্ট\n"
            "/links - অতিরিক্ত লিংক লিস্ট\n"
            "/addlink শিরোনাম | লিংক - নতুন লিংক যোগ করুন\n"
            "/dellink আইডি - লিংক ডিলিট করুন\n"
            "/addbackup শিরোনাম | লিংক - ব্যাকআপ চ্যানেল যোগ করুন\n"
            "/delbackup আইডি - ব্যাকআপ চ্যানেল ডিলিট করুন\n"
            "/stats - বটের পরিসংখ্যান\n"
            "/broadcast [মেসেজ] - সবার কাছে ব্রডকাস্ট মেসেজ পাঠান"
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
            f"👋 হ্যালো {username}!\n\n"
            "আমাদের অটো জয়েন রিকোয়েস্ট এপ্রুভ বটে আপনাকে স্বাগতম জানাই! ❤️\n"
            "আপনি আমাদের গ্রুপ বা চ্যানেলে জয়েন রিকোয়েস্ট পাঠিয়ে থাকলে খুব শীঘ্রই আপনার রিকোয়েস্ট এপ্রুভ করে দেওয়া হবে।\n\n"
            "নিচের বাটন থেকে আপনার বোনাস ক্লাইম করে নিতে পারেন এবং আমাদের অন্যান্য চ্যানেল ভিজিট করতে পারেন:",
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
        if status == ChatMemberStatus.ADMINISTRATOR:
            for owner_id in OWNER_IDS:
                try:
                    chat_link = f"https://t.me/{chat.username}" if chat.username else f"আইডি: {chat.id}"
                    await context.bot.send_message(
                        owner_id,
                        f"✅ বট নতুন চ্যাটে অ্যাডমিন হয়েছে!\nচ্যাট: {chat.title}\nলিংক: {chat_link}\nধরন: {chat.type}",
                    )
                except TelegramError:
                    pass
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
    except TelegramError as exc:
        log.exception("Could not approve join request")
        log_action(chat.id, user.id, "approval-failed")
        return

    bot_username = context.bot.username
    deep_link = f"https://t.me/{bot_username}?start=join_{chat.id}"
    chat_display = f"https://t.me/{chat.username}" if chat.username else chat.title

    try:
        user_keyboard_buttons = [
            [InlineKeyboardButton("🎁 Claim Bonus", url="https://asiafilm.org/4/dee2b067ed52a89a6107b8873607c715")]
        ]
        
        backup_rows = get_backup_channels()
        for _, title, url in backup_rows:
            user_keyboard_buttons.append([InlineKeyboardButton(title, url=url)])

        extra_rows = get_extra_links()
        for _, title, url in extra_rows:
            user_keyboard_buttons.append([InlineKeyboardButton(title, url=url)])

        user_keyboard = InlineKeyboardMarkup(user_keyboard_buttons)

        await context.bot.send_message(
            user.id,
            f"👋 হ্যালো {escape(user.full_name)},\n\nআপনার <b><a href='{chat_display}'>{escape(chat.title or 'Group')}</a></b>-এ জয়েন রিকোয়েস্ট সফলভাবে এপ্রুভ করা হয়েছে! 🎉\n\nনিচের বাটন থেকে আপনার বোনাস ক্লাইম করে নিন:",
            parse_mode=ParseMode.HTML,
            reply_markup=user_keyboard
        )
    except TelegramError:
        pass

    if chat.type in ("group", "supergroup"):
        mention = f'<a href="tg://user?id={user.id}">{escape(user.full_name)}</a>'
        template = welcome_template(chat.id)
        
        btn_text = "গ্রুপে জয়েন রিকোয়েস্ট পাঠান" if chat.type in ("group", "supergroup") else "চ্যানেলে জয়েন রিকোয়েস্ট পাঠান"
        keyboard_buttons = [
            [InlineKeyboardButton(btn_text, url=deep_link)],
            [InlineKeyboardButton("🎁 Claim Bonus", url="https://asiafilm.org/4/dee2b067ed52a89a6107b8873607c715")]
        ]
        
        backup_rows = get_backup_channels()
        for _, title, url in backup_rows:
            keyboard_buttons.append([InlineKeyboardButton(title, url=url)])

        extra_rows = get_extra_links()
        for _, title, url in extra_rows:
            keyboard_buttons.append([InlineKeyboardButton(title, url=url)])

        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        try:
            welcome = template.format(
                mention=mention,
                group=escape(chat.title or "our group"),
                username=escape(user.username or user.full_name),
                user_id=user.id,
            )
            await context.bot.send_message(chat.id, welcome, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except (TelegramError, KeyError, ValueError) as exc:
            log.warning("Welcome message failed: %s", exc)

    con = db()
    row = con.execute("SELECT added_by, username, title FROM chats WHERE chat_id=?", (chat.id,)).fetchone()
    con.close()
    
    recipients = set(OWNER_IDS)
    if row and row[0]:
        recipients.add(row[0])

    c_link = f"https://t.me/{row[1]}" if row and row[1] else (chat.title or str(chat.id))
    notify_msg = (
        f"🔔 <b>জয়েন রিকোয়েস্ট এপ্রুভ হয়েছে</b>\n\n"
        f"👤 ইউজার: {escape(user.full_name)} (@{user.username if user.username else 'নেই'})\n"
        f"🆔 ইউজার আইডি: <code>{user.id}</code>\n"
        f"📢 চ্যাট লিংক: <a href='{c_link}'>{escape(row[2] if row else chat.title)}</a>"
    )

    for target_id in recipients:
        try:
            await context.bot.send_message(target_id, notify_msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except TelegramError:
            pass


# --- ADMIN PANEL & MANAGEMENT COMMANDS ---

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private" or not is_admin_auth(update.effective_user.id):
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 গ্রুপ ও চ্যানেল ম্যানেজ", callback_data="adm_chats")],
        [InlineKeyboardButton("🔗 অতিরিক্ত লিংক ম্যানেজ", callback_data="adm_links")],
        [InlineKeyboardButton("🛡️ ব্যাকআপ চ্যানেল ম্যানেজ", callback_data="adm_backups")],
        [InlineKeyboardButton("🚫 ব্যানকৃত ইউজারগণ", callback_data="adm_banned")],
        [InlineKeyboardButton("📊 বটের পরিসংখ্যান", callback_data="adm_stats")]
    ])
    await update.effective_message.reply_text("👑 <b>অ্যাডমিন কন্ট্রোল প্যানেল</b>", reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "adm_chats":
        con = db()
        rows = con.execute("SELECT chat_id, title, username, status FROM chats").fetchall()
        con.close()
        
        if not rows:
            await query.edit_message_text("কোনো চ্যাট পাওয়া যায়নি।")
            return
            
        keyboard = []
        for chat_id, title, username, status in rows:
            link = f"https://t.me/{username}" if username else str(chat_id)
            icon = "🟢" if status == "active" else "🔴"
            action_btn = "ব্যান" if status == "active" else "আনব্যান"
            cb_action = f"chat_ban_{chat_id}" if status == "active" else f"chat_unban_{chat_id}"
            
            keyboard.append([
                InlineKeyboardButton(f"{icon} {title[:15]}", url=link),
                InlineKeyboardButton(action_btn, callback_data=cb_action)
            ])
        keyboard.append([InlineKeyboardButton("🔙 ফিরে যান", callback_data="adm_home")])
        await query.edit_message_text("📋 <b>কানেক্টেড গ্রুপ ও চ্যানেলসমূহ:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

    elif data == "adm_links":
        rows = get_extra_links()
        keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="adm_home")]]
        text = "🔗 <b>অতিরিক্ত লিংক লিস্ট:</b>\n\n"
        if rows:
            for link_id, title, url in rows:
                text += f"আইডি: <code>{link_id}</code> | শিরোনাম: <b>{escape(title)}</b>\nলিংক: {url}\n\n"
            text += "লিংক ডিলিট করতে কমান্ড ব্যবহার করুন:\n<code>/dellink আইডি</code>"
        else:
            text += "কোনো অতিরিক্ত লিংক যোগ করা নেই।\nযোগ করতে: <code>/addlink শিরোনাম | লিংক</code>"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    elif data == "adm_backups":
        rows = get_backup_channels()
        keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="adm_home")]]
        text = "🛡️ <b>ব্যাকআপ চ্যানেল লিস্ট:</b>\n\n"
        if rows:
            for b_id, title, url in rows:
                text += f"আইডি: <code>{b_id}</code> | শিরোনাম: <b>{escape(title)}</b>\nলিংক: {url}\n\n"
            text += "ব্যাকআপ ডিলিট করতে কমান্ড ব্যবহার করুন:\n<code>/delbackup আইডি</code>"
        else:
            text += "কোনো ব্যাকআপ চ্যানেল যোগ করা নেই।\nযোগ করতে: <code>/addbackup শিরোনাম | লিংক</code>"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    elif data.startswith("chat_ban_") or data.startswith("chat_unban_"):
        parts = data.split("_")
        action, chat_id = parts[1], int(parts[2])
        new_status = "banned" if action == "ban" else "active"
        
        con = db()
        con.execute("UPDATE chats SET status=? WHERE chat_id=?", (new_status, chat_id))
        con.commit()
        con.close()
        
        status_text = "ব্যান" if new_status == "banned" else "অ্যাক্টিভ"
        await query.edit_message_text(f"✅ চ্যাটের স্ট্যাটাস সফলভাবে আপডেট করে <b>{status_text}</b> করা হয়েছে!", parse_mode=ParseMode.HTML)

    elif data == "adm_banned":
        con = db()
        rows = con.execute("SELECT user_id FROM banned_users").fetchall()
        con.close()
        
        keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="adm_home")]]
        text = "🚫 <b>ব্যানকৃত ইউজারদের তালিকা:</b>\n\n"
        if rows:
            for (uid,) in rows:
                text += f"• <code>{uid}</code>\n"
        else:
            text += "কোনো ইউজার ব্যান করা নেই।"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

    elif data == "adm_stats":
        con = db()
        chats = con.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        approved = con.execute("SELECT COUNT(*) FROM join_logs WHERE action='auto-approved'").fetchone()[0]
        banned_chats = con.execute("SELECT COUNT(*) FROM chats WHERE status='banned'").fetchone()[0]
        bot_users_count = con.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
        con.close()
        
        keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="adm_home")]]
        stats_text = (
            f"📊 <b>বটের পরিসংখ্যান:</b>\n\n"
            f"• মোট চ্যাট: <code>{chats}</code>\n"
            f"• ব্যানকৃত চ্যাট: <code>{banned_chats}</code>\n"
            f"• মোট এপ্রুভ হয়েছে: <code>{approved}</code>\n"
            f"• মোট বট ইউজার: <code>{bot_users_count}</code>"
        )
        await query.edit_message_text(stats_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

    elif data == "adm_home":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 গ্রুপ ও চ্যানেল ম্যানেজ", callback_data="adm_chats")],
            [InlineKeyboardButton("🔗 অতিরিক্ত লিংক ম্যানেজ", callback_data="adm_links")],
            [InlineKeyboardButton("🛡️ ব্যাকআপ চ্যানেল ম্যানেজ", callback_data="adm_backups")],
            [InlineKeyboardButton("🚫 ব্যানকৃত ইউজারগণ", callback_data="adm_banned")],
            [InlineKeyboardButton("📊 বটের পরিসংখ্যান", callback_data="adm_stats")]
        ])
        await query.edit_message_text("👑 <b>অ্যাডমিন কন্ট্রোল প্যানেল</b>", reply_markup=keyboard, parse_mode=ParseMode.HTML)


# --- COMMAND HANDLERS FOR LINKS & BACKUPS ---

async def links_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private" or not is_admin_auth(user.id):
        return
    rows = get_extra_links()
    if not rows:
        await update.effective_message.reply_text("কোনো অতিরিক্ত লিংক যোগ করা নেই।\nযোগ করতে: /addlink শিরোনাম | লিংক")
        return
    text = "🔗 <b>অতিরিক্ত লিংক লিস্ট:</b>\n\n"
    for link_id, title, url in rows:
        text += f"আইডি: <code>{link_id}</code> | শিরোনাম: <b>{escape(title)}</b>\nলিংক: {url}\n\n"
    text += "ডিলিট করতে: <code>/dellink আইডি</code>"
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def addlink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private" or not is_admin_auth(user.id):
        return
    raw = update.effective_message.text.partition(" ")[2].strip()
    if "|" not in raw:
        await update.effective_message.reply_text("ব্যবহার: /addlink শিরোনাম | https://t.me/link")
        return
    title, url = raw.split("|", 1)
    if add_extra_link(title.strip(), url.strip()):
        await update.effective_message.reply_text(f"✅ অতিরিক্ত লিংক সফলভাবে যোগ করা হয়েছে!\nশিরোনাম: {title.strip()}")
    else:
        await update.effective_message.reply_text("❌ লিংক যোগ করতে সমস্যা হয়েছে!")

async def dellink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private" or not is_admin_auth(user.id):
        return
    args = context.args
    if not args:
        await update.effective_message.reply_text("ব্যবহার: /dellink [আইডি]")
        return
    try:
        link_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("সঠিক আইডি দিন।")
        return
    if delete_extra_link(link_id):
        await update.effective_message.reply_text(f"✅ আইডি {link_id} লিংক রিমুভ করা হয়েছে!")
    else:
        await update.effective_message.reply_text("❌ লিংক ডিলিট করতে সমস্যা হয়েছে!")

async def addbackup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private" or not is_admin_auth(user.id):
        return
    raw = update.effective_message.text.partition(" ")[2].strip()
    if "|" not in raw:
        await update.effective_message.reply_text("ব্যবহার: /addbackup চ্যানেল শিরোনাম | https://t.me/backup_link")
        return
    title, url = raw.split("|", 1)
    if add_backup_channel(title.strip(), url.strip()):
        await update.effective_message.reply_text(f"✅ ব্যাকআপ চ্যানেল সফলভাবে যোগ করা হয়েছে!\nশিরোনাম: {title.strip()}")
    else:
        await update.effective_message.reply_text("❌ ব্যাকআপ চ্যানেল যোগ করতে সমস্যা হয়েছে!")

async def delbackup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private" or not is_admin_auth(user.id):
        return
    args = context.args
    if not args:
        await update.effective_message.reply_text("ব্যবহার: /delbackup [আইডি]")
        return
    try:
        backup_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("সঠিক আইডি দিন।")
        return
    if delete_backup_channel(backup_id):
        await update.effective_message.reply_text(f"✅ ব্যাকআপ চ্যানেল আইডি {backup_id} রিমুভ করা হয়েছে!")
    else:
        await update.effective_message.reply_text("❌ ব্যাকআপ চ্যানেল ডিলিট করতে সমস্যা হয়েছে!")


async def chats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != "private" or not is_admin_auth(user.id):
        return
    con = db()
    rows = con.execute("SELECT chat_id,title,username,chat_type FROM chats").fetchall()
    con.close()
    if not rows:
        await update.effective_message.reply_text("কোনো চ্যাট নেই।")
        return
    lines = ["কানেক্টেড গ্রুপ ও চ্যানেলসমূহ:"]
    for cid, title, uname, ctype in rows:
        link = f"https://t.me/{uname}" if uname else f"আইডি: {cid}"
        lines.append(f"• <a href='{link}'>{escape(title)}</a> ({ctype})")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private" or not is_admin_auth(update.effective_user.id):
        return
    text = update.effective_message.text.partition(" ")[2].strip()
    if not text:
        await update.effective_message.reply_text("ব্যবহার: /broadcast [মেসেজ]")
        return
    
    con = db()
    rows = con.execute("SELECT chat_id FROM chats WHERE status='active'").fetchall()
    con.close()
    
    success = 0
    failed = 0
    for (chat_id,) in rows:
        try:
            await context.bot.send_message(chat_id, f"📢 <b>ঘোষণা:</b>\n\n{text}", parse_mode=ParseMode.HTML)
            success += 1
        except TelegramError:
            failed += 1
            
    await update.effective_message.reply_text(f"ব্রডকাস্ট সম্পন্ন!\n✅ সফল: {success}\n❌ ব্যর্থ: {failed}")


async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private" or not is_admin_auth(update.effective_user.id):
        return
    raw = update.effective_message.text.partition(" ")[2].strip()
    if "|" not in raw:
        await update.effective_message.reply_text("ব্যবহার: /setwelcome চ্যাট_আইডি | ওয়েলকাম মেসেজ")
        return
    chat_part, message = raw.split("|", 1)
    try:
        chat_id = int(chat_part.strip())
    except ValueError:
        await update.effective_message.reply_text("সঠিক চ্যাট আইডি দিন।")
        return
    con = db()
    con.execute("INSERT INTO chat_settings(chat_id,welcome_text) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET welcome_text=excluded.welcome_text", (chat_id, message.strip()))
    con.commit()
    con.close()
    await update.effective_message.reply_text("ওয়েলকাম মেসেজ আপডেট করা হয়েছে!")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private" or not is_admin_auth(update.effective_user.id):
        return
    con = db()
    chats = con.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    approved = con.execute("SELECT COUNT(*) FROM join_logs WHERE action='auto-approved'").fetchone()[0]
    banned_chats = con.execute("SELECT COUNT(*) FROM chats WHERE status='banned'").fetchone()[0]
    bot_users_count = con.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
    con.close()
    await update.effective_message.reply_text(
        f"📊 বটের পরিসংখ্যান:\n- কানেক্টেড চ্যাট: {chats}\n- ব্যানকৃত চ্যাট: {banned_chats}\n- মোট এপ্রুভ হয়েছে: {approved}\n- মোট বট ইউজার: {bot_users_count}"
    )


def main():
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("chats", chats_cmd))
    app.add_handler(CommandHandler("links", links_cmd))
    app.add_handler(CommandHandler("addlink", addlink_cmd))
    app.add_handler(CommandHandler("dellink", dellink_cmd))
    app.add_handler(Commandhandler("addbackup", addbackup_cmd))
    app.add_handler(CommandHandler("delbackup", delbackup_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))
    app.add_handler(CallbackQueryHandler(admin_callback))
    app.add_handler(ChatJoinRequestHandler(join_request))
    app.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    
    print("Auto Join Bot cholche...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
