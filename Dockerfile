FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY tg_media_bot/ tg_media_bot/
RUN chmod -R 644 bot.py tg_media_bot/

CMD ["python", "-u", "bot.py"]
