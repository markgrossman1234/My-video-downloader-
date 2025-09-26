# bot.py
import os
import threading
from flask import Flask
from pyrogram import Client, filters, compose
from pyrogram.types import Message
from pyrogram.errors import (
    ChannelPrivate, UserBannedInChannel, FloodWait,
    MessageIdInvalid, FileReferenceExpired, PeerIdInvalid, RPCError
)
import asyncio

# =========================
# Environment variables
# =========================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN")          # BotFather token
SESSION = os.getenv("SESSION")              # user session string

# Safe boot print (no secrets leaked)
print(f"[BOOT] API_ID set? {'yes' if API_ID else 'no'} | "
      f"API_HASH len={len(API_HASH)} | "
      f"BOT_TOKEN tail={BOT_TOKEN[-6:] if BOT_TOKEN else 'None'} | "
      f"SESSION len={len(SESSION) if SESSION else 0}")

# =========================
# Pyrogram clients
# =========================
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION)

# =========================
# Helpers / QoL
# =========================
def parse_private_link(link: str):
    """
    Accepts links like: https://t.me/c/123456789/10
    Returns (chat_id, message_id) or (None, None) if invalid.
    """
    if "/c/" not in link:
        return None, None
    parts = link.strip().split("/")
    try:
        raw_chat_id = parts[-2]      # 123456789
        message_id = int(parts[-1])  # 10
        chat_id = int(f"-100{raw_chat_id}")  # internal peer id
        return chat_id, message_id
    except Exception:
        return None, None

async def fetch_and_download(chat_id: int, message_id: int):
    """
    Fetch a message via the user session and download media if present.
    Returns (file_path, error_text). On success, error_text is None.
    """
    try:
        # helps right after joining a channel to refresh peer cache
        await user.get_chat(chat_id)

        msg = await user.get_messages(chat_id, message_id)
        media = msg.video or msg.document or msg.animation
        if not media:
            return None, "No downloadable media in that message."

        try:
            path = await user.download_media(msg, file_name="temp_video.mp4")
        except FileReferenceExpired:
            # re-fetch and retry once if file ref expired
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
# Handlers (your logic + debug)
# =========================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(_, message: Message):
    print("[HANDLER] /start")
    await message.reply_text(
        "Hi! Send me a link to a restricted/private channel post "
        "(format: https://t.me/c/123456789/10). I must be a member."
    )

# Debug: reply to any private text that isn't /start
@bot.on_message(filters.private & ~filters.command("start"))
async def debug_ping(_, m: Message):
    print("[HANDLER] debug_ping")
    await m.reply_text("I see you. Now send a link like: https://t.me/c/123456789/10")

# Link handler (if text is a /c/ link, this kicks in)
@bot.on_message(filters.text & filters.private)
async def handle_link(_, message: Message):
    link = message.text.strip()
    chat_id, message_id = parse_private_link(link)
    if not chat_id:
        return  # debug handler already replied

    print(f"[HANDLER] handle_link chat_id={chat_id} msg_id={message_id}")
    file_path = None
    try:
        file_path, err = await fetch_and_download(chat_id, message_id)
        if err:
            await message.reply_text(err)
            return

        # Send back via the bot; Telegram will pick the right media type
        await bot.send_document(
            chat_id=message.chat.id,
            document=file_path,
            caption="Here’s your downloadable media."
        )
    except Exception as e:
        await message.reply_text(f"Error: {e}")
        print(f"[ERROR] {e}")
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

# =========================
# Tiny Flask server (keeps Render Web Service alive)
# =========================
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.getenv("PORT", "10000"))
    app_flask.run(host="0.0.0.0", port=port)

# =========================
# Runner (compose handles both clients in one loop)
# =========================
if __name__ == "__main__":
    # Start tiny web server so Render detects an open port
    threading.Thread(target=run_flask, daemon=True).start()

    print("[BOOT] Starting bot & user with compose() …")
    # compose() starts and manages multiple Clients on the SAME loop.
    compose([bot, user])  # blocks here until stopped
