import os
import asyncio
import threading
from flask import Flask
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import (
    ChannelPrivate, UserBannedInChannel, FloodWait,
    MessageIdInvalid, FileReferenceExpired, PeerIdInvalid, RPCError
)

# =========================
# Environment variables
# =========================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION = os.getenv("SESSION")  # using your env name exactly

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
    Tries to fetch a message and download its media.
    Returns (file_path, error_text). On success error_text is None.
    """
    try:
        # Refresh peer cache (useful right after you join a channel)
        await user.get_chat(chat_id)

        msg = await user.get_messages(chat_id, message_id)
        media = msg.video or msg.document or msg.animation
        if not media:
            return None, "No downloadable media in that message."

        try:
            path = await user.download_media(msg, file_name="temp_video.mp4")
        except FileReferenceExpired:
            # Re-fetch and retry once if file ref is stale
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
        # Respect floodwait automatically
        await asyncio.sleep(e.value)
        return await fetch_and_download(chat_id, message_id)
    except RPCError as e:
        return None, f"Telegram error: {e}"

# =========================
# Handlers (your original logic + debug)
# =========================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    await message.reply_text(
        "Hi! Send me a link to a restricted/private channel post "
        "(format: https://t.me/c/123456789/10). "
        "I must be a member of that channel."
    )

# Debug handler: replies to any private text that isn't /start
@bot.on_message(filters.private & ~filters.command("start"))
async def debug_ping(_, m: Message):
    await m.reply_text("I see you. Now send a link like: https://t.me/c/123456789/10")

@bot.on_message(filters.text & filters.private)
async def handle_link(client: Client, message: Message):
    # If someone sends text that is actually a link, handle it here
    link = message.text.strip()
    chat_id, message_id = parse_private_link(link)
    if not chat_id:
        return  # debug_ping already answered

    try:
        file_path, err = await fetch_and_download(chat_id, message_id)
        if err:
            await message.reply_text(err)
            return

        # Send the media back to the user (video/doc auto-detected by Telegram)
        await bot.send_document(
            chat_id=message.chat.id,
            document=file_path,
            caption="Here’s your downloadable media."
        )
    except Exception as e:
        await message.reply_text(f"Error: {e}")
        print(f"Error: {e}")
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
# Runner
# =========================
async def main():
    await bot.start()
    await user.start()
    print("Bot is running...")
    await idle()  # keep both clients alive
    await user.stop()
    await bot.stop()

if __name__ == "__main__":
    # Start the tiny web server so Render detects an open port
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
