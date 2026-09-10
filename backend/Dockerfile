# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Tesseract OCR + Poppler-adjacent libs needed by PyMuPDF/Pillow at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /app/uploads

ENV UPLOAD_DIR=/app/uploads \
    TESSERACT_CMD=/usr/bin/tesseract \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
