import os
import re
import time
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from html import escape

from dotenv import load_dotenv
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()

# ============================================================
# BLACK BERRY MEGA BOT
# Management + Protection + Welcome + Filters + Aura Point Game
# python-telegram-bot 20+
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_NAME = os.getenv(
    "DB_NAME",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "blackberry_bot.db"),
)

SPAM_LIMIT = 3
SPAM_WINDOW = 3
DEFAULT_WARN_LIMIT = 3

# Aura rules
REPLY_AURA = 1
POSITIVE_AURA = 3
NEGATIVE_AURA = -2
AURA_COOLDOWN = 2          # same user cannot gain unlimited points every instant
MAX_AURA_PER_MINUTE = 20
AURA_ROB_COOLDOWN = 30       # seconds between rob attempts by the same user

POSITIVE_WORDS = {
    "good", "great", "awesome", "amazing", "nice", "best", "love",
    "lovely", "excellent", "wonderful", "happy", "congrats", "congratulations",
    "thanks", "thank", "support", "respect", "win", "winner", "beautiful",
    "brilliant", "super", "fantastic", "helpful", "friend", "friends",
    "acha", "accha", "achha", "bahut", "pyaar", "pyar", "khush", "khushi",
    "shukriya", "dhanyavad", "badiya", "sahi", "mast", "zabardast",
    "respect", "proud", "awesome","cutie" "welcome" "thank you" "cute",
}

NEGATIVE_WORDS = {
    "bad", "hate", "stupid", "idiot", "loser", "worst", "useless",
    "angry", "sad", "dislike", "trash", "shut", "fake", "scam",
    "bura", "gussa", "nafrat", "bewakoof", "pagal", "bekar", "bakwas",
    "ghatiya", "jhuth", "jhooth", "fraud","gawar","bevkoof" "kutta"

}

# Add your own words here if you want stronger group filtering.
DEFAULT_BAD_WORDS = {
    "badword1", "badword2"
}

db = sqlite3.connect(DB_NAME, check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL")
cursor = db.cursor()


def commit():
    db.commit()


# Backward-compatible migration for existing SQLite databases.
def migrate_filter_columns():
    for sql in (
        "ALTER TABLE advanced_filters ADD COLUMN trigger_type TEXT DEFAULT 'keyword'",
        "ALTER TABLE advanced_filters ADD COLUMN trigger_user_id INTEGER DEFAULT NULL",
    ):
        try:
            cursor.execute(sql)
        except sqlite3.OperationalError:
            pass
    db.commit()


def init_db():
    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS approved_users (
        chat_id INTEGER, user_id INTEGER, name TEXT,
        approved_by INTEGER, created_at INTEGER,
        PRIMARY KEY(chat_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS warnings (
        chat_id INTEGER, user_id INTEGER, warns INTEGER DEFAULT 0,
        PRIMARY KEY(chat_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS tracked_members (
        chat_id INTEGER, user_id INTEGER, username TEXT, full_name TEXT,
        joined_at INTEGER, last_seen INTEGER,
        PRIMARY KEY(chat_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS blocked_users (
        chat_id INTEGER, user_id INTEGER, name TEXT,
        blocked_by INTEGER, created_at INTEGER,
        PRIMARY KEY(chat_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS admin_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER, actor_id INTEGER, action TEXT,
        target_id INTEGER, target_name TEXT, created_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS group_settings (
        chat_id INTEGER PRIMARY KEY,
        welcome INTEGER DEFAULT 1,
        antilink INTEGER DEFAULT 0,
        antiflood INTEGER DEFAULT 1,
        badword INTEGER DEFAULT 0,
        aura INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS custom_filters (
        chat_id INTEGER,
        word TEXT,
        PRIMARY KEY(chat_id, word)
    );

    CREATE TABLE IF NOT EXISTS advanced_filters (
        chat_id INTEGER,
        trigger TEXT,
        response_chat_id INTEGER,
        response_message_id INTEGER,
        created_by INTEGER,
        created_at INTEGER,
        trigger_type TEXT DEFAULT 'keyword',
        trigger_user_id INTEGER DEFAULT NULL,
        PRIMARY KEY(chat_id, trigger)
    );

    CREATE TABLE IF NOT EXISTS aura_points (
        user_id INTEGER PRIMARY KEY,
        total_points INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS group_aura (
        chat_id INTEGER,
        user_id INTEGER,
        username TEXT,
        full_name TEXT,
        points INTEGER DEFAULT 0,
        last_earned INTEGER DEFAULT 0,
        PRIMARY KEY(chat_id, user_id)
    );
 
    CREATE TABLE IF NOT EXISTS identity_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        user_id INTEGER,
        username TEXT,
        full_name TEXT,
        recorded_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS aura_protection (
        chat_id INTEGER,
        user_id INTEGER,
        protected_by INTEGER,
        created_at INTEGER,
        PRIMARY KEY(chat_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS aura_daily_loss (
        chat_id INTEGER,
        user_id INTEGER,
        loss_day TEXT,
        lost_points INTEGER DEFAULT 0,
        PRIMARY KEY(chat_id, user_id, loss_day)
    );
    """)
    commit()


init_db()
migrate_filter_columns()

spam_tracker = defaultdict(list)
aura_tracker = defaultdict(list)
aura_rob_tracker = defaultdict(list)


def ensure_settings(chat_id):
    cursor.execute("SELECT 1 FROM group_settings WHERE chat_id=?", (chat_id,))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO group_settings(chat_id) VALUES(?)", (chat_id,)
        )
        commit()


def get_setting(chat_id, key):
    ensure_settings(chat_id)
    cursor.execute(f"SELECT {key} FROM group_settings WHERE chat_id=?", (chat_id,))
    row = cursor.fetchone()
    return bool(row[0]) if row else False


def set_setting(chat_id, key, value):
    ensure_settings(chat_id)
    cursor.execute(
        f"UPDATE group_settings SET {key}=? WHERE chat_id=?",
        (1 if value else 0, chat_id),
    )
    commit()


def log_action(chat_id, actor_id, action, target_id=None, target_name=None):
    cursor.execute(
        """INSERT INTO admin_logs
        (chat_id, actor_id, action, target_id, target_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (chat_id, actor_id, action, target_id, target_name, int(time.time())),
    )
    commit()


def fmt_time(ts):
    """Format Unix time in India time (IST) for stylish logs."""
    try:
        ist = timezone(timedelta(hours=5, minutes=30))
        return datetime.fromtimestamp(int(ts), tz=ist).strftime("%d-%m-%Y %I:%M:%S %p")
    except Exception:
        return str(ts)


def display_username(username):
    return f"@{escape(username)}" if username else "Not available"


def get_group_aura(chat_id, user_id):
    cursor.execute(
        "SELECT points FROM group_aura WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = cursor.fetchone()
    return row[0] if row else 0


def get_global_aura(user_id):
    cursor.execute("SELECT total_points FROM aura_points WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0


def is_aura_protected(chat_id, user_id):
    """True only while the 24-hour Aura Protection window is active."""
    cursor.execute(
        "SELECT created_at FROM aura_protection WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        return False
    created_at = int(row[0] or 0)
    if int(time.time()) - created_at >= AURA_PROTECTION_DURATION:
        cursor.execute(
            "DELETE FROM aura_protection WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )
        commit()
        return False
    return True


def protection_remaining(chat_id, user_id):
    cursor.execute(
        "SELECT created_at FROM aura_protection WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        return 0
    remaining = AURA_PROTECTION_DURATION - (int(time.time()) - int(row[0] or 0))
    if remaining <= 0:
        remove_aura_protection(chat_id, user_id)
        return 0
    return remaining


def protection_remaining_text(chat_id, user_id):
    remaining = protection_remaining(chat_id, user_id)
    if not remaining:
        return "EXPIRED / OFF"
    hours, rem = divmod(remaining, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"

def set_aura_protection(chat_id, user_id, protected_by):
    cursor.execute(
        "INSERT OR REPLACE INTO aura_protection(chat_id,user_id,protected_by,created_at) VALUES(?,?,?,?)",
        (chat_id, user_id, protected_by, int(time.time())),
    )
    commit()


def remove_aura_protection(chat_id, user_id):
    cursor.execute(
        "DELETE FROM aura_protection WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    commit()



def charge_aura_protection(chat_id, user, charged_by):
    """Deduct 500 Aura and start a fresh 24-hour protection window."""
    if is_aura_protected(chat_id, user.id):
        return False, "already_active"
    balance = get_group_aura(chat_id, user.id)
    if balance < AURA_PROTECTION_COST:
        return False, "insufficient"
    now = int(time.time())
    cursor.execute(
        "INSERT INTO aura_points(user_id,total_points) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET total_points=total_points-?",
        (user.id, -AURA_PROTECTION_COST, AURA_PROTECTION_COST),
    )
    cursor.execute(
        "UPDATE group_aura SET points=points-?, last_earned=? "
        "WHERE chat_id=? AND user_id=?",
        (AURA_PROTECTION_COST, now, chat_id, user.id),
    )
    set_aura_protection(chat_id, user.id, charged_by)
    commit()
    return True, "activated"


DAILY_AURA_LOSS_LIMIT = 500
AURA_PROTECTION_COST = 500
AURA_PROTECTION_DURATION = 24 * 60 * 60
IST = timezone(timedelta(hours=5, minutes=30))


def aura_day_key(ts=None):
    dt = datetime.fromtimestamp(int(ts or time.time()), tz=IST)
    return dt.strftime("%Y-%m-%d")


def get_daily_aura_loss(chat_id, user_id, ts=None):
    day = aura_day_key(ts)
    cursor.execute(
        "SELECT lost_points FROM aura_daily_loss WHERE chat_id=? AND user_id=? AND loss_day=?",
        (chat_id, user_id, day),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def apply_daily_aura_loss(chat_id, user_id, requested_loss):
    requested_loss = max(0, int(requested_loss))
    if requested_loss <= 0:
        return 0
    day = aura_day_key()
    already_lost = get_daily_aura_loss(chat_id, user_id)
    remaining = max(0, DAILY_AURA_LOSS_LIMIT - already_lost)
    allowed = min(requested_loss, remaining)
    if allowed:
        cursor.execute(
            """INSERT INTO aura_daily_loss(chat_id,user_id,loss_day,lost_points)
               VALUES(?,?,?,?)
               ON CONFLICT(chat_id,user_id,loss_day)
               DO UPDATE SET lost_points=lost_points+excluded.lost_points""",
            (chat_id, user_id, day, allowed),
        )
        commit()
    return allowed


async def is_admin(update: Update, user_id=None):
    chat = update.effective_chat
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    if user_id is None:
        user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        return False
    try:
        member = await chat.get_member(user_id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception:
        return False


async def admin_required(update: Update):
    if not update.effective_chat or update.effective_chat.type not in (
        ChatType.GROUP, ChatType.SUPERGROUP
    ):
        if update.message:
            await update.message.reply_text("❌ This command works in groups only.")
        return False

    if not await is_admin(update):
        if update.message:
            await update.message.reply_text("❌ This command is only for group admins.")
        return False

    return True


def is_approved(chat_id, user_id):
    cursor.execute(
        "SELECT 1 FROM approved_users WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    return cursor.fetchone() is not None


def track_member(user, chat_id):
    if not user or user.is_bot:
        return

    now = int(time.time())
    cursor.execute(
        "SELECT username, full_name FROM tracked_members WHERE chat_id=? AND user_id=?",
        (chat_id, user.id),
    )
    previous = cursor.fetchone()

    if previous and (previous[0] != user.username or previous[1] != user.full_name):
        cursor.execute(
            """INSERT INTO identity_history(chat_id,user_id,username,full_name,recorded_at)
               VALUES(?,?,?,?,?)""",
            (chat_id, user.id, previous[0], previous[1], now),
        )

    cursor.execute(
        """INSERT INTO tracked_members
        (chat_id,user_id,username,full_name,joined_at,last_seen)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
        username=excluded.username, full_name=excluded.full_name,
        last_seen=excluded.last_seen""",
        (chat_id, user.id, user.username, user.full_name, now, now),
    )
    commit()


def resolve_user_identifier(update, args=None):
    args = args or []

    if (
        update.message
        and update.message.reply_to_message
        and update.message.reply_to_message.from_user
    ):
        return update.message.reply_to_message.from_user

    if not args:
        return None

    raw_first = args[0].strip()
    # @username / numeric ID use the first token; full names can contain spaces.
    raw = raw_first if raw_first.startswith("@") or raw_first.lstrip("-").isdigit() else " ".join(args).strip()
    username = raw[1:] if raw.startswith("@") else raw

    if username.lstrip("-").isdigit():
        uid = int(username)
        cursor.execute(
            """SELECT user_id,username,full_name FROM tracked_members
               WHERE chat_id=? AND user_id=?""",
            (update.effective_chat.id, uid),
        )
        row = cursor.fetchone()
        return type("Target", (), {
            "id": uid,
            "username": row[1] if row else None,
            "full_name": row[2] if row else str(uid),
            "is_bot": False,
        })()

    cursor.execute(
        """SELECT user_id,username,full_name FROM tracked_members
           WHERE chat_id=? AND lower(username)=lower(?)
           ORDER BY last_seen DESC LIMIT 1""",
        (update.effective_chat.id, username),
    )
    row = cursor.fetchone()
    if row:
        return type("Target", (), {
            "id": row[0],
            "username": row[1],
            "full_name": row[2] or username,
            "is_bot": False,
        })()

    # Full-name targeting: /approve Rahul Kumar
    name_query = raw.strip()
    cursor.execute(
        """SELECT user_id,username,full_name FROM tracked_members
           WHERE chat_id=? AND lower(full_name)=lower(?)
           ORDER BY last_seen DESC LIMIT 1""",
        (update.effective_chat.id, name_query),
    )
    row = cursor.fetchone()
    if row:
        return type("Target", (), {
            "id": row[0],
            "username": row[1],
            "full_name": row[2] or name_query,
            "is_bot": False,
        })()

    return None


async def get_target(update, context):
    user = resolve_user_identifier(update, context.args)
    if not user and update.message:
        await update.message.reply_text(
            "❌ User nahi mila.\n"
            "Reply karke command bhejo, ya @username / User ID use karo."
        )
    return user


# ============================================================
# APPROVE / FREE
# ============================================================

async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_required(update):
        return

    u = await get_target(update, context)
    if not u:
        return

    cid = update.effective_chat.id
    cursor.execute(
        "INSERT OR REPLACE INTO approved_users VALUES(?,?,?,?,?)",
        (cid, u.id, u.full_name, update.effective_user.id, int(time.time())),
    )
    cursor.execute(
        "DELETE FROM blocked_users WHERE chat_id=? AND user_id=?",
        (cid, u.id),
    )
    commit()

    username = f"@{escape(u.username)}" if u.username else "Not available"
    log_action(cid, update.effective_user.id, "approve_free", u.id, u.full_name)

    await update.message.reply_text(
        "╭━━━〔 💎 BLACK BERRY MEGA 〕━━━╮\n"
        "┃ ✅ USER APPROVED & FREED\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 👤 Name : {escape(u.full_name)}\n"
        f"┃ 🆔 User ID : <code>{u.id}</code>\n"
        f"┃ 🔗 Username : {username}\n"
        f"┃ 👮 Approved By : {escape(update.effective_user.full_name)}\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        "┃ 🛡️ Protection : EXEMPTED\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML",
    )


async def free_command(update, context):
    await approve_command(update, context)


async def unapprove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_required(update):
        return

    u = await get_target(update, context)
    if not u:
        return

    cid = update.effective_chat.id
    cursor.execute(
        "DELETE FROM approved_users WHERE chat_id=? AND user_id=?",
        (cid, u.id),
    )
    commit()

    username = f"@{escape(u.username)}" if u.username else "Not available"
    log_action(cid, update.effective_user.id, "unapprove", u.id, u.full_name)

    await update.message.reply_text(
        "╭━━━〔 💎 BLACK BERRY MEGA 〕━━━╮\n"
        "┃ 🔒 PROTECTION RE-APPLIED\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 👤 Name : {escape(u.full_name)}\n"
        f"┃ 🆔 User ID : <code>{u.id}</code>\n"
        f"┃ 🔗 Username : {username}\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        "┃ 🛡️ Protection : ACTIVE\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML",
    )


# ============================================================
# MODERATION
# ============================================================

async def ban_command(update, context):
    if not await admin_required(update):
        return
    u = await get_target(update, context)
    if not u:
        return
    try:
        await update.effective_chat.ban_member(u.id)
        log_action(update.effective_chat.id, update.effective_user.id, "ban", u.id, u.full_name)
        await update.message.reply_text(f"🚫 Banned: {escape(u.full_name)}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ban failed: {escape(str(e))}", parse_mode="HTML")


async def unban_command(update, context):
    if not await admin_required(update):
        return
    u = await get_target(update, context)
    if not u:
        return
    try:
        await update.effective_chat.unban_member(u.id, only_if_banned=True)
        log_action(update.effective_chat.id, update.effective_user.id, "unban", u.id, u.full_name)
        await update.message.reply_text(f"✅ Unbanned: {escape(u.full_name)}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Unban failed: {escape(str(e))}", parse_mode="HTML")


async def kick_command(update, context):
    if not await admin_required(update):
        return
    u = await get_target(update, context)
    if not u:
        return
    try:
        await update.effective_chat.ban_member(u.id)
        await update.effective_chat.unban_member(u.id)
        log_action(update.effective_chat.id, update.effective_user.id, "kick", u.id, u.full_name)
        await update.message.reply_text(f"👢 Kicked: {escape(u.full_name)}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Kick failed: {escape(str(e))}", parse_mode="HTML")


async def mute_command(update, context):
    if not await admin_required(update):
        return
    u = await get_target(update, context)
    if not u:
        return
    try:
        await update.effective_chat.restrict_member(
            u.id, permissions=ChatPermissions(can_send_messages=False)
        )
        log_action(update.effective_chat.id, update.effective_user.id, "mute", u.id, u.full_name)
        await update.message.reply_text(f"🔇 Muted: {escape(u.full_name)}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Mute failed: {escape(str(e))}", parse_mode="HTML")


async def unmute_command(update, context):
    if not await admin_required(update):
        return
    u = await get_target(update, context)
    if not u:
        return
    try:
        await update.effective_chat.restrict_member(
            u.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        log_action(update.effective_chat.id, update.effective_user.id, "unmute", u.id, u.full_name)
        await update.message.reply_text(f"🔊 Unmuted: {escape(u.full_name)}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Unmute failed: {escape(str(e))}", parse_mode="HTML")


async def warn_command(update, context):
    if not await admin_required(update):
        return
    u = await get_target(update, context)
    if not u:
        return

    cid = update.effective_chat.id
    cursor.execute(
        """INSERT INTO warnings(chat_id,user_id,warns) VALUES(?,?,1)
           ON CONFLICT(chat_id,user_id) DO UPDATE SET warns=warns+1""",
        (cid, u.id),
    )
    commit()

    cursor.execute(
        "SELECT warns FROM warnings WHERE chat_id=? AND user_id=?",
        (cid, u.id),
    )
    warns = cursor.fetchone()[0]

    if warns >= DEFAULT_WARN_LIMIT:
        try:
            await update.effective_chat.ban_member(u.id)
            cursor.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (cid, u.id))
            commit()
            await update.message.reply_text(
                f"🚫 {escape(u.full_name)} reached {DEFAULT_WARN_LIMIT} warnings and was banned.",
                parse_mode="HTML",
            )
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ {escape(u.full_name)}: {warns}/{DEFAULT_WARN_LIMIT}. Ban failed.",
                parse_mode="HTML",
            )
    else:
        await update.message.reply_text(
            f"⚠️ Warning: {escape(u.full_name)} — {warns}/{DEFAULT_WARN_LIMIT}",
            parse_mode="HTML",
        )


async def unwarn_command(update, context):
    if not await admin_required(update):
        return
    u = await get_target(update, context)
    if not u:
        return
    cid = update.effective_chat.id
    cursor.execute(
        """UPDATE warnings SET warns=CASE WHEN warns>0 THEN warns-1 ELSE 0 END
           WHERE chat_id=? AND user_id=?""",
        (cid, u.id),
    )
    commit()
    await update.message.reply_text(f"✅ One warning removed from {escape(u.full_name)}", parse_mode="HTML")


async def warns_command(update, context):
    u = await get_target(update, context)
    if not u:
        return
    cursor.execute(
        "SELECT warns FROM warnings WHERE chat_id=? AND user_id=?",
        (update.effective_chat.id, u.id),
    )
    row = cursor.fetchone()
    await update.message.reply_text(
        f"⚠️ {escape(u.full_name)}: {row[0] if row else 0}/{DEFAULT_WARN_LIMIT}",
        parse_mode="HTML",
    )


async def clear_command(update, context):
    if not await admin_required(update):
        return
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("Usage: /clear 10")

    count = min(int(context.args[0]), 100)
    deleted = 0
    current = update.message.message_id

    for mid in range(current - 1, max(0, current - count - 1), -1):
        try:
            await context.bot.delete_message(update.effective_chat.id, mid)
            deleted += 1
        except Exception:
            pass

    log_action(update.effective_chat.id, update.effective_user.id, f"clear_{deleted}")
    await update.message.reply_text(
        f"╭━━━〔 🧹 CLEAR 〕━━━╮\n┃ Deleted: <b>{deleted}</b> messages\n┃ 🕒 <code>{fmt_time(time.time())}</code>\n╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML"
    )


# ============================================================
# SETTINGS / FILTERS
# ============================================================

async def setting_command(update, context, key, title):
    if not await admin_required(update):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        return await update.message.reply_text(f"Usage: /{key} on|off")
    value = context.args[0].lower() == "on"
    set_setting(update.effective_chat.id, key, value)
    log_action(update.effective_chat.id, update.effective_user.id, f"{key}_{'on' if value else 'off'}")
    await update.message.reply_text(
        f"╭━━━〔 ⚙️ {escape(title)} 〕━━━╮\n"
        f"┃ Status: <b>{'ON' if value else 'OFF'}</b>\n"
        f"┃ 👮 By: <b>{escape(update.effective_user.full_name)}</b>\n"
        f"┃ 🕒 <code>{fmt_time(time.time())}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML"
    )


async def welcome_command(update, context):
    await setting_command(update, context, "welcome", "Welcome")


async def antilink_command(update, context):
    await setting_command(update, context, "antilink", "Anti-link")


async def antiflood_command(update, context):
    await setting_command(update, context, "antiflood", "Anti-flood")


async def badword_command(update, context):
    await setting_command(update, context, "badword", "Bad-word filter")


async def aura_setting_command(update, context):
    await setting_command(update, context, "aura", "Aura Game")


async def filter_add_command(update, context):
    """Save a response message and trigger it by keyword or sender username."""
    if not await admin_required(update):
        return
    if not context.args:
        return await update.message.reply_text(
            "❌ Usage: response message par reply karke /filteradd keyword ya /filteradd @username"
        )
    if not update.message.reply_to_message:
        return await update.message.reply_text(
            "❌ Jis message ko bot response mein bheje, pehle us par reply karo."
        )
    raw = " ".join(context.args).strip()
    trigger = raw.lower()
    if len(trigger) > 100:
        return await update.message.reply_text("❌ Keyword/username 100 characters se chhota rakho.")
    # Three trigger modes:
    # 1) /filteradd hello          -> keyword from anyone
    # 2) /filteradd @rahul         -> any message from @rahul
    # 3) /filteradd @rahul hello   -> only hello from @rahul
    trigger_type = "keyword"
    if len(context.args) == 1 and raw.startswith("@"):
        trigger_type = "username"
    elif len(context.args) >= 2 and context.args[0].startswith("@"):
        trigger_type = "username_keyword"
        trigger = context.args[0].lower() + "|" + " ".join(context.args[1:]).strip().lower()
    elif len(context.args) == 1 and context.args[0].lstrip("-").isdigit():
        trigger_type = "user_id"
        trigger = context.args[0].strip()
    elif len(context.args) >= 2 and context.args[0].lstrip("-").isdigit():
        trigger_type = "user_id_keyword"
        trigger = context.args[0].strip() + "|" + " ".join(context.args[1:]).strip().lower()
    cid = update.effective_chat.id
    reply_msg = update.message.reply_to_message
    cursor.execute(
        """INSERT OR REPLACE INTO advanced_filters
        (chat_id,trigger,response_chat_id,response_message_id,created_by,created_at,trigger_type,trigger_user_id)
        VALUES(?,?,?,?,?,?,?,?)""",
        (cid, trigger, cid, reply_msg.message_id, update.effective_user.id, int(time.time()), trigger_type, None),
    )
    commit()
    if trigger_type == "username":
        mode = "👤 Username"
    elif trigger_type == "username_keyword":
        mode = "👤 Username + Keyword"
    elif trigger_type == "user_id":
        mode = "🆔 User ID"
    elif trigger_type == "user_id_keyword":
        mode = "🆔 User ID + Keyword"
    else:
        mode = "🔑 Keyword"
    # Use send_message instead of reply_text here. The command message can be
    # unavailable by the time Telegram processes the confirmation, which causes
    # BadRequest: Message to be replied not found.
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "╭━━━〔 🔎 FILTER SAVED 〕━━━╮\n"
            f"┃ {mode}: <b>{escape(trigger)}</b>\n"
            "┃ ⚡ Auto response: ON\n"
            "┃ 📦 Response: replied message/media\n"
            "┃ 🖼️ Photo • Sticker • Video • GIF • Document supported\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯"
        ),
        parse_mode="HTML",
    )


async def filter_remove_command(update, context):
    if not await admin_required(update):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /filterdel word")
    trigger = " ".join(context.args).strip().lower()
    cid = update.effective_chat.id
    cursor.execute("DELETE FROM advanced_filters WHERE chat_id=? AND trigger=?", (cid, trigger))
    cursor.execute("DELETE FROM custom_filters WHERE chat_id=? AND word=?", (cid, trigger))
    commit()
    log_action(cid, update.effective_user.id, "filter_delete", None, trigger)
    await update.message.reply_text(f"🗑️ Filter removed: <b>{escape(trigger)}</b>", parse_mode="HTML")


async def filters_command(update, context):
    if not await admin_required(update):
        return
    cid = update.effective_chat.id
    cursor.execute(
        "SELECT trigger,created_at,COALESCE(trigger_type,'keyword') FROM advanced_filters WHERE chat_id=? ORDER BY trigger COLLATE NOCASE",
        (cid,),
    )
    rows = cursor.fetchall()
    if not rows:
        return await update.message.reply_text("🔎 Is group mein koi custom auto-reply filter nahi hai.")
    lines = ["╭━━━〔 🔎 FILTER LIST 〕━━━╮"]
    for i, (trigger, created, trigger_type) in enumerate(rows, 1):
        icon = "👤" if trigger_type in ("username", "username_keyword") else ("🆔" if trigger_type in ("user_id", "user_id_keyword") else "🔑")
        lines.append(f"┃ {i}. {icon} <b>{escape(trigger)}</b> — {fmt_time(created)}")
    lines.append("╰━━━━━━━━━━━━━━━━━━━━╯")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def dmute_command(update, context):
    """Mute a target and remove the admin's command message."""
    if not await admin_required(update):
        return
    u = await get_target(update, context)
    if not u:
        return
    try:
        await update.effective_chat.restrict_member(
            u.id, permissions=ChatPermissions(can_send_messages=False)
        )
        log_action(update.effective_chat.id, update.effective_user.id, "dmute", u.id, u.full_name)
        try:
            await update.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            update.effective_chat.id,
            "╭━━━〔 🔇 DMUTE 〕━━━╮\n"
            f"┃ 👤 <b>{escape(u.full_name)}</b>\n"
            f"┃ 🆔 <code>{u.id}</code>\n"
            "┃ 🔒 Status: <b>MUTED</b>\n"
            f"┃ 👮 By: <b>{escape(update.effective_user.full_name)}</b>\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ DMute failed: {escape(str(e))}", parse_mode="HTML")


# ============================================================
# AURA POINT GAME
# ============================================================

def update_aura(chat_id, user, delta):
    now = int(time.time())

    # Aura Protection blocks Aura decreases, not normal gains.
    if delta < 0 and is_aura_protected(chat_id, user.id):
        return 0

    # Per-minute anti-farm guard
    key = (chat_id, user.id)
    aura_tracker[key] = [
        t for t in aura_tracker[key] if now - t < 60
    ]

    if delta > 0 and len(aura_tracker[key]) >= MAX_AURA_PER_MINUTE:
        return 0

    if delta < 0:
        allowed_loss = apply_daily_aura_loss(chat_id, user.id, -delta)
        if allowed_loss <= 0:
            return 0
        delta = -allowed_loss

    cursor.execute(
        """INSERT INTO aura_points(user_id,total_points) VALUES(?,?)
           ON CONFLICT(user_id) DO UPDATE SET total_points=total_points+excluded.total_points""",
        (user.id, delta),
    )

    cursor.execute(
        """INSERT INTO group_aura(chat_id,user_id,username,full_name,points,last_earned)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(chat_id,user_id) DO UPDATE SET
           username=excluded.username,
           full_name=excluded.full_name,
           points=group_aura.points+excluded.points,
           last_earned=excluded.last_earned""",
        (chat_id, user.id, user.username, user.full_name, delta, now),
    )
    commit()

    if delta > 0:
        aura_tracker[key].append(now)

    return delta


def aura_for_text(text):
    words = set(re.findall(r"[a-zA-Z\u0900-\u097F]+", (text or "").lower()))
    positive = len(words & POSITIVE_WORDS)
    negative = len(words & NEGATIVE_WORDS)

    if positive and negative:
        return 0, positive, negative
    if positive:
        return POSITIVE_AURA, positive, 0
    if negative:
        return NEGATIVE_AURA, 0, negative
    return 0, 0, 0


async def aura_command(update, context):
    """Aura profile plus give/rob/protect/unprotect subcommands."""
    if not update.effective_user or not update.effective_chat or not update.message:
        return

    if context.args:
        sub = context.args[0].lower()

        if sub in ("balance", "bal", "points"):
            return await aura_balance_command(update, context)

        if sub in ("give", "rob"):
            if update.message.reply_to_message:
                target = update.message.reply_to_message.from_user
                amount_raw = context.args[1] if len(context.args) >= 2 else ""
            elif len(context.args) >= 3:
                target = resolve_user_identifier(update, [context.args[1]])
                amount_raw = context.args[2]
            else:
                return await update.message.reply_text(
                    "💎 Usage: Reply → /aura give 10 | Reply → /aura rob 10\n"
                    "or /aura give @username 10"
                )

            if not target:
                return await update.message.reply_text("❌ User nahi mila. Reply, @username ya User ID use karo.")
            try:
                amount = int(amount_raw)
                if amount <= 0 or amount > 10000:
                    raise ValueError
            except ValueError:
                return await update.message.reply_text("❌ Aura amount 1–10000 ke beech hona chahiye.")

            if sub == "give":
                if not await is_admin(update):
                    return await update.message.reply_text("❌ /aura give sirf group admins use kar sakte hain.")
                change_group_and_global_aura(update.effective_chat.id, target, amount)
                log_action(update.effective_chat.id, update.effective_user.id, "aura_give", target.id, target.full_name)
                return await send_stylish_aura_result(
                    update, "💎 AURA GIVEN", target, amount,
                    f"👮 By: {escape(update.effective_user.full_name)}"
                )

            if target.id == update.effective_user.id:
                return await update.message.reply_text("❌ Khud ko rob nahi kar sakte.")
            if target.is_bot:
                return await update.message.reply_text("❌ Bot ko rob nahi kar sakte.")
            if is_aura_protected(update.effective_chat.id, target.id):
                return await update.message.reply_text(
                    f"🛡️ <b>{escape(target.full_name)}</b> Aura Protected hai.",
                    parse_mode="HTML"
                )

            rkey = (update.effective_chat.id, update.effective_user.id)
            now = int(time.time())
            aura_rob_tracker[rkey] = [t for t in aura_rob_tracker[rkey] if now - t < AURA_ROB_COOLDOWN]
            if aura_rob_tracker[rkey]:
                left = AURA_ROB_COOLDOWN - (now - max(aura_rob_tracker[rkey]))
                return await update.message.reply_text(f"⏳ Rob cooldown: {max(1, left)}s")

            available = get_group_aura(update.effective_chat.id, target.id)
            stolen = min(amount, max(0, available))
            if stolen <= 0:
                return await update.message.reply_text("💨 Target ke paas rob karne ke liye Aura nahi hai.")

            actual_stolen = -change_group_and_global_aura(
                update.effective_chat.id, target, -stolen
            )
            if actual_stolen <= 0:
                return await update.message.reply_text(
                    f"🛡️ <b>{escape(target.full_name)}</b> ne aaj ka {DAILY_AURA_LOSS_LIMIT} Aura loss limit reach kar liya hai.",
                    parse_mode="HTML"
                )
            robber_before_group = get_group_aura(update.effective_chat.id, update.effective_user.id)
            robber_before_global = get_global_aura(update.effective_user.id)
            change_group_and_global_aura(update.effective_chat.id, update.effective_user, actual_stolen)
            stolen = actual_stolen
            target_after_group = get_group_aura(update.effective_chat.id, target.id)
            target_after_global = get_global_aura(target.id)
            robber_after_group = get_group_aura(update.effective_chat.id, update.effective_user.id)
            robber_after_global = get_global_aura(update.effective_user.id)
            aura_rob_tracker[rkey].append(now)
            log_action(update.effective_chat.id, update.effective_user.id, "aura_rob", target.id, target.full_name)

            return await update.message.reply_text(
                "╭━━━〔 💥 AURA ROB 〕━━━╮\n"
                f"┃ 🦹 Robber: <b>{escape(update.effective_user.full_name)}</b>\n"
                f"┃ 🎯 Target: <b>{escape(target.full_name)}</b>\n"
                f"┃ 🆔 Target ID: <code>{target.id}</code>\n"
                f"┃ 🎯 Target Before: <b>{target_after_group + stolen}</b> 💎 | <b>{target_after_global + stolen}</b> 🌍\n"
                f"┃ ➖ Target Subtracted: <b>-{stolen}</b> Aura\n"
                f"┃ 🎯 Target After: <b>{target_after_group}</b> 💎 | <b>{target_after_global}</b> 🌍\n"
                f"┃ 🦹 Robber Before: <b>{robber_before_group}</b> 💎 | <b>{robber_before_global}</b> 🌍\n"
                f"┃ ➕ Robber Added: <b>+{stolen}</b> Aura\n"
                f"┃ 🦹 Robber After: <b>{robber_after_group}</b> 💎 | <b>{robber_after_global}</b> 🌍\n"
                f"┃ 🕒 Time: <code>{fmt_time(now)}</code>\n"
                "╰━━━━━━━━━━━━━━━━━━━━╯",
                parse_mode="HTML"
            )

        if sub in ("protect", "unprotect"):
            if update.message.reply_to_message:
                target = update.message.reply_to_message.from_user
            elif len(context.args) > 1:
                target = resolve_user_identifier(update, [context.args[1]])
            else:
                target = update.effective_user

            if not target:
                return await update.message.reply_text("❌ User nahi mila.")
            if target.id != update.effective_user.id and not await is_admin(update):
                return await update.message.reply_text("❌ Kisi aur ko protect/unprotect karne ke liye admin hona zaroori hai.")

            if sub == "protect":
                set_aura_protection(update.effective_chat.id, target.id, update.effective_user.id)
                log_action(update.effective_chat.id, update.effective_user.id, "aura_protect", target.id, target.full_name)
                return await update.message.reply_text(
                    "╭━━━〔 🛡️ AURA PROTECTION 〕━━━╮\n"
                    f"┃ 👤 <b>{escape(target.full_name)}</b>\n"
                    f"┃ 🆔 <code>{target.id}</code>\n"
                    "┃ 🔒 Rob Protection: <b>ACTIVE</b>\n"
                    f"┃ 📅 Day: <b>{datetime.now(IST).strftime("%A")}</b>\n"
                    f"┃ 🕒 Time: <code>{fmt_time(time.time())}</code>\n"
                    "╰━━━━━━━━━━━━━━━━━━━━╯",
                    parse_mode="HTML"
                )

            remove_aura_protection(update.effective_chat.id, target.id)
            log_action(update.effective_chat.id, update.effective_user.id, "aura_unprotect", target.id, target.full_name)
            return await update.message.reply_text(
                f"🔓 <b>{escape(target.full_name)}</b> Aura Protection removed.",
                parse_mode="HTML"
            )

        if sub in ("on", "off"):
            if not await admin_required(update):
                return
            value = sub == "on"
            set_setting(update.effective_chat.id, "aura", value)
            return await update.message.reply_text(
                f"✨ Aura Game: <b>{'ON' if value else 'OFF'}</b>",
                parse_mode="HTML"
            )

    target = update.effective_user
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user

    group_points = get_group_aura(update.effective_chat.id, target.id)
    global_points = get_global_aura(target.id)
    protected = is_aura_protected(update.effective_chat.id, target.id)

    buttons = [
        [
            InlineKeyboardButton("💎 Top Aura", callback_data="aura_top"),
            InlineKeyboardButton("🌍 Worldwide", callback_data="aura_global"),
        ],
        [InlineKeyboardButton(
            "🛡️ Protected" if protected else "🛡️ Protect",
            callback_data=f"aura_protect:{target.id}"
        )],
    ]
    await update.message.reply_text(
        "╭━━━〔 ✨ AURA PROFILE 〕━━━╮\n"
        f"┃ 👤 <b>{escape(target.full_name)}</b>\n"
        f"┃ 🆔 <code>{target.id}</code>\n"
        f"┃ 🔗 {display_username(target.username)}\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 💎 Group Aura : <b>{group_points}</b>\n"
        f"┃ 🌍 Worldwide : <b>{global_points}</b>\n"
        f"┃ 🛡️ Rob Shield : <b>{'ON' if protected else 'OFF'}</b>\n"
        f"┃ ⏳ Shield Left : <b>{protection_remaining_text(update.effective_chat.id, target.id)}</b>\n"
        f"┃ 📉 Daily Loss : <b>{get_daily_aura_loss(update.effective_chat.id, target.id)}/{DAILY_AURA_LOSS_LIMIT}</b>\n"
        f"┃ 📅 Day : <b>{datetime.now(IST).strftime("%A")}</b>\n"
        f"┃ 🕒 Checked : <code>{fmt_time(time.time())}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def change_group_and_global_aura(chat_id, user, delta):
    now = int(time.time())
    if delta < 0 and is_aura_protected(chat_id, user.id):
        return 0
    if delta < 0:
        allowed_loss = apply_daily_aura_loss(chat_id, user.id, -delta)
        if allowed_loss <= 0:
            return 0
        delta = -allowed_loss
    cursor.execute(
        """INSERT INTO aura_points(user_id,total_points) VALUES(?,?)
           ON CONFLICT(user_id) DO UPDATE SET total_points=total_points+excluded.total_points""",
        (user.id, delta),
    )
    cursor.execute(
        """INSERT INTO group_aura(chat_id,user_id,username,full_name,points,last_earned)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(chat_id,user_id) DO UPDATE SET
           username=excluded.username, full_name=excluded.full_name,
           points=group_aura.points+excluded.points,
           last_earned=excluded.last_earned""",
        (chat_id, user.id, user.username, user.full_name, delta, now),
    )
    commit()
    return delta


async def send_stylish_aura_result(update, title, target, changed, extra=""):
    cid = update.effective_chat.id
    group_after = get_group_aura(cid, target.id)
    global_after = get_global_aura(target.id)
    group_before = group_after - changed
    global_before = global_after - changed
    action_word = "Added" if changed >= 0 else "Subtracted"
    signed = f"{'+' if changed >= 0 else ''}{changed}"
    await update.message.reply_text(
        "╭━━━〔 ✨ AURA ACTION 〕━━━╮\n"
        f"┃ {title}\n"
        f"┃ 👤 <b>{escape(target.full_name)}</b>\n"
        f"┃ 🆔 <code>{target.id}</code>\n"
        f"┃ 🔗 {display_username(target.username)}\n"
        f"┃ 📊 Before: <b>{group_before}</b> 💎 | <b>{global_before}</b> 🌍\n"
        f"┃ {'➕' if changed >= 0 else '➖'} {action_word}: <b>{signed}</b>\n"
        f"┃ 📊 After: <b>{group_after}</b> 💎 | <b>{global_after}</b> 🌍\n"
        f"┃ 💎 Group Total: <b>{group_after}</b>\n"
        f"┃ 🌍 Worldwide Total: <b>{global_after}</b>\n"
        f"┃ 🕒 Time: <code>{fmt_time(time.time())}</code>\n"
        f"┃ {extra}\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML"
    )


async def aura_balance_command(update, context):
    if not update.effective_chat or not update.message:
        return
    target = update.effective_user
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user
    elif context.args:
        resolved = resolve_user_identifier(update, context.args)
        if resolved:
            target = resolved
    cid = update.effective_chat.id
    protected = is_aura_protected(cid, target.id)
    await update.message.reply_text(
        "╭━━━〔 💎 AURA BALANCE 〕━━━╮\n"
        f"┃ 👤 <b>{escape(target.full_name)}</b>\n"
        f"┃ 🆔 <code>{target.id}</code>\n"
        f"┃ 🔗 {display_username(target.username)}\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 💎 Group Total: <b>{get_group_aura(cid, target.id)}</b>\n"
        f"┃ 🌍 Worldwide Total: <b>{get_global_aura(target.id)}</b>\n"
        f"┃ 🛡️ Protection: <b>{'ACTIVE' if protected else 'OFF'}</b>\n"
        f"┃ ⏳ Protection Left: <b>{protection_remaining_text(cid, target.id)}</b>\n"
        f"┃ 📉 Daily Loss Used: <b>{get_daily_aura_loss(cid, target.id)}/{DAILY_AURA_LOSS_LIMIT}</b>\n"
        f"┃ 🕒 Checked: <code>{fmt_time(time.time())}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML",
    )


async def aura_group_balance_command(update, context):
    if not update.effective_chat or not update.message:
        return
    target = update.effective_user
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user
    elif context.args:
        resolved = resolve_user_identifier(update, context.args)
        if resolved:
            target = resolved
    cid = update.effective_chat.id
    await update.message.reply_text(
        "╭━━━〔 💎 GROUP AURA BALANCE 〕━━━╮\n"
        f"┃ 👤 <b>{escape(target.full_name)}</b>\n"
        f"┃ 🆔 <code>{target.id}</code>\n"
        f"┃ 🔗 {display_username(target.username)}\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 💎 This Group Total: <b>{get_group_aura(cid, target.id)}</b>\n"
        f"┃ 🛡️ Protection: <b>{'ACTIVE' if is_aura_protected(cid, target.id) else 'OFF'}</b>\n"
        f"┃ ⏳ Protection Left: <b>{protection_remaining_text(cid, target.id)}</b>\n"
        f"┃ 🕒 Checked: <code>{fmt_time(time.time())}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML",
    )


async def aura_worldwide_balance_command(update, context):
    if not update.effective_chat or not update.message:
        return
    target = update.effective_user
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user
    elif context.args:
        resolved = resolve_user_identifier(update, context.args)
        if resolved:
            target = resolved
    await update.message.reply_text(
        "╭━━━〔 🌍 WORLDWIDE AURA BALANCE 〕━━━╮\n"
        f"┃ 👤 <b>{escape(target.full_name)}</b>\n"
        f"┃ 🆔 <code>{target.id}</code>\n"
        f"┃ 🔗 {display_username(target.username)}\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 🌍 Worldwide Total: <b>{get_global_aura(target.id)}</b>\n"
        f"┃ 🕒 Checked: <code>{fmt_time(time.time())}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML",
    )


async def topaura_command(update, context):
    if not update.effective_chat:
        return

    cursor.execute(
        """SELECT user_id,full_name,username,points
           FROM group_aura WHERE chat_id=?
           ORDER BY points DESC LIMIT 10""",
        (update.effective_chat.id,),
    )
    rows = cursor.fetchall()

    if not rows:
        return await update.message.reply_text("✨ Abhi Aura leaderboard empty hai.")

    medals = ["🥇", "🥈", "🥉"]
    lines = ["✨ <b>TOP AURA — THIS GROUP</b> ✨", ""]
    for i, row in enumerate(rows, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        username = f" @{escape(row[2])}" if row[2] else ""
        lines.append(
            f"{medal} <b>{escape(row[1])}</b>{username} — 💎 <b>{row[3]}</b>"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def globaltopaura_command(update, context):
    cursor.execute(
        """SELECT user_id,points FROM aura_points
           ORDER BY points DESC LIMIT 10"""
    )
    rows = cursor.fetchall()

    if not rows:
        return await update.message.reply_text("🌍 Worldwide Aura leaderboard empty hai.")

    lines = ["🌍 <b>TOP AURA — WORLDWIDE</b> 🌍", ""]
    medals = ["🥇", "🥈", "🥉"]

    for i, (uid, points) in enumerate(rows, 1):
        try:
            chat = update.effective_chat
            member = await chat.get_member(uid)
            name = member.user.full_name
            username = f" @{escape(member.user.username)}" if member.user.username else ""
        except Exception:
            name = f"User {uid}"
            username = ""

        medal = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{medal} <b>{escape(name)}</b>{username} — 💎 <b>{points}</b>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def set_aura_command(update, context):
    if not await admin_required(update):
        return

    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        amount_raw = context.args[0] if context.args else ""
    elif len(context.args) >= 2:
        u = resolve_user_identifier(update, [context.args[0]])
        amount_raw = context.args[1]
    else:
        return await update.message.reply_text("💎 Usage: Reply → /setaura 100 OR /setaura @user 100")

    if not u:
        return await update.message.reply_text("❌ User nahi mila.")
    try:
        points = int(amount_raw)
        if points < 0 or points > 1000000:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("❌ Points 0–1000000 ke beech number hona chahiye.")

    cid = update.effective_chat.id
    cursor.execute(
        """INSERT INTO group_aura(chat_id,user_id,username,full_name,points,last_earned)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(chat_id,user_id) DO UPDATE SET
           username=excluded.username, full_name=excluded.full_name,
           points=excluded.points, last_earned=excluded.last_earned""",
        (cid, u.id, u.username, u.full_name, points, int(time.time())),
    )
    commit()
    log_action(cid, update.effective_user.id, "aura_set", u.id, u.full_name)
    await update.message.reply_text(
        "╭━━━〔 💎 AURA SET 〕━━━╮\n"
        f"┃ 👤 <b>{escape(u.full_name)}</b>\n"
        f"┃ 🆔 <code>{u.id}</code>\n"
        f"┃ 🔗 {display_username(u.username)}\n"
        f"┃ 💎 Group Aura: <b>{points}</b>\n"
        f"┃ 👮 By: <b>{escape(update.effective_user.full_name)}</b>\n"
        f"┃ 🕒 <code>{fmt_time(time.time())}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML"
    )


async def give_aura_command(update, context):
    if not await admin_required(update):
        return

    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        amount_raw = context.args[0] if context.args else ""
    elif len(context.args) >= 2:
        u = resolve_user_identifier(update, [context.args[0]])
        amount_raw = context.args[1]
    else:
        return await update.message.reply_text("Usage: Reply → /giveaura 10 OR /giveaura @user 10")

    if not u:
        return await update.message.reply_text("❌ User nahi mila.")
    try:
        amount = int(amount_raw)
        if amount <= 0 or amount > 10000:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("❌ Amount 1–10000 hona chahiye.")

    change_group_and_global_aura(update.effective_chat.id, u, amount)
    log_action(update.effective_chat.id, update.effective_user.id, "aura_give", u.id, u.full_name)
    await send_stylish_aura_result(update, "💎 AURA GIVEN", u, amount,
                                   f"👮 By: {escape(update.effective_user.full_name)}")


async def rob_aura_command(update, context):
    if not update.effective_chat or not update.message:
        return

    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        amount_raw = context.args[0] if context.args else ""
    elif len(context.args) >= 2:
        u = resolve_user_identifier(update, [context.args[0]])
        amount_raw = context.args[1]
    else:
        return await update.message.reply_text("Usage: Reply → /robaura 10 OR /robaura @user 10")

    if not u:
        return await update.message.reply_text("❌ User nahi mila.")
    if u.id == update.effective_user.id:
        return await update.message.reply_text("❌ Khud ko rob nahi kar sakte.")
    try:
        amount = int(amount_raw)
        if amount <= 0 or amount > 10000:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("❌ Amount 1–10000 hona chahiye.")

    cid = update.effective_chat.id
    if is_aura_protected(cid, u.id):
        return await update.message.reply_text(f"🛡️ <b>{escape(u.full_name)}</b> Aura Protected hai.", parse_mode="HTML")

    rkey = (cid, update.effective_user.id)
    now = int(time.time())
    aura_rob_tracker[rkey] = [t for t in aura_rob_tracker[rkey] if now - t < AURA_ROB_COOLDOWN]
    if aura_rob_tracker[rkey]:
        left = AURA_ROB_COOLDOWN - (now - max(aura_rob_tracker[rkey]))
        return await update.message.reply_text(f"⏳ Rob cooldown: {max(1, left)}s")

    available = get_group_aura(cid, u.id)
    stolen = min(amount, max(0, available))
    if stolen <= 0:
        return await update.message.reply_text("💨 Target ke paas rob karne ke liye Aura nahi hai.")

    actual_stolen = -change_group_and_global_aura(cid, u, -stolen)
    if actual_stolen <= 0:
        return await update.message.reply_text(
            f"🛡️ <b>{escape(u.full_name)}</b> ne aaj ka {DAILY_AURA_LOSS_LIMIT} Aura loss limit reach kar liya hai.",
            parse_mode="HTML"
        )
    robber_before_group = get_group_aura(cid, update.effective_user.id)
    robber_before_global = get_global_aura(update.effective_user.id)
    change_group_and_global_aura(cid, update.effective_user, actual_stolen)
    stolen = actual_stolen
    target_after_group = get_group_aura(cid, u.id)
    target_after_global = get_global_aura(u.id)
    robber_after_group = get_group_aura(cid, update.effective_user.id)
    robber_after_global = get_global_aura(update.effective_user.id)
    aura_rob_tracker[rkey].append(now)
    log_action(cid, update.effective_user.id, "aura_rob", u.id, u.full_name)

    await update.message.reply_text(
        "╭━━━〔 💥 AURA ROB 〕━━━╮\n"
        f"┃ 🦹 Robber: <b>{escape(update.effective_user.full_name)}</b>\n"
        f"┃ 🎯 Target: <b>{escape(u.full_name)}</b>\n"
        f"┃ 🆔 Target ID: <code>{u.id}</code>\n"
        f"┃ 🔗 Target: {display_username(u.username)}\n"
        f"┃ 🎯 Target Before: <b>{target_after_group + stolen}</b> 💎 | <b>{target_after_global + stolen}</b> 🌍\n"
        f"┃ ➖ Target Subtracted: <b>-{stolen}</b> Aura\n"
        f"┃ 🎯 Target After: <b>{target_after_group}</b> 💎 | <b>{target_after_global}</b> 🌍\n"
        f"┃ 🦹 Robber Before: <b>{robber_before_group}</b> 💎 | <b>{robber_before_global}</b> 🌍\n"
        f"┃ ➕ Robber Added: <b>+{stolen}</b> Aura\n"
        f"┃ 🦹 Robber After: <b>{robber_after_group}</b> 💎 | <b>{robber_after_global}</b> 🌍\n"
        f"┃ 🕒 Time: <code>{fmt_time(now)}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML"
    )


async def aura_protect_command(update, context):
    if not update.effective_chat or not update.message:
        return
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
    elif context.args:
        target = resolve_user_identifier(update, context.args)
    else:
        target = update.effective_user

    if not target:
        return await update.message.reply_text("❌ User nahi mila.")
    if target.id != update.effective_user.id and not await is_admin(update):
        return await update.message.reply_text("❌ Dusre user ko protect karne ke liye admin hona zaroori hai.")

    protection_before_group = get_group_aura(update.effective_chat.id, target.id)
    protection_before_global = get_global_aura(target.id)
    ok, reason = charge_aura_protection(update.effective_chat.id, target, update.effective_user.id)
    if not ok:
        if reason == "already_active":
            return await update.message.reply_text(
                f"🛡️ <b>{escape(target.full_name)}</b> ki Aura Protection already ACTIVE hai.\n"
                f"⏳ Remaining: <b>{protection_remaining_text(update.effective_chat.id, target.id)}</b>",
                parse_mode="HTML",
            )
        return await update.message.reply_text(
            f"❌ <b>{escape(target.full_name)}</b> ke paas protection ke liye {AURA_PROTECTION_COST} Aura nahi hai.\n"
            f"💎 Current Group Aura: <b>{get_group_aura(update.effective_chat.id, target.id)}</b>",
            parse_mode="HTML",
        )

    log_action(update.effective_chat.id, update.effective_user.id, "aura_protect_-500", target.id, target.full_name)
    await update.message.reply_text(
        "╭━━━〔 🛡️ AURA PROTECTION 〕━━━╮\n"
        f"┃ 👤 <b>{escape(target.full_name)}</b>\n"
        f"┃ 🆔 <code>{target.id}</code>\n"
        "┃ 🔒 Protection: <b>ACTIVE</b>\n"
        f"┃ 📊 Before: <b>{protection_before_group}</b> 💎 | <b>{protection_before_global}</b> 🌍\n"
        f"┃ ➖ Subtracted: <b>-{AURA_PROTECTION_COST}</b> Aura\n"
        f"┃ 📊 After: <b>{get_group_aura(update.effective_chat.id, target.id)}</b> 💎 | <b>{get_global_aura(target.id)}</b> 🌍\n"
        f"┃ 💎 Group Total: <b>{get_group_aura(update.effective_chat.id, target.id)}</b>\n"
        f"┃ 🌍 Worldwide Total: <b>{get_global_aura(target.id)}</b>\n"
        "┃ ⏳ Valid For: <b>24 Hours</b>\n"
        f"┃ 📅 Day: <b>{datetime.now(IST).strftime('%A')}</b>\n"
        f"┃ 🕒 Time: <code>{fmt_time(time.time())}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML"
    )


async def aura_unprotect_command(update, context):
    if not update.effective_chat or not update.message:
        return
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
    elif context.args:
        target = resolve_user_identifier(update, context.args)
    else:
        target = update.effective_user

    if not target:
        return await update.message.reply_text("❌ User nahi mila.")
    if target.id != update.effective_user.id and not await is_admin(update):
        return await update.message.reply_text("❌ Dusre user ko unprotect karne ke liye admin hona zaroori hai.")

    remove_aura_protection(update.effective_chat.id, target.id)
    log_action(update.effective_chat.id, update.effective_user.id, "aura_unprotect", target.id, target.full_name)
    await update.message.reply_text(
        "╭━━━〔 🔓 AURA UNPROTECTED 〕━━━╮\n"
        f"┃ 👤 <b>{escape(target.full_name)}</b>\n"
        f"┃ 🆔 <code>{target.id}</code>\n"
        f"┃ 🕒 <code>{fmt_time(time.time())}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML"
    )


# ============================================================
# WELCOME / MEMBER EVENTS
# ============================================================

async def member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result or not result.new_chat_member:
        return

    old = result.old_chat_member
    new = result.new_chat_member
    chat = update.effective_chat
    user = new.user

    if not chat or not user or user.is_bot:
        return

    joined = old.status in ("left", "kicked") and new.status in (
        "member", "restricted"
    )
    if joined:
        track_member(user, chat.id)
        if get_setting(chat.id, "welcome"):
            await context.bot.send_message(
                chat.id,
                "╭━━━〔 💎 BLACK BERRY MEGA 〕━━━╮\n"
                "┃ 👋 <b>WELCOME!</b>\n"
                "┣━━━━━━━━━━━━━━━━━━━━\n"
                f"┃ 👤 {escape(user.full_name)}\n"
                f"┃ 🆔 <code>{user.id}</code>\n"
                "┃ ✨ Enjoy the group and earn Aura Points!\n"
                "╰━━━━━━━━━━━━━━━━━━━━╯",
                parse_mode="HTML",
            )


# ============================================================
# MESSAGE ENGINE
# ============================================================

LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/|discord\.gg/)",
    re.IGNORECASE,
)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user or user.is_bot:
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    track_member(user, chat.id)
    ensure_settings(chat.id)

    user_admin = await is_admin(update, user.id)
    approved = is_approved(chat.id, user.id)

    text = message.text or message.caption or ""

    # --------------------------------------------------------
    # Reliable custom auto-reply filters
    # --------------------------------------------------------
    if not user_admin:
        incoming_text = (message.text or message.caption or "").strip().lower()
        incoming_username = (f"@{user.username}" if user.username else "").lower()
        incoming_user_id = str(user.id)
        cursor.execute(
            "SELECT trigger,response_chat_id,response_message_id,COALESCE(trigger_type,'keyword') FROM advanced_filters WHERE chat_id=?",
            (chat.id,),
        )
        matched = None
        for trigger, response_chat_id, response_message_id, trigger_type in cursor.fetchall():
            trigger = (trigger or "").strip().lower()
            if trigger_type == "username":
                if incoming_username == trigger:
                    matched = (response_chat_id, response_message_id)
                    break
            elif trigger_type == "username_keyword":
                parts = trigger.split("|", 1)
                if incoming_username == parts[0] and len(parts) > 1 and parts[1] in incoming_text:
                    matched = (response_chat_id, response_message_id)
                    break
            elif trigger_type == "user_id":
                if incoming_user_id == trigger:
                    matched = (response_chat_id, response_message_id)
                    break
            elif trigger_type == "user_id_keyword":
                parts = trigger.split("|", 1)
                if incoming_user_id == parts[0] and len(parts) > 1 and parts[1] in incoming_text:
                    matched = (response_chat_id, response_message_id)
                    break
            elif trigger in incoming_text:
                matched = (response_chat_id, response_message_id)
                break
        if matched:
            try:
                await context.bot.copy_message(
                    chat_id=chat.id,
                    from_chat_id=matched[0],
                    message_id=matched[1],
                    reply_to_message_id=message.message_id,
                )
            except Exception:
                # Never fail silently.
                try:
                    await message.reply_text("💎 Filter matched!")
                except Exception:
                    pass

    # --------------------------------------------------------
    # Moderation protection
    # --------------------------------------------------------
    if not user_admin and not approved:
        if get_setting(chat.id, "antiflood"):
            now = time.time()
            key = (chat.id, user.id)
            spam_tracker[key] = [
                t for t in spam_tracker[key] if now - t <= SPAM_WINDOW
            ]
            spam_tracker[key].append(now)

            if len(spam_tracker[key]) >= SPAM_LIMIT:
                try:
                    await message.delete()
                except Exception:
                    pass
                spam_tracker[key] = []
                return

        if get_setting(chat.id, "antilink") and text and LINK_RE.search(text):
            try:
                await message.delete()
                await context.bot.send_message(
                    chat.id,
                    f"🔗 {escape(user.full_name)}, links are disabled in this group.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return

        if get_setting(chat.id, "badword") and text:
            low = text.lower()
            cursor.execute(
                "SELECT word FROM custom_filters WHERE chat_id=?",
                (chat.id,),
            )
            custom = {r[0].lower() for r in cursor.fetchall()}
            blocked = DEFAULT_BAD_WORDS | custom
            if any(re.search(rf"(?<!\\w){re.escape(w)}(?!\\w)", low) for w in blocked):
                try:
                    await message.delete()
                except Exception:
                    pass
                return

    # --------------------------------------------------------
    # Aura game
    # Any normal message earns 1 point.
    # Positive text earns extra points.
    # Negative text loses points.
    # Replying to someone also earns the normal point.
    # --------------------------------------------------------
    if get_setting(chat.id, "aura") and text:
        delta = 1
        if message.reply_to_message and message.reply_to_message.from_user:
            delta += REPLY_AURA
        extra, positive_count, negative_count = aura_for_text(text)
        delta += extra

        # Admins also participate in the Aura game.
        # Moderation commands are already excluded because this handler ignores commands.

        # Cooldown: prevents repeated instant farming.
        key = (chat.id, user.id)
        now = int(time.time())
        last = max(aura_tracker[key]) if aura_tracker[key] else 0

        if now - last >= AURA_COOLDOWN:
            changed = update_aura(chat.id, user, delta)

            if changed > 1 and positive_count:
                try:
                    await message.reply_text(
                        f"✨ +{changed} Aura Points, {escape(user.first_name)}!\n💎 Total: {get_group_aura(chat.id, user.id)} | 🌍 {get_global_aura(user.id)}",
                        parse_mode="HTML",
                        disable_notification=True,
                    )
                except Exception:
                    pass
            elif changed < 0:
                try:
                    await message.reply_text(
                        f"💥 {changed} Aura Points, {escape(user.first_name)}.\n💎 Total: {get_group_aura(chat.id, user.id)} | 🌍 {get_global_aura(user.id)}",
                        parse_mode="HTML",
                        disable_notification=True,
                    )
                except Exception:
                    pass


async def userinfo_command(update, context):
    if not update.effective_chat or not update.message:
        return

    target = resolve_user_identifier(update, context.args)
    if not target:
        target = update.effective_user

    cid = update.effective_chat.id
    cursor.execute(
        "SELECT joined_at,last_seen FROM tracked_members WHERE chat_id=? AND user_id=?",
        (cid, target.id),
    )
    tracked = cursor.fetchone()

    cursor.execute(
        """SELECT full_name,username,recorded_at FROM identity_history
           WHERE chat_id=? AND user_id=? ORDER BY recorded_at DESC LIMIT 5""",
        (cid, target.id),
    )
    history = cursor.fetchall()

    status = "UNKNOWN"
    try:
        member = await update.effective_chat.get_member(target.id)
        status = member.status.upper()
    except Exception:
        pass

    old_lines = [
        f"• <b>{escape(name or 'Unknown')}</b> {display_username(username)} — <code>{fmt_time(ts)}</code>"
        for name, username, ts in history
    ]
    old_text = "\n".join(old_lines) if old_lines else "• No previous name/username recorded."

    await update.message.reply_text(
        "╭━━━〔 👤 USER INFORMATION 〕━━━╮\n"
        f"┃ 👤 Name: <b>{escape(target.full_name)}</b>\n"
        f"┃ 🆔 User ID: <code>{target.id}</code>\n"
        f"┃ 🔗 Username: {display_username(target.username)}\n"
        f"┃ 📌 Status: <b>{escape(status)}</b>\n"
        f"┃ 🕒 Joined/Tracked: <code>{fmt_time(tracked[0]) if tracked else 'Not tracked'}</code>\n"
        f"┃ 🕘 Last Seen: <code>{fmt_time(tracked[1]) if tracked else 'Not tracked'}</code>\n"
        f"┃ 💎 Group Aura: <b>{get_group_aura(cid, target.id)}</b>\n"
        f"┃ 🌍 Worldwide Aura: <b>{get_global_aura(target.id)}</b>\n"
        f"┃ 🛡️ Aura Shield: <b>{'ON' if is_aura_protected(cid, target.id) else 'OFF'}</b>\n"
        f"┃ ⏳ Shield Left: <b>{protection_remaining_text(cid, target.id)}</b>\n"
        f"┃ 📉 Daily Aura Loss: <b>{get_daily_aura_loss(cid, target.id)}/{DAILY_AURA_LOSS_LIMIT}</b>\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        "┃ 🕰️ <b>OLD NAMES / USERNAMES</b>\n"
        f"┃ {old_text.replace(chr(10), chr(10) + '┃ ')}\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✨ Aura", callback_data=f"userinfo_aura:{target.id}"),
                InlineKeyboardButton("🛡️ Shield", callback_data=f"userinfo_shield:{target.id}"),
            ],
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{target.id}"),
                InlineKeyboardButton("🔓 Free", callback_data=f"free:{target.id}"),
            ]
        ])
    )


async def history_command(update, context):
    if not await admin_required(update):
        return

    cid = update.effective_chat.id
    limit = 10
    if context.args and context.args[0].isdigit():
        limit = max(1, min(int(context.args[0]), 30))

    cursor.execute(
        """SELECT actor_id,action,target_id,target_name,created_at
           FROM admin_logs WHERE chat_id=? ORDER BY id DESC LIMIT ?""",
        (cid, limit),
    )
    rows = cursor.fetchall()

    if not rows:
        return await update.message.reply_text("📜 Command history abhi empty hai.")

    lines = ["╭━━━〔 📜 COMMAND HISTORY 〕━━━╮"]
    for actor_id, action, target_id, target_name, ts in rows:
        actor_name = "Unknown"
        actor_username = None
        try:
            member = await update.effective_chat.get_member(actor_id)
            actor_name = member.user.full_name
            actor_username = member.user.username
        except Exception:
            pass
        lines.append(
            "┣━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 🕒 <code>{fmt_time(ts)}</code>\n"
            f"┃ ⚡ <b>{escape(action.upper())}</b>\n"
            f"┃ 👮 By: <b>{escape(actor_name)}</b> {display_username(actor_username)}\n"
            f"┃ 🆔 Actor ID: <code>{actor_id}</code>\n"
            f"┃ 🎯 Target: <b>{escape(target_name or '—')}</b>\n"
            f"┃ 🆔 Target ID: <code>{target_id if target_id is not None else '—'}</code>"
        )
    lines.append("╰━━━━━━━━━━━━━━━━━━━━╯")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def aura_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    data = query.data or ""
    cid = query.message.chat.id
    actor = query.from_user

    if data.startswith("approve:") or data.startswith("free:"):
        if not await is_admin(update):
            return await query.message.reply_text("❌ Sirf group admins approve/free kar sakte hain.")
        try:
            uid = int(data.split(":", 1)[1])
        except ValueError:
            return

        cursor.execute(
            "SELECT user_id,username,full_name FROM tracked_members WHERE chat_id=? AND user_id=?",
            (cid, uid),
        )
        row = cursor.fetchone()
        if row:
            target = type("Target", (), {
                "id": row[0], "username": row[1], "full_name": row[2], "is_bot": False
            })()
        else:
            target = type("Target", (), {
                "id": uid, "username": None, "full_name": str(uid), "is_bot": False
            })()

        if data.startswith("approve:") or data.startswith("free:"):
            cursor.execute(
                "INSERT OR REPLACE INTO approved_users VALUES(?,?,?,?,?)",
                (cid, target.id, target.full_name, actor.id, int(time.time())),
            )
            cursor.execute(
                "DELETE FROM blocked_users WHERE chat_id=? AND user_id=?",
                (cid, target.id),
            )
            commit()
            log_action(cid, actor.id, "approve_free_click", target.id, target.full_name)
            return await query.message.reply_text(
                "╭━━━〔 💎 BLACK BERRY MEGA 〕━━━╮\n"
                "┃ ✅ <b>USER APPROVED & FREED</b>\n"
                f"┃ 👤 {escape(target.full_name)}\n"
                f"┃ 🆔 <code>{target.id}</code>\n"
                f"┃ 🔗 {display_username(target.username)}\n"
                f"┃ 👮 By: {escape(actor.full_name)}\n"
                f"┃ 🕒 <code>{fmt_time(time.time())}</code>\n"
                "┃ 🛡️ Protection: <b>EXEMPTED</b>\n"
                "╰━━━━━━━━━━━━━━━━━━━━╯",
                parse_mode="HTML"
            )

    if data == "aura_top":
        cursor.execute(
            """SELECT user_id,full_name,username,points FROM group_aura
               WHERE chat_id=? ORDER BY points DESC LIMIT 10""",
            (cid,),
        )
        rows = cursor.fetchall()
        if not rows:
            return await query.message.reply_text("✨ Aura leaderboard empty hai.")
        medals = ["🥇", "🥈", "🥉"]
        lines = ["✨ <b>TOP AURA — THIS GROUP</b> ✨", ""]
        for i, row in enumerate(rows, 1):
            medal = medals[i-1] if i <= 3 else f"{i}."
            lines.append(f"{medal} <b>{escape(row[1])}</b> {display_username(row[2])} — 💎 <b>{row[3]}</b>")
        return await query.message.reply_text("\n".join(lines), parse_mode="HTML")

    if data == "aura_global":
        cursor.execute("SELECT user_id,total_points FROM aura_points ORDER BY total_points DESC LIMIT 10")
        rows = cursor.fetchall()
        if not rows:
            return await query.message.reply_text("🌍 Worldwide Aura leaderboard empty hai.")
        lines = ["🌍 <b>TOP AURA — WORLDWIDE</b> 🌍", ""]
        for i, (uid, points) in enumerate(rows, 1):
            cursor.execute(
                "SELECT full_name,username FROM group_aura WHERE user_id=? ORDER BY last_earned DESC LIMIT 1",
                (uid,),
            )
            rr = cursor.fetchone()
            name = rr[0] if rr else f"User {uid}"
            username = rr[1] if rr else None
            medal = ["🥇","🥈","🥉"][i-1] if i <= 3 else f"{i}."
            lines.append(f"{medal} <b>{escape(name)}</b> {display_username(username)} — 💎 <b>{points}</b>")
        return await query.message.reply_text("\n".join(lines), parse_mode="HTML")

    if ":" not in data:
        return
    action, raw_uid = data.split(":", 1)
    try:
        uid = int(raw_uid)
    except ValueError:
        return

    cursor.execute(
        "SELECT user_id,username,full_name FROM tracked_members WHERE chat_id=? AND user_id=?",
        (cid, uid),
    )
    row = cursor.fetchone()
    target = type("Target", (), {
        "id": uid,
        "username": row[1] if row else None,
        "full_name": row[2] if row else str(uid),
        "is_bot": False,
    })()

    if action == "userinfo_aura":
        return await query.message.reply_text(
            f"✨ <b>{escape(target.full_name)}</b>\n"
            f"💎 Group: <b>{get_group_aura(cid, uid)}</b>\n"
            f"🌍 Worldwide: <b>{get_global_aura(uid)}</b>\n"
            f"🛡️ Protected: <b>{'YES' if is_aura_protected(cid, uid) else 'NO'}</b>",
            parse_mode="HTML"
        )

    if action in ("userinfo_shield", "aura_protect"):
        if uid != actor.id and not await is_admin(update):
            return await query.message.reply_text("❌ Dusre user ka shield admin hi change kar sakta hai.")
        if is_aura_protected(cid, uid):
            remove_aura_protection(cid, uid)
            log_action(cid, actor.id, "aura_unprotect", uid, target.full_name)
            return await query.message.reply_text(
                f"╭━━━〔 🛡️ AURA SHIELD 〕━━━╮\n"
                f"┃ 👤 <b>{escape(target.full_name)}</b>\n"
                "┃ 🔓 Aura Protection OFF\n"
                f"┃ 💎 Group Total: <b>{get_group_aura(cid, uid)}</b>\n"
                f"┃ 🌍 Worldwide Total: <b>{get_global_aura(uid)}</b>\n"
                f"┃ 🕒 <code>{fmt_time(time.time())}</code>\n"
                "╰━━━━━━━━━━━━━━━━━━━━╯",
                parse_mode="HTML"
            )

        ok, reason = charge_aura_protection(cid, target, actor.id)
        if not ok:
            msg = (
                f"❌ Protection ke liye {AURA_PROTECTION_COST} Aura chahiye."
                if reason == "insufficient"
                else f"🛡️ Protection already active: {protection_remaining_text(cid, uid)}"
            )
            return await query.message.reply_text(
                f"{msg}\n💎 Current Aura: <b>{get_group_aura(cid, uid)}</b>",
                parse_mode="HTML"
            )

        log_action(cid, actor.id, "aura_protect_-500", uid, target.full_name)
        return await query.message.reply_text(
            f"╭━━━〔 🛡️ AURA SHIELD 〕━━━╮\n"
            f"┃ 👤 <b>{escape(target.full_name)}</b>\n"
            "┃ 🛡️ Aura Protection ON\n"
            f"┃ 💸 Cost: <b>-{AURA_PROTECTION_COST}</b> Aura\n"
            f"┃ 💎 Group Total: <b>{get_group_aura(cid, uid)}</b>\n"
            f"┃ 🌍 Worldwide Total: <b>{get_global_aura(uid)}</b>\n"
            "┃ ⏳ Valid: <b>24 Hours</b>\n"
            f"┃ 🕒 <code>{fmt_time(time.time())}</code>\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯",
            parse_mode="HTML"
        )



# ============================================================
# BASIC / UTILITY
# ============================================================

async def start(update, context):
    await update.message.reply_text(
        "🤖 <b>Black Berry Mega Bot Active!</b>\n"
        "💎 Management + Protection + Aura Game",
        parse_mode="HTML",
    )


async def ping_command(update, context):
    await update.message.reply_text("🏓 Pong! Black Berry Mega is active.")


async def id_command(update, context):
    user = update.effective_user
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        user = update.message.reply_to_message.from_user

    username = f"@{user.username}" if user.username else "Not available"
    await update.message.reply_text(
        "╭━━━〔 👤 USER ID CARD 〕━━━╮\n"
        f"┃ 👤 Name: <b>{escape(user.full_name)}</b>\n"
        f"┃ 🆔 User ID: <code>{user.id}</code>\n"
        f"┃ 🔗 Username: {escape(username)}\n"
        f"┃ 🕒 Checked: <code>{fmt_time(time.time())}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✨ Aura", callback_data=f"userinfo_aura:{user.id}")]
        ])
    )


async def help_command(update, context):
    await update.message.reply_text(
        "╭━━━〔 💎 BLACK BERRY MEGA 〕━━━╮\n"
        "┃ <b>👮 MANAGEMENT</b>\n"
        "┃ /ban /unban /kick /mute /dmute /unmute\n"
        "┃ /warn /unwarn /warns /clear 10\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        "┃ <b>🛡️ PROTECTION</b>\n"
        "┃ /approve /free /unapprove\n"
        "┃ Reply OR @username OR User ID\n"
        "┃ /welcome on|off /antilink on|off\n"
        "┃ /antiflood on|off /badword on|off\n"
        "┃ /filteradd word /filterdel word /filters\n"
        "┃ Reply to response: /filteradd @user | /filteradd @user word\n"
        "┃ User ID filter: /filteradd 123456789 | /filteradd 123456789 word\n"
        "┃ Response can be text, sticker, photo, video, GIF, document or other media\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        "┃ <b>✨ AURA GAME</b>\n"
        "┃ /aura — profile\n"
        "┃ /aurabal — current group Aura balance\n"
        "┃ /auraworldwidebal — worldwide Aura balance\n"
        "┃ /aurabalance — group + worldwide balance\n"
        "┃ /aura give @user 10\n"
        "┃ /aura rob @user 10\n"
        "┃ /aura protect @user\n"
        "┃ /aura unprotect @user\n"
        "┃ /giveaura @user 10\n"
        "┃ /robaura @user 10\n"
        "┃ /auraprotect / /auraunprotect\n"
        "┃ /topaura /globaltopaura\n"
        "┃ /setaura @user 100\n"
        "┃ /aura on|off\n"
        "┣━━━━━━━━━━━━━━━━━━━━\n"
        "┃ <b>📜 HISTORY & USER INFO</b>\n"
        "┃ /userinfo — old names + usernames\n"
        "┃ /history 10 — command history\n"
        "┃ /id /ping /help /adminhelp\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        parse_mode="HTML",
    )


async def adminhelp_command(update, context):
    if not await admin_required(update):
        return
    await help_command(update, context)


# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN environment variable is missing.")
        print("Windows CMD: set BOT_TOKEN=YOUR_NEW_BOT_TOKEN")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("adminhelp", adminhelp_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("userinfo", userinfo_command))
    app.add_handler(CommandHandler("history", history_command))

    # Approve / Free
    app.add_handler(CommandHandler("approve", approve_command))
    app.add_handler(CommandHandler("free", free_command))
    app.add_handler(CommandHandler("unblock", free_command))
    app.add_handler(CommandHandler("unapprove", unapprove_command))

    # Moderation
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("kick", kick_command))
    app.add_handler(CommandHandler("mute", mute_command))
    app.add_handler(CommandHandler("dmute", dmute_command))
    app.add_handler(CommandHandler("unmute", unmute_command))
    app.add_handler(CommandHandler("warn", warn_command))
    app.add_handler(CommandHandler("unwarn", unwarn_command))
    app.add_handler(CommandHandler("warns", warns_command))
    app.add_handler(CommandHandler("clear", clear_command))

    # Settings
    app.add_handler(CommandHandler("welcome", welcome_command))
    app.add_handler(CommandHandler("antilink", antilink_command))
    app.add_handler(CommandHandler("antiflood", antiflood_command))
    app.add_handler(CommandHandler("badword", badword_command))
    app.add_handler(CommandHandler("filteradd", filter_add_command))
    app.add_handler(CommandHandler("filterdel", filter_remove_command))
    app.add_handler(CommandHandler("filters", filters_command))
    app.add_handler(CommandHandler("aura", aura_command))
    app.add_handler(CommandHandler("aurabalance", aura_balance_command))
    app.add_handler(CommandHandler("aurabal", aura_group_balance_command))
    app.add_handler(CommandHandler("auragroupbal", aura_group_balance_command))
    app.add_handler(CommandHandler("auraworldwidebal", aura_worldwide_balance_command))
    app.add_handler(CommandHandler("aurawbal", aura_worldwide_balance_command))
    app.add_handler(CommandHandler("topaura", topaura_command))
    app.add_handler(CommandHandler("globaltopaura", globaltopaura_command))
    app.add_handler(CommandHandler("setaura", set_aura_command))
    app.add_handler(CommandHandler("giveaura", give_aura_command))
    app.add_handler(CommandHandler("robaura", rob_aura_command))
    app.add_handler(CommandHandler("auraprotect", aura_protect_command))
    app.add_handler(CommandHandler("auraunprotect", aura_unprotect_command))

    # Welcome event
    app.add_handler(ChatMemberHandler(member_update, ChatMemberHandler.CHAT_MEMBER))

    # Clickable profile / Aura buttons
    app.add_handler(CallbackQueryHandler(aura_callback, pattern=r"^(aura_|userinfo_)"))

    # Messages
    app.add_handler(MessageHandler(~filters.COMMAND, message_handler))

    print("💎 Black Berry Mega Bot is running... | Management + Aura + History + Protection")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
