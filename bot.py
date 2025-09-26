import os
import asyncio
import threading
from flask import Flask
from pyrogram import Client, filters, idle
from pyrogram.types import Message

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
# Handlers (your original logic)
# =========================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    await message.reply_text(
        "Hi! Send me a link to a restricted video in a private channel "
        "(I must be a member). I'll send it back as a downloadable file!"
    )

@bot.on_message(filters.text & filters.private)
async def handle_link(client: Client, message: Message):
    try:
        # Extract chat ID and message ID from the link (e.g., https://t.me/c/123456789/10)
        link = message.text
        if "/c/" in link:
            # Private channel link format: https://t.me/c/CHAT_ID/MESSAGE_ID
            parts = link.split("/")
            chat_id = int("-100" + parts[-2])  # Convert to Telegram's internal format
            message_id = int(parts[-1])
        else:
            await message.reply_text(
                "Please send a valid private channel link "
                "(e.g., https://t.me/c/123456789/10)."
            )
            return

        # Use user client to fetch the message (since bot may not have access)
        async with user:
            msg = await user.get_messages(chat_id, message_id)
            if msg.video or msg.document:
                # Download the media to a temporary file
                file_path = await user.download_media(msg, file_name="temp_video.mp4")
                # Send the media back to the user
                await bot.send_video(
                    chat_id=message.chat.id,
                    video=file_path,
                    caption="Here’s your downloadable video!",
                    reply_to_message_id=message.id
                )
                # Clean up the temporary file
                if os.path.exists(file_path):
                    os.remove(file_path)
            else:
                await message.reply_text("No video found in that message. Please send a link to a video post.")
    except Exception as e:
        await message.reply_text(f"Error: {str(e)}. Make sure I'm a member of the channel and the link is valid.")
        print(f"Error: {e}")

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
    await idle()          # keep both clients alive
    await user.stop()
    await bot.stop()

if __name__ == "__main__":
    # start the tiny web server in the background so Render detects an open port
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
