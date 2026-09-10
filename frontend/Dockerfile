# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY frontend_app.py .
COPY templates ./templates
COPY static ./static

ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "frontend_app.py"]
