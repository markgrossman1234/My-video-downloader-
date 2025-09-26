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

# ===== [ADD 1/7] imports for commands/menu + link parsing + in-memory file
import re
from io import BytesIO
from pyrogram.types import BotCommand

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

# ===== [ADD 2/7] user session for private fetches
SESSION: str = os.getenv("SESSION", "")

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

if SESSION:
    log.info("[BOOT] USER session present (len=%s).", len(SESSION))
else:
    log.warning("[BOOT] No USER SESSION provided. Private downloads won't work.")

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

# ===== [ADD 3/7] User Client (lazy-started when needed)
user: Optional[Client] = None
if SESSION:
    user = Client(
        name="my_user",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION,
        workdir=".",
        in_memory=True,
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

# ===== [ADD 4/7] publish the Telegram command menu once
_commands_set = False
async def ensure_commands():
    """Publish the / menu (shows left of the message box)."""
    global _commands_set
    if _commands_set:
        return
    cmds = [
        BotCommand("start", "Show welcome & help"),
        BotCommand("ping",  "Health check"),
        BotCommand("get",   "Reply with a t.me message link to fetch media"),
        BotCommand("help",  "How to use the bot"),
    ]
    await bot.set_bot_commands(cmds)
    _commands_set = True
    log.info("[BOOT] Bot commands published.")

# ===== [ADD 5/7] helpers for private message fetching
_link_c = re.compile(r"https?://t\.me/c/(\d+)/(\d+)")
_link_u = re.compile(r"https?://t\.me/([A-Za-z0-9_]+)/(\d+)")

def _parse_tme_link(text: str):
    if not text:
        return None
    m = _link_c.search(text)
    if m:
        internal_id, msg_id = int(m.group(1)), int(m.group(2))
        chat_id = int("-100" + str(internal_id))  # convert t.me/c id to -100…
        return chat_id, int(msg_id)
    m = _link_u.search(text)
    if m:
        username, msg_id = m.group(1), int(m.group(2))
        return username, int(msg_id)
    return None

async def _ensure_user_started():
    if not user:
        raise RuntimeError("USER session missing: set SESSION env to enable private downloads.")
    if not user.is_connected:
        await user.start()

# =========================
# Handlers (ORDER MATTERS)
# =========================
# Use "group" to control priority: smaller runs first.
# Keep the catch-all logger at group=99 so it never masks others.

# 1) /start ------------------------------------------------------------
@bot.on_message(filters.command("start") & filters.incoming, group=0)
async def start_handler(_: Client, message: Message):
    await ensure_commands()  # [ADD 6/7] nudge menu to appear immediately
    await safe_reply(
        message,
        "👋 **I’m alive!**\n"
        "Send me a link and I’ll try to process it.\n"
        "_(If you still don’t see replies, updates aren’t reaching me.)_"
    )

# 2) /ping -------------------------------------------------------------
@bot.on_message(filters.command("ping") & filters.incoming, group=0)
async def ping_handler(_: Client, message: Message):
    await ensure_commands()  # keep commands available
    await safe_reply(message, "🏓 Pong!")

# 3) Your other command/feature handlers go here -----------------------
# NOTE: put them with group=1 (or 0) BEFORE the catch-all logger.

# [ADD 7/7-A] /help
@bot.on_message(filters.command("help") & filters.incoming, group=1)
async def help_handler(_: Client, message: Message):
    await ensure_commands()
    await safe_reply(
        message,
        "Send a Telegram message link from a chat you can read, e.g.:\n"
        "• `https://t.me/c/<internal_id>/<msg_id>` (private/supergroup)\n"
        "• `https://t.me/<username>/<msg_id>` (public)\n\n"
        "Use **/get** while replying to a message that contains such a link\n"
        "or just paste the link directly."
    )

# [ADD 7/7-B] /get — fetch video/doc/animation/audio from a message link
@bot.on_message(filters.command("get") & filters.incoming, group=1)
async def get_handler(_: Client, message: Message):
    await ensure_commands()

    # Find a link either in this message or the replied one
    text = message.text or ""
    if message.reply_to_message:
        text = (
            message.reply_to_message.text
            or message.reply_to_message.caption
            or text
            or ""
        )

    parsed = _parse_tme_link(text)
    if not parsed:
        await safe_reply(
            message,
            "Send or reply to a valid Telegram message link like:\n"
            "`https://t.me/c/<id>/<msg_id>` or `https://t.me/<username>/<msg_id>`"
        )
        return

    chat_ref, msg_id = parsed

    try:
        await _ensure_user_started()

        # fetch target message with USER account
        target = await user.get_messages(chat_ref, msg_id)  # type: ignore[arg-type]
        if not target:
            await safe_reply(message, "Couldn't fetch that message (not visible?).")
            return

        media = target.video or target.document or target.animation or target.audio
        if not media:
            await safe_reply(message, "No downloadable media found in that message.")
            return

        bio = BytesIO()
        file_name = getattr(media, "file_name", None) or "file.bin"
        await user.download_media(target, file_name=bio)  # type: ignore[arg-type]
        bio.name = file_name
        bio.seek(0)

        if target.video:
            await bot.send_video(chat_id=message.chat.id, video=bio, caption=target.caption or "")
        elif target.animation:
            await bot.send_animation(chat_id=message.chat.id, animation=bio, caption=target.caption or "")
        elif target.audio:
            await bot.send_audio(chat_id=message.chat.id, audio=bio, caption=target.caption or "")
        else:
            await bot.send_document(chat_id=message.chat.id, document=bio, caption=target.caption or "")

    except Exception as e:
        log.exception("get_handler error: %s", e)
        await safe_reply(
            message,
            "Error while fetching:\n"
            f"`{e}`\n\n"
            "• Is `SESSION` set?\n"
            "• Can your USER account open that chat/message?\n"
            "• For invite-only chats, open them once with the USER account first."
        )

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
