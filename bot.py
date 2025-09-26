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
SESSION = os.getenv("SESSION")  # your session string

# Sanity print (safe: only shows lengths/ends)
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
    if "/c/" not in link:
        return None, None
    parts = link.strip().split("/")
    try:
        raw_chat_id = parts[-2]
        message_id = int(parts[-1])
        chat_id = int(f"-100{raw_chat_id}")
        return chat_id, message_id
    except Exception:
        return None, None

async def fetch_and_download(chat_id: int, message_id: int):
    try:
        await user.get_chat(chat_id)  # refresh peer cache
        msg = await user.get_messages(chat_id, message_id)
        media = msg.video or msg.document or msg.animation
        if not media:
            return None, "No downloadable media in that message."

        try:
            path = await user.download_media(msg, file_name="temp_video.mp4")
        except FileReferenceExpired:
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

# =========================
# Handlers (your original logic + debug)
# =========================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(_, message: Message):
    print("[HANDLER] /start")
    await message.reply_text(
        "Hi! Send me a link to a restricted/private channel post "
        "(format: https://t.me/c/123456789/10). "
        "I must be a member of that channel."
    )

# Debug: reply to any private text that isn't /start
@bot.on_message(filters.private & ~filters.command("start"))
async def debug_ping(_, m: Message):
    print("[HANDLER] debug_ping")
    await m.reply_text("I see you. Now send a link like: https://t.me/c/123456789/10")

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
# Runner
# =========================
async def main():
    # Start both clients
    await bot.start()

    # Delete any existing webhook so long-poll works
    try:
        await bot.delete_webhook(True)
        print("[BOOT] Deleted webhook (if any). Long-polling enabled.")
    except Exception as e:
        print(f"[BOOT] delete_webhook error (ignored): {e}")

    await user.start()
    print("[BOOT] Bot started, User session started. Waiting for updates...")
    await idle()
    await user.stop()
    await bot.stop()

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
