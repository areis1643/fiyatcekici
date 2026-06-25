# Playwright resmi imaji: Chromium + tum OS bagimliliklari HAZIR gelir.
# Boylece "playwright install --with-deps" adimina hic gerek kalmaz.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
