import os
import threading
import logging
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message

# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("bot")

# =========================
# Environment
# =========================
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

# =========================
# Tiny Flask app (keeps Render port bound)
# =========================
app = Flask(__name__)

@app.get("/")
def root():
    # health endpoint so Render sees a 200 and keeps the service up
    return "ok", 200

@app.get("/healthz")
def healthz():
    return "ok", 200

def run_flask():
    # Run Flask in a background thread so Pyrogram can own the main loop
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# =========================
# Pyrogram Bot
# =========================
bot = Client(
    name="my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# --- Debug handler: log every message that reaches us
@bot.on_message(filters.all)
async def _debug_all(client: Client, message: Message):
    try:
        who = message.from_user.id if message.from_user else "unknown"
        textish = message.text or message.caption or "<non-text>"
        log.info("[UPDATE] chat=%s | user=%s | text=%s",
                 message.chat.id, who, textish)
    except Exception as e:
        log.exception("debug handler error: %s", e)

# --- /start handler (bulletproof match)
# Force prefixes="/" so plain "/start" in private chats always matches.
@bot.on_message(filters.command(["start"], prefixes="/"))
async def start_handler(client: Client, message: Message):
    try:
        await message.reply_text(
            "👋 Yo! I’m alive and responding.\n"
            "Send a link and I’ll try to process it."
        )
    except Exception as e:
        log.exception("Error in start_handler: %s", e)

# --- Fallback reply so you always see something on plain text
@bot.on_message(filters.text & ~filters.command(["start"]))
async def echo_handler(client: Client, message: Message):
    try:
        preview = (message.text or "")[:200]
        await message.reply_text(f"Got it: {preview}")
    except Exception as e:
        log.exception("Error in echo_handler: %s", e)

# =========================
# Entrypoint
# =========================
if __name__ == "__main__":
    # 1) Start Flask in the background to keep the Render port open
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("[BOOT] Flask started on port %s. Starting Pyrogram…", PORT)

    # 2) Run the bot — this owns the asyncio loop cleanly
    bot.run()
