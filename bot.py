# bot.py
# ---------------------------------------------------------------------
# Pyrogram bot + tiny Flask health server for Render.
# - Single event loop (no asyncio.run nesting)
# - Handlers ordered so commands respond
# - Catch-all logger runs LAST and never blocks replies
# - OPTIONAL: user session (SESSION_STRING) to fetch from private channels
# ---------------------------------------------------------------------

import os
import threading
import logging
from typing import Optional

from flask import Flask
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

# ===== [ADD] extra imports for commands/menu + links + file ops
import re
from io import BytesIO
from pathlib import Path
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

# [ADD] user session (optional, enables private-channel fetching)
SESSION_STRING: str = os.getenv("SESSION_STRING", "").strip()

if not API_ID or not API_HASH or not BOT_TOKEN:
    log.error("[BOOT] Missing one of API_ID / API_HASH / BOT_TOKEN")
else:
    tail = BOT_TOKEN[-6:] if len(BOT_TOKEN) >= 6 else BOT_TOKEN
    log.info(
        "[BOOT] API_ID set? %s | API_HASH len=%s | BOT_TOKEN tail=%s | SESSION? %s",
        "yes" if API_ID else "no",
        len(API_HASH),
        tail,
        "yes" if SESSION_STRING else "no",
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

# [ADD] Optional user client (only started if SESSION_STRING is present)
user_client: Optional[Client] = None
if SESSION_STRING:
    user_client = Client(
        name="user",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True,
        workdir=".",
        parse_mode=ParseMode.MARKDOWN,
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

# ===== [ADD] command menu (shows left of the message box)
_COMMANDS_SET = False
async def ensure_bot_commands_set(app: Client):
    global _COMMANDS_SET
    if _COMMANDS_SET:
        return
    try:
        await app.set_bot_commands([
            BotCommand("start", "Start / help"),
            BotCommand("ping",  "Health check"),
            BotCommand("get",   "Fetch media from a t.me link"),
            BotCommand("help",  "How to use the bot"),
        ])
        _COMMANDS_SET = True
        log.info("Bot commands set.")
    except Exception as e:
        log.warning("Could not set bot commands: %s", e)

# ===== [ADD] link parsing helpers
_LINK_RE = re.compile(
    r"(?:https?://)?t\.me/(?:c/(\d+)/(\d+)|([A-Za-z0-9_]+)/(\d+))",
    flags=re.IGNORECASE,
)

def _link_to_chat_and_msg(link: str):
    """
    Parse t.me link to (chat_id_or_username, msg_id).
    - t.me/c/<internal_id>/<msg_id>  => chat_id = -100<internal_id>
    - t.me/<username>/<msg_id>      => chat_id = username (str)
    """
    m = _LINK_RE.search((link or "").strip())
    if not m:
        return None, None
    if m.group(1) and m.group(2):  # /c/<id>/<msg>
        channel_id = m.group(1)
        msg_id = int(m.group(2))
        chat_id = int(f"-100{channel_id}")  # convert to real chat id
        return chat_id, msg_id
    if m.group(3) and m.group(4):  # /<username>/<msg>
        username = m.group(3)
        msg_id = int(m.group(4))
        return username, msg_id
    return None, None

# =========================
# Handlers (ORDER MATTERS)
# =========================
# Use "group" to control priority: smaller runs first.
# Keep the catch-all logger at group=99 so it never masks others.

# 1) /start ------------------------------------------------------------
@bot.on_message(filters.command("start") & filters.incoming, group=0)
async def start_handler(app: Client, message: Message):
    await ensure_bot_commands_set(app)  # [ADD] show menu immediately
    await safe_reply(
        message,
        "👋 **I’m alive!**\n"
        "Send me a link and I’ll try to process it.\n"
        "_(If you still don’t see replies, updates aren’t reaching me.)_"
    )

# 2) /ping -------------------------------------------------------------
@bot.on_message(filters.command("ping") & filters.incoming, group=0)
async def ping_handler(app: Client, message: Message):
    await ensure_bot_commands_set(app)  # [ADD] keep commands available
    await safe_reply(message, "🏓 Pong!")

# 3) Your other command/feature handlers go here -----------------------
# NOTE: put them with group=1 (or 0) BEFORE the catch-all logger.

# [ADD] /help
@bot.on_message(filters.command("help") & filters.incoming, group=1)
async def help_handler(app: Client, message: Message):
    await ensure_bot_commands_set(app)
    await safe_reply(
        message,
        "Send or reply to a valid Telegram message link like:\n"
        "```\nhttps://t.me/c/<internal_id>/<msg_id>\nhttps://t.me/<username>/<msg_id>\n```"
    )

# [ADD] /get — fetch video/doc/etc. using USER SESSION if present, else bot
@bot.on_message(filters.command("get") & filters.incoming, group=1)
async def get_handler(app: Client, message: Message):
    await ensure_bot_commands_set(app)

    # pick link: argument or replied text/caption
    candidate = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        candidate = parts[1].strip()
    if not candidate and message.reply_to_message:
        candidate = (message.reply_to_message.text
                     or message.reply_to_message.caption
                     or "").strip()

    if not candidate:
        await safe_reply(
            message,
            "Send or reply to a valid Telegram message link like:\n"
            "```\nhttps://t.me/c/<internal_id>/<msg_id>\nhttps://t.me/<username>/<msg_id>\n```"
        )
        return

    chat_key, msg_id = _link_to_chat_and_msg(candidate)
    if chat_key is None:
        await safe_reply(message, "❌ That doesn't look like a valid Telegram message link.")
        return

    # choose client: prefer user session if available
    client_to_use: Client = user_client if user_client is not None else bot

    try:
        # fetch message
        src_msg = await client_to_use.get_messages(chat_key, msg_id)
        if not src_msg:
            await safe_reply(message, "⚠️ Message not found or not visible to this account.")
            return

        # if media exists, download then send back with bot (so it appears from the bot)
        media = src_msg.video or src_msg.document or src_msg.animation or src_msg.audio or src_msg.photo
        if not media:
            await safe_reply(message, "No downloadable media found in that message.")
            return

        await safe_reply(message, "⏬ Downloading media…")

        outdir = Path("/tmp") if Path("/tmp").exists() else Path(".")
        # use a predictable file name if possible
        fname = getattr(media, "file_name", None) or "file.bin"
        out_path = outdir / fname

        # download via whichever client fetched it (user or bot)
        path = await client_to_use.download_media(src_msg, file_name=str(out_path))
        if not path:
            await safe_reply(message, "Download returned nothing. Permission or size issue?")
            return

        # now send back using the bot client (to your DM)
        caption = src_msg.caption or ""
        if src_msg.video:
            await bot.send_video(chat_id=message.chat.id, video=path, caption=caption)
        elif src_msg.animation:
            await bot.send_animation(chat_id=message.chat.id, animation=path, caption=caption)
        elif src_msg.audio:
            await bot.send_audio(chat_id=message.chat.id, audio=path, caption=caption)
        elif src_msg.photo:
            await bot.send_photo(chat_id=message.chat.id, photo=path, caption=caption)
        else:
            await bot.send_document(chat_id=message.chat.id, document=path, caption=caption)

    except Exception as e:
        log.exception("get_handler error")
        hint = (
            "• If it's a private channel, either add the **bot** to that channel, "
            "or set a valid **SESSION_STRING** for a user that is a member.\n"
        )
        await safe_reply(message, f"❌ Error fetching: `{str(e)[:300]}`\n{hint}")

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

    # 2) start user session if provided (no interactive login on Render)
    if user_client is not None:
        try:
            user_client.start()
            log.info("[BOOT] User session started.")
        except Exception as e:
            log.exception("Failed to start user session: %s", e)

    # 3) run bot (this owns the event loop cleanly)
    # No asyncio.run() anywhere else; this prevents the “different loop” crash.
    try:
        bot.run()
    except KeyboardInterrupt:
        log.info("Shutting down on SIGINT…")
    except Exception as e:
        log.exception("Fatal error from bot.run(): %s", e)
    finally:
        # stop user session cleanly if we started it
        if user_client is not None:
            try:
                user_client.stop()
                log.info("User session stopped.")
            except Exception:
                pass
