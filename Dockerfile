# Use official Python runtime as base image
FROM python:3.12-slim

# Install system dependencies (FFmpeg) as root
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot code
COPY bot.py .

# Expose port if needed (not required for Telegram bots, but good practice)
EXPOSE 8080

# Run the bot
CMD ["python3", "bot.py"]
