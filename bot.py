# bot.py
# ---------------------------------------------------------------------
# Pyrogram bot + tiny Flask health server for Render.
# - Single event loop (no asyncio.run nesting)
# - Handlers ordered so commands respond
# - Catch-all logger runs LAST and never blocks replies
# ---------------------------------------------------------------------

import os
import threading
import logging
from typing import Optional

from flask import Flask
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("bot")

# =========================
# Environment
# =========================
API_ID: int = int(os.getenv("API_ID", "0"))
API_HASH: str = os.getenv("API_HASH", "")
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
PORT: int = int(os.getenv("PORT", "10000"))  # Render binds this

if not API_ID or not API_HASH or not BOT_TOKEN:
    log.error("[BOOT] Missing one of API_ID / API_HASH / BOT_TOKEN")
else:
    tail = BOT_TOKEN[-6:] if len(BOT_TOKEN) >= 6 else BOT_TOKEN
    log.info(
        "[BOOT] API_ID set? %s | API_HASH len=%s | BOT_TOKEN tail=%s",
        "yes" if API_ID else "no",
        len(API_HASH),
        tail,
    )

# =========================
# Flask (health probe only)
# =========================
app = Flask(__name__)

@app.get("/")
def health():
    # Render pings this; keeping it simple avoids blocking the bot.
    return "ok", 200

def run_flask():
    # Keep Werkzeug strictly in a background thread (no reloader).
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# =========================
# Pyrogram Client (Bot)
# =========================
bot = Client(
    name="bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=ParseMode.MARKDOWN,
    in_memory=True,          # no session file writes on Render
    workdir=".",             # keep default cwd
)

# ---------------------------------------------------------------------
# Helper: safe reply to avoid crashes if user blocked bot, etc.
# ---------------------------------------------------------------------
async def safe_reply(msg: Message, text: str) -> Optional[Message]:
    try:
        return await msg.reply_text(text, disable_web_page_preview=True)
    except Exception as e:
        log.warning("reply failed chat=%s: %s", msg.chat.id if msg.chat else "?", e)
        return None

# =========================
# Handlers (ORDER MATTERS)
# =========================
# Use "group" to control priority: smaller runs first.
# Keep the catch-all logger at group=99 so it never masks others.

# 1) /start ------------------------------------------------------------
@bot.on_message(filters.command("start") & filters.incoming, group=0)
async def start_handler(_: Client, message: Message):
    await safe_reply(
        message,
        "👋 **I’m alive!**\n"
        "Send me a link and I’ll try to process it.\n"
        "_(If you still don’t see replies, updates aren’t reaching me.)_"
    )

# 2) /ping -------------------------------------------------------------
@bot.on_message(filters.command("ping") & filters.incoming, group=0)
async def ping_handler(_: Client, message: Message):
    await safe_reply(message, "🏓 Pong!")

# 3) Your other command/feature handlers go here -----------------------
# NOTE: put them with group=1 (or 0) BEFORE the catch-all logger.
# Example:
# @bot.on_message(filters.command("help") & filters.incoming, group=1)
# async def help_handler(_: Client, message: Message):
#     await safe_reply(message, "Help text…")

# 4) Fallback echo for plain text (NOT commands) -----------------------
@bot.on_message(filters.text & ~filters.command([]) & filters.incoming, group=2)
async def echo_handler(_: Client, message: Message):
    # Trim to avoid huge messages
    text = (message.text or "")[:200]
    if text.strip():
        await safe_reply(message, f"Got it: `{text}`")

# 5) Catch-all logger (runs LAST; never replies) -----------------------
@bot.on_message(filters.all, group=99)
async def _debug_all(_: Client, message: Message):
    try:
        uid = message.from_user.id if message.from_user else "unknown"
        txt = message.text or message.caption or "<non-text>"
        log.info("[UPDATE] chat=%s | user=%s | text=%s", message.chat.id, uid, txt)
    except Exception as e:
        log.exception("debug handler error: %s", e)

# =========================
# Main
# =========================
if __name__ == "__main__":
    # 1) start health server first (background)
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("[BOOT] Flask started on port %s. Starting Pyrogram…", PORT)

    # 2) run bot (this owns the event loop cleanly)
    # No asyncio.run() anywhere else; this prevents the “different loop” crash.
    try:
        bot.run()
    except KeyboardInterrupt:
        log.info("Shutting down on SIGINT…")
    except Exception as e:
        log.exception("Fatal error from bot.run(): %s", e)
