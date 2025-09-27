# bot.py
# ---------------------------------------------------------------------
# Pyrogram bot + tiny Flask health server for Render.
# Runs BOTH a Bot client (for chat UI) and a User client (your session)
# so the bot can fetch from private channels you're a member of.
# - Single event loop via pyrogram.compose()
# - Flask stays in a background thread
# - Handlers ordered so commands respond immediately
# - Catch-all logger runs LAST and never blocks replies
# ---------------------------------------------------------------------

import os
import re
import threading
import logging
from typing import Optional, Tuple, Union

from flask import Flask
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message, BotCommand, InputMediaDocument, InputMediaPhoto, InputMediaVideo
)
from pyrogram.errors import RPCError

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
SESSION_STRING: str = os.getenv("SESSION_STRING", "")
PORT: int = int(os.getenv("PORT", "10000"))  # Render binds this

if not API_ID or not API_HASH or not BOT_TOKEN or not SESSION_STRING:
    log.error("[BOOT] Missing one or more envs: API_ID / API_HASH / BOT_TOKEN / SESSION_STRING")
else:
    tail = BOT_TOKEN[-6:] if len(BOT_TOKEN) >= 6 else BOT_TOKEN
    log.info(
        "[BOOT] API_ID set? %s | API_HASH len=%s | BOT_TOKEN tail=%s | SESSION len=%s",
        "yes" if API_ID else "no",
        len(API_HASH),
        tail,
        len(SESSION_STRING),
    )

# =========================
# Flask (health probe only)
# =========================
app = Flask(__name__)

@app.get("/")
def health():
    return "ok", 200

def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# =========================
# Pyrogram Clients
# =========================
# Bot client (talks to users)
bot = Client(
    name="bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=ParseMode.MARKDOWN,
    in_memory=True,
    workdir=".",
)

# User client (acts as YOU, can read your private channels)
user = Client(
    name="user",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True,
    workdir=".",
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
async def safe_reply(msg: Message, text: str) -> Optional[Message]:
    try:
        return await msg.reply_text(text, disable_web_page_preview=True)
    except Exception as e:
        log.warning("reply failed chat=%s: %s", msg.chat.id if msg.chat else "?", e)
        return None

_TME_C = re.compile(r"https?://t\.me/c/(\d{5,})/(\d+)")
_TME_U = re.compile(r"https?://t\.me/([A-Za-z0-9_]{5,})/(\d+)")

def parse_tme_link(link: str) -> Optional[Tuple[Union[int, str], int]]:
    """Return (chat, msg_id).
    - t.me/c/<internal_id>/<msg_id>  => chat = -100<internal_id>
    - t.me/<username>/<msg_id>       => chat = "<username>"
    """
    m = _TME_C.match(link)
    if m:
        cid = int(m.group(1))
        mid = int(m.group(2))
        # convert to mega-id
        chat_id = int(f"-100{cid}")
        return chat_id, mid

    m = _TME_U.match(link)
    if m:
        username = m.group(1)
        mid = int(m.group(2))
        return username, mid
    return None

async def send_media_back(src_msg: Message, media_msg: Message):
    """Download media using the *user* client and send back with the *bot*."""
    try:
        if media_msg is None:
            await safe_reply(src_msg, "⚠️ Message not found.")
            return

        caption = media_msg.caption or ""
        if media_msg.photo:
            path = await user.download_media(media_msg.photo, file_name="/tmp")
            await bot.send_photo(src_msg.chat.id, path, caption=caption)
        elif media_msg.video:
            path = await user.download_media(media_msg.video, file_name="/tmp")
            await bot.send_video(src_msg.chat.id, path, caption=caption)
        elif media_msg.document:
            path = await user.download_media(media_msg.document, file_name="/tmp")
            await bot.send_document(src_msg.chat.id, path, caption=caption)
        elif media_msg.animation:
            path = await user.download_media(media_msg.animation, file_name="/tmp")
            await bot.send_animation(src_msg.chat.id, path, caption=caption)
        elif media_msg.audio:
            path = await user.download_media(media_msg.audio, file_name="/tmp")
            await bot.send_audio(src_msg.chat.id, path, caption=caption)
        elif media_msg.voice:
            path = await user.download_media(media_msg.voice, file_name="/tmp")
            await bot.send_voice(src_msg.chat.id, path, caption=caption)
        else:
            await safe_reply(src_msg, "⚠️ That message has no downloadable media.")
    except RPCError as e:
        # Most common: not enough rights to access the source chat
        await safe_reply(
            src_msg,
            "🚫 I couldn't fetch that. If it's a private channel, your *user session* must have access. "
            "Make sure the account that created the SESSION_STRING is a member of that chat."
        )
        log.warning("fetch/send failure: %s", e)
    except Exception as e:
        await safe_reply(src_msg, f"❌ Download/send failed: `{e}`")
        log.exception("send_media_back error")

async def ensure_menu():
    """Set the left-bottom command menu."""
    try:
        await bot.set_bot_commands([
            BotCommand("start", "Show welcome"),
            BotCommand("help",  "How to use"),
            BotCommand("ping",  "Health check"),
            BotCommand("get",   "Fetch media by t.me link"),
            BotCommand("id",    "Show your ID & chat ID"),
            BotCommand("about", "About this bot"),
            BotCommand("menu",  "Re-send the command menu"),
        ])
    except Exception as e:
        log.warning("set_bot_commands failed: %s", e)

# =========================
# Handlers (ORDER MATTERS)
# =========================
# 1) /start ------------------------------------------------------------
@bot.on_message(filters.command("start") & filters.incoming, group=0)
async def start_handler(_: Client, message: Message):
    await ensure_menu()
    await safe_reply(
        message,
        "👋 **I’m alive!**\n"
        "Send me a *t.me* link to a message and I’ll try to fetch its media.\n"
        "• Private channel? The **user session** must be a member.\n"
        "• Use `/get <link>` or reply `/get` to a link message."
    )

# 2) /help -------------------------------------------------------------
@bot.on_message(filters.command("help") & filters.incoming, group=0)
async def help_handler(_: Client, message: Message):
    await safe_reply(
        message,
        "📖 **Help**\n"
        "• `/get https://t.me/c/<id>/<msg>` or `https://t.me/<username>/<msg>`\n"
        "• Or reply `/get` to a message that contains such a link.\n"
        "• Private channels require the *session account* to be a member.\n"
        "• `/ping` checks health, `/id` shows ids, `/about` shows info.\n"
    )

# 3) /ping -------------------------------------------------------------
@bot.on_message(filters.command("ping") & filters.incoming, group=0)
async def ping_handler(_: Client, message: Message):
    await safe_reply(message, "🏓 Pong!")

# 4) /id ---------------------------------------------------------------
@bot.on_message(filters.command("id") & filters.incoming, group=0)
async def id_handler(_: Client, message: Message):
    uid = message.from_user.id if message.from_user else "unknown"
    cid = message.chat.id
    await safe_reply(message, f"👤 `user_id={uid}`\n💬 `chat_id={cid}`")

# 5) /about ------------------------------------------------------------
@bot.on_message(filters.command("about") & filters.incoming, group=0)
async def about_handler(_: Client, message: Message):
    await safe_reply(message, "ℹ️ Simple fetch bot powered by Pyrogram.")

# 6) /menu (re-send commands) -----------------------------------------
@bot.on_message(filters.command("menu") & filters.incoming, group=0)
async def menu_handler(_: Client, message: Message):
    await ensure_menu()
    await safe_reply(message, "✅ Menu updated. Tap the button by the message bar to see commands.")

# 7) /get --------------------------------------------------------------
@bot.on_message(filters.command("get") & filters.incoming, group=0)
async def get_handler(_: Client, message: Message):
    # pick link: argument or replied text/caption
    parts = (message.text or "").split(maxsplit=1)
    link = None
    if len(parts) == 2:
        link = parts[1].strip()
    elif message.reply_to_message:
        link = (message.reply_to_message.text
                or message.reply_to_message.caption
                or "").strip()

    if not link:
        await safe_reply(
            message,
            "Send or reply to a valid Telegram message link like:\n"
            "`https://t.me/c/<id>/<msg_id>` or `https://t.me/<username>/<msg_id>`"
        )
        return

    got = parse_tme_link(link)
    if not got:
        await safe_reply(
            message,
            "❗️That doesn't look like a valid Telegram message link.\n"
            "Use `https://t.me/c/<id>/<msg>` or `https://t.me/<username>/<msg>`."
        )
        return

    chat_ref, msg_id = got
    try:
        target_msg = await user.get_messages(chat_ref, msg_id)
        await send_media_back(message, target_msg)
    except RPCError as e:
        await safe_reply(
            message,
            "🚫 Telegram denied access. If it's a private channel, make sure the "
            "**account that owns the SESSION_STRING** is a member of that chat."
        )
        log.warning("/get access error: %s", e)
    except Exception as e:
        await safe_reply(message, f"❌ Failed to fetch: `{e}`")
        log.exception("/get unexpected error")

# 8) Fallback echo for plain text (NOT commands) -----------------------
@bot.on_message(filters.text & ~filters.command([]) & filters.incoming, group=2)
async def echo_handler(_: Client, message: Message):
    text = (message.text or "")[:200]
    if text.strip():
        await safe_reply(message, f"Got it: `{text}`")

# 9) Catch-all logger (runs LAST; never replies) -----------------------
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

    # 2) run BOTH clients with a single loop
    #    (no asyncio.run nesting; prevents “different loop” crash)
    from pyrogram import compose
    try:
        compose([bot, user])  # blocks
    except KeyboardInterrupt:
        log.info("Shutting down on SIGINT…")
    except Exception as e:
        log.exception("Fatal error from compose(): %s", e)
