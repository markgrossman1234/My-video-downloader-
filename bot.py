# bot.py
import os
import threading
import logging
import traceback
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("bot")

# ---------------- Env ----------------
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "10000"))

if not API_ID or not API_HASH or not BOT_TOKEN:
    log.error("[BOOT] Missing one of API_ID / API_HASH / BOT_TOKEN envs")
else:
    log.info(
        "[BOOT] API_ID set? %s | API_HASH len=%s | BOT_TOKEN tail=%s",
        "yes" if API_ID else "no",
        len(API_HASH),
        BOT_TOKEN[-6:] if BOT_TOKEN else "none"
    )

# ---------------- Flask (keeps Render port bound) ----------------
app = Flask(__name__)

@app.get("/")
def health():
    return "ok", 200

def run_flask():
    # Run in a separate thread so Pyrogram can own the main loop.
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)

# ---------------- Pyrogram Bot ----------------
bot = Client(
    name="my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,          # reduce FS writes on Render
    workdir=".",             # avoid permission surprises
)

# ---------- Utility: safe reply with logging ----------
async def safe_reply(msg: Message, text: str):
    try:
        log.info("[SEND] -> chat=%s | text preview=%r", msg.chat.id, text[:80])
        await msg.reply_text(text)
        log.info("[SEND] done")
    except Exception as e:
        log.error("[SEND] failed: %s", e)
        log.error(traceback.format_exc())

# ---------- On startup ----------
@bot.on_started
async def _on_started(client: Client):
    try:
        me = await client.get_me()
        log.info("[BOOT] get_me: id=%s | username=@%s | name=%s", me.id, me.username, me.first_name)
        # Set commands so Telegram UI shows them
        await client.set_bot_commands([
            BotCommand("start", "Show hello message"),
            BotCommand("ping", "Health test"),
        ])
        log.info("[BOOT] Bot commands set")
    except Exception as e:
        log.error("[BOOT] on_started error: %s", e)
        log.error(traceback.format_exc())

# ---------- DEBUG: log every single update ----------
@bot.on_message(filters.all, group=-1000)
async def _debug_all(client: Client, message: Message):
    try:
        who = message.from_user.id if message.from_user else "unknown"
        txt = message.text or message.caption or "<non-text>"
        log.info("[UPDATE] chat=%s | user=%s | text=%s", message.chat.id, who, txt)
    except Exception as e:
        log.error("debug handler error: %s", e)
        log.error(traceback.format_exc())

# ---------- /start ----------
@bot.on_message(filters.private & filters.command("start"))
async def start_handler(client: Client, message: Message):
    try:
        log.info("[HANDLER:/start] replying…")
        await safe_reply(
            message,
            "👋 Hey! I’m alive on Render.\n"
            "Send me a private-channel post link like `https://t.me/c/123456789/10` "
            "and I’ll try to fetch the media (the *user session* must be in the channel)."
        )
        log.info("[HANDLER:/start] done")
    except Exception as e:
        log.error("[HANDLER:/start] error: %s", e)
        log.error(traceback.format_exc())

# ---------- /ping ----------
@bot.on_message(filters.private & filters.command("ping"))
async def ping_handler(client: Client, message: Message):
    try:
        log.info("[HANDLER:/ping] replying…")
        await safe_reply(message, "pong ✅")
        log.info("[HANDLER:/ping] done")
    except Exception as e:
        log.error("[HANDLER:/ping] error: %s", e)
        log.error(traceback.format_exc())

# ---------- Fallback: echo plain text ----------
@bot.on_message(filters.private & filters.text & ~filters.command(["start", "ping"]))
async def echo_handler(client: Client, message: Message):
    try:
        log.info("[HANDLER:echo] replying…")
        await safe_reply(message, f"Got it: {message.text[:200]}")
        log.info("[HANDLER:echo] done")
    except Exception as e:
        log.error("[HANDLER:echo] error: %s", e)
        log.error(traceback.format_exc())

# ============================================================
# If you still need the user session and private fetch logic,
# add your existing code BELOW this line inside new handlers.
# The point right now is to prove replies work first.
# ============================================================

if __name__ == "__main__":
    # Start Flask first (background) so Render sees the port.
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("[BOOT] Flask started on port %s. Starting Pyrogram…", PORT)
    # Give ownership of the main loop to Pyrogram.
    bot.run()
