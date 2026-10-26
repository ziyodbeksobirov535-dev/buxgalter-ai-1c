FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Tizim paketlari
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Bog'liqliklar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Loyiha kodlari
COPY . .

# Ma'lumotlar papkasi
RUN mkdir -p /app/data /app/static

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://127.0.0.1:8000/api/auth/state || exit 1

CMD ["python", "main.py"]
