import os
import sqlite3
import threading
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes
)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TZ = ZoneInfo("Asia/Colombo")
DB = "events.db"

def db():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            event_time TEXT NOT NULL,
            reward TEXT,
            link TEXT,
            reminded_24 INTEGER DEFAULT 0,
            reminded_1 INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn

CONN = db()
LOCK = threading.Lock()

def add_event(chat_id, name, category, dt, reward, link):
    with LOCK:
        cur = CONN.execute(
            "INSERT INTO events(chat_id,name,category,event_time,reward,link) VALUES(?,?,?,?,?,?)",
            (chat_id, name, category, dt.isoformat(), reward, link)
        )
        CONN.commit()
        return cur.lastrowid

def get_events(chat_id):
    with LOCK:
        return CONN.execute(
            "SELECT id,name,category,event_time,reward,link FROM events "
            "WHERE chat_id=? ORDER BY event_time",
            (chat_id,)
        ).fetchall()

def delete_event(chat_id, event_id):
    with LOCK:
        cur = CONN.execute(
            "DELETE FROM events WHERE chat_id=? AND id=?",
            (chat_id, event_id)
        )
        CONN.commit()
        return cur.rowcount > 0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Even Reminder Bot!\n\n"
        "Commands:\n"
        "/events - upcoming events\n"
        "/today - today's events\n"
        "/add - add an event\n"
        "/delete ID - delete an event\n"
        "/help - show help\n\n"
        "Add format:\n"
        "/add Event name | Category | YYYY-MM-DD HH:MM | Reward | Link\n\n"
        "Example:\n"
        "/add Lista AMA | AMA | 2026-11-20 20:00 | 100 USDT | https://example.com"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_events(update.effective_chat.id)
    now = datetime.now(TZ)
    rows = [r for r in rows if datetime.fromisoformat(r[3]).astimezone(TZ) >= now]
    if not rows:
        await update.message.reply_text("📭 No upcoming events.")
        return
    out = ["📅 Upcoming events:\n"]
    for r in rows[:30]:
        dt = datetime.fromisoformat(r[3]).astimezone(TZ)
        reward = f"\n🎁 {r[4]}" if r[4] else ""
        link = f"\n🔗 {r[5]}" if r[5] else ""
        out.append(f"#{r[0]} • {r[1]}\n🏷️ {r[2]}\n🕐 {dt:%d %b %Y, %I:%M %p} 🇱🇰{reward}{link}\n")
    await update.message.reply_text("\n".join(out))

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_events(update.effective_chat.id)
    today_date = datetime.now(TZ).date()
    rows = [r for r in rows if datetime.fromisoformat(r[3]).astimezone(TZ).date() == today_date]
    if not rows:
        await update.message.reply_text("📭 No events today.")
        return
    out = ["📌 Today's events:\n"]
    for r in rows:
        dt = datetime.fromisoformat(r[3]).astimezone(TZ)
        out.append(f"#{r[0]} • {r[1]} — {r[2]} — {dt:%I:%M %p} 🇱🇰\n{r[5] or ''}")
    await update.message.reply_text("\n".join(out))

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.partition(" ")[2].strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 5:
        await update.message.reply_text(
            "Use:\n/add Event name | Category | YYYY-MM-DD HH:MM | Reward | Link"
        )
        return
    name, category, date_text, reward, link = parts[:5]
    try:
        dt = datetime.strptime(date_text, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    except ValueError:
        await update.message.reply_text("❌ Date must be: YYYY-MM-DD HH:MM")
        return
    if dt <= datetime.now(TZ):
        await update.message.reply_text("❌ Event time must be in the future.")
        return
    event_id = add_event(update.effective_chat.id, name, category, dt, reward, link)
    await update.message.reply_text(
        f"✅ Added event #{event_id}\n"
        f"🕐 {dt:%d %b %Y, %I:%M %p} 🇱🇰"
    )

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Use: /delete ID")
        return
    ok = delete_event(update.effective_chat.id, int(context.args[0]))
    await update.message.reply_text("🗑️ Deleted." if ok else "❌ Event not found.")

async def reminder_loop(app):
    while True:
        now = datetime.now(TZ)
        with LOCK:
            rows = CONN.execute(
                "SELECT id,chat_id,name,category,event_time,reward,link,reminded_24,reminded_1 FROM events"
            ).fetchall()
        for r in rows:
            event_id, chat_id, name, category, event_time, reward, link, r24, r1 = r
            dt = datetime.fromisoformat(event_time).astimezone(TZ)
            seconds = (dt - now).total_seconds()

            if 0 < seconds <= 24*3600 and not r24:
                msg = f"🔔 24-HOUR REMINDER\n\n🏆 {name}\n🏷️ {category}\n🕐 {dt:%d %b %Y, %I:%M %p} 🇱🇰"
                if reward: msg += f"\n🎁 {reward}"
                if link: msg += f"\n🔗 {link}"
                try: await app.bot.send_message(chat_id, msg)
                except Exception: pass
                with LOCK:
                    CONN.execute("UPDATE events SET reminded_24=1 WHERE id=?", (event_id,))
                    CONN.commit()

            if 0 < seconds <= 3600 and not r1:
                msg = f"⏰ 1-HOUR REMINDER\n\n🏆 {name}\n🏷️ {category}\n🕐 {dt:%I:%M %p} 🇱🇰"
                if reward: msg += f"\n🎁 {reward}"
                if link: msg += f"\n🔗 {link}"
                try: await app.bot.send_message(chat_id, msg)
                except Exception: pass
                with LOCK:
                    CONN.execute("UPDATE events SET reminded_1=1 WHERE id=?", (event_id,))
                    CONN.commit()
        await asyncio.sleep(60)

async def post_init(app):
    app.create_task(reminder_loop(app))

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("events", events))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("delete", delete))
    app.run_polling()

if __name__ == "__main__":
    main()
