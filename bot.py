# bot.py
import os
import threading
from flask import Flask
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import (
    ChannelPrivate, UserBannedInChannel, FloodWait,
    MessageIdInvalid, FileReferenceExpired, PeerIdInvalid, RPCError
)
import asyncio

# =========================
# ENV
# =========================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN")          # BotFather token
SESSION = os.getenv("SESSION")              # user session string

print(f"[BOOT] API_ID set? {'yes' if API_ID else 'no'} | "
      f"API_HASH len={len(API_HASH)} | "
      f"BOT_TOKEN tail={BOT_TOKEN[-6:] if BOT_TOKEN else 'None'} | "
      f"SESSION len={len(SESSION) if SESSION else 0}")

# =========================
# Clients
# =========================
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION)

# =========================
# Helpers
# =========================
def parse_private_link(link: str):
    """https://t.me/c/123456789/10 -> (-100123456789, 10)"""
    if "/c/" not in link:
        return None, None
    try:
        parts = link.strip().split("/")
        raw_chat_id = parts[-2]
        msg_id = int(parts[-1])
        chat_id = int(f"-100{raw_chat_id}")
        return chat_id, msg_id
    except Exception:
        return None, None

async def fetch_and_download(chat_id: int, message_id: int):
    """Download media with the user client. Return (path, err)."""
    try:
        # refresh peer cache, helpful right after joining channels
        await user.get_chat(chat_id)

        msg = await user.get_messages(chat_id, message_id)
        media = msg.video or msg.document or msg.animation
        if not media:
            return None, "No downloadable media in that message."

        try:
            path = await user.download_media(msg, file_name="temp_video.mp4")
        except FileReferenceExpired:
            # refetch once if file reference expired
            msg = await user.get_messages(chat_id, message_id)
            path = await user.download_media(msg, file_name="temp_video.mp4")

        return path, None

    except ChannelPrivate:
        return None, "I’m not a member of that private channel."
    except UserBannedInChannel:
        return None, "This account is banned in that channel."
    except MessageIdInvalid:
        return None, "That message ID doesn’t exist in that chat."
    except PeerIdInvalid:
        return None, "Bad link or chat ID. Use a link like https://t.me/c/123456789/10."
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await fetch_and_download(chat_id, message_id)
    except RPCError as e:
        return None, f"Telegram error: {e}"
    except Exception as e:
        return None, f"Unexpected error: {e}"

# =========================
# Handlers
# =========================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(_, m: Message):
    print(f"[HANDLER] /start from {m.from_user.id} @{m.from_user.username}")
    await m.reply_text(
        "hey 👋 bot’s awake.\n"
        "send a private channel link like:\n"
        "`https://t.me/c/123456789/10`\n"
        "(the user session must be in that channel)",
        quote=True
    )

# Catch-all: log every private message so we know updates arrive
@bot.on_message(filters.private & ~filters.command("start"))
async def _log_everything(_, m: Message):
    print(f"[UPDATE] chat={m.chat.id} user={m.from_user.id} text={repr(m.text)}")
    # light ping so you see something in chat
    if m.text:
        await m.reply_text("pong ✅  now send a /c/ link")

# Link handler (runs when the text is a /c/ link)
@bot.on_message(filters.text & filters.private)
async def handle_link(_, m: Message):
    link = m.text.strip()
    chat_id, msg_id = parse_private_link(link)
    if not chat_id:
        return  # not a /c/ link; catch-all already replied

    print(f"[HANDLER] handle_link chat_id={chat_id} msg_id={msg_id}")
    file_path = None
    try:
        file_path, err = await fetch_and_download(chat_id, msg_id)
        if err:
            await m.reply_text(err)
            return

        # send back via bot; Telegram will pick correct media type
        await bot.send_document(m.chat.id, document=file_path, caption="here’s your downloadable media.")
    except Exception as e:
        await m.reply_text(f"Error: {e}")
        print(f"[ERROR] {e}")
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

# =========================
# Tiny Flask keep-alive (for Render Web Service)
# =========================
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.getenv("PORT", "10000"))
    app_flask.run(host="0.0.0.0", port=port)

# =========================
# Runner (single event loop, no stop calls)
# =========================
async def start_all():
    await bot.start()
    me = await bot.get_me()
    print(f"[BOOT] Bot online as @{me.username} id={me.id}")
    await user.start()
    print("[BOOT] Bot & User started. Waiting for updates…")
    await idle()  # blocks here, keeps both alive

if __name__ == "__main__":
    # start tiny web server so Render sees an open port
    threading.Thread(target=run_flask, daemon=True).start()

    # single loop, zero asyncio.run(), zero stop() — bye-bye cursed error
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_all())
