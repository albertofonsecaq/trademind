FROM python:3.12-slim

WORKDIR /app

# Key frames for Telegram videos are extracted with ffmpeg; without it every
# video is kept unanalysed and flagged for reprocessing (#95).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
