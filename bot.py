import os
import threading
import logging
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message

# ===== Logging =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("bot")

# ===== Env =====
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

# ===== Flask (keeps Render port bound) =====
app = Flask(__name__)

@app.get("/")
def health():
    return "ok", 200

def run_flask():
    # Run in a separate thread so Pyrogram can own the main loop.
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ===== Pyrogram Bot =====
bot = Client(
    name="my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# --- Debug: log every update that reaches us
@bot.on_message(filters.all)
async def _debug_all(client: Client, message: Message):
    try:
        who = f"{message.from_user.id if message.from_user else 'unknown'}"
        txt = message.text or message.caption or "<non-text>"
        log.info("[UPDATE] chat=%s | user=%s | text=%s",
                 message.chat.id, who, txt)
    except Exception as e:
        log.exception("debug handler error: %s", e)

# --- /start
@bot.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        "👋 Yo! I’m alive. Send me a link and I’ll try to process it.\n"
        "(If you still see no replies, it means updates aren’t reaching me.)"
    )

# --- Fallback echo so we always respond to plain text
@bot.on_message(filters.text & ~filters.command(["start"]))
async def echo_handler(client: Client, message: Message):
    await message.reply_text(f"Got it: {message.text[:200]}")

if __name__ == "__main__":
    # start Flask first, in background
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("[BOOT] Flask started on port %s. Starting Pyrogram…", PORT)
    # run bot (owns the main event loop cleanly)
    bot.run()
