FROM python:3.11-slim

WORKDIR /app

# Ограничение буферизации для логов в контейнере
ENV PYTHONUNBUFFERED=1

# Копирование и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Команда запуска
CMD ["python", "bot.py"]
