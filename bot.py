import os
import asyncio
from pyrogram import Client, filters, idle
from pyrogram.types import Message

# Initialize the bot with environment variables
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN")  # optional
SESSION = os.getenv("SESSION")      # your session string

# Create bot and user clients (only if env exists)
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN) if BOT_TOKEN else None
user = Client("user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION) if SESSION else None

# Handle /start command
if bot:
    @bot.on_message(filters.command("start") & filters.private)
    async def start_command(client: Client, message: Message):
        await message.reply_text(
            "Hi! Send me a link to a restricted video in a private channel (I must be a member). "
            "I'll send it back as a downloadable file!"
        )

# Handle incoming message with a video link
if bot and user:
    @bot.on_message(filters.text & filters.private)
    async def handle_link(client: Client, message: Message):
        try:
            link = message.text
            if "/c/" not in link:
                await message.reply_text(
                    "Please send a valid private channel link "
                    "(e.g., https://t.me/c/123456789/10)."
                )
                return

            # Extract chat ID and message ID
            parts = link.split("/")
            chat_id = int("-100" + parts[-2])  # internal format
            message_id = int(parts[-1])

            # Use user client to fetch the message
            async with user:
                msg = await user.get_messages(chat_id, message_id)
                if msg.video or msg.document:
                    file_path = await user.download_media(msg, file_name="temp_video.mp4")
                    await bot.send_video(
                        chat_id=message.chat.id,
                        video=file_path,
                        caption="Here’s your downloadable video!",
                        reply_to_message_id=message.id
                    )
                    os.remove(file_path)
                else:
                    await message.reply_text("No video found in that message.")
        except Exception as e:
            await message.reply_text(f"Error: {str(e)}")
            print(f"Error: {e}")

# Start both clients
async def main():
    if bot: await bot.start()
    if user: await user.start()
    print("Bot is running...")
    await idle()   # <— this keeps everything alive
    if user: await user.stop()
    if bot: await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
