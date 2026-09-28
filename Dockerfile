# Python slim-образ: мало весит, достаточно для aiogram + aiosqlite
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Зависимости ставятся первыми для кэширования слоя
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Папки под БД и фото (перекрываются volumes из compose)
RUN mkdir -p /app/data/photos

CMD ["python", "bot.py"]
