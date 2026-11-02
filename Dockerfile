FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Bog'liqliklar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Loyiha kodlari
COPY . .

# Ma'lumotlar papkasi
RUN mkdir -p /app/data /app/static

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/auth/state', timeout=4)" || exit 1

CMD ["python", "main.py"]
