#!/bin/bash
# 🏦 BUXGALTER AI — ikki marta bosib ishga tushirish (macOS)
# Server fonda ishlaydi: bu oynani yopsangiz ham to'xtamaydi. To'xtatish: stop.command

cd "$(dirname "$0")" || exit 1
PORT="${PORT:-8000}"
URL="http://127.0.0.1:$PORT"
mkdir -p data
LOG="data/server.log"

pause_exit() { echo; read -r -p "Yopish uchun Enter bosing…"; exit "${1:-1}"; }

echo "🏦 BUXGALTER AI — ishga tushirilmoqda"
echo "📂 $(pwd)"

# 1) Virtual muhit (birinchi marta yoki buzilgan bo'lsa — qayta o'rnatiladi)
if ! .venv/bin/python -c "import fastapi, uvicorn, aiogram, openpyxl, multipart" >/dev/null 2>&1; then
  PY=""
  for c in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 /usr/local/bin/python3.12 \
           /usr/local/bin/python3.13 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && \
       "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "❌ Python 3.10+ topilmadi. O'rnating: brew install python@3.12  (yoki python.org)"
    pause_exit 1
  fi
  echo "📦 Kutubxonalar o'rnatilmoqda (birinchi marta 1–2 daqiqa)…"
  rm -rf .venv
  "$PY" -m venv .venv && .venv/bin/python -m pip install -q --upgrade pip \
    && .venv/bin/python -m pip install -q -r requirements.txt || { echo "❌ O'rnatishda xato"; pause_exit 1; }
fi

# 2) Port band bo'lsa: eski BUXGALTER AI serverini to'xtatamiz, boshqa dastur bo'lsa — tegmaymiz
for pid in $(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null); do
  cmd="$(ps -o command= -p "$pid")"
  if [[ "$cmd" == *main.py* ]]; then
    echo "🔄 Eski server to'xtatilmoqda (PID $pid)"
    kill "$pid" 2>/dev/null
  else
    echo "❌ $PORT-port boshqa dastur tomonidan band: $cmd"
    pause_exit 1
  fi
done
for _ in $(seq 1 20); do lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break; sleep 0.25; done

# 3) Fonda ishga tushirish
echo "--- $(date '+%Y-%m-%d %H:%M:%S') ishga tushirildi ---" >> "$LOG"
PORT="$PORT" nohup .venv/bin/python main.py >> "$LOG" 2>&1 < /dev/null &
echo $! > data/server.pid
disown 2>/dev/null

# 4) Tayyor bo'lishini kutish
for _ in $(seq 1 40); do
  curl -s -o /dev/null -m 1 "$URL/api/status" && break
  sleep 0.5
done
if curl -s -o /dev/null -m 2 "$URL/"; then
  echo "✅ Server ishlayapti: $URL"
  [ -z "$NO_OPEN" ] && open "$URL"
  echo "ℹ️  Bu oynani yopishingiz mumkin — server fonda ishlashda davom etadi."
  echo "⏹  To'xtatish: stop.command   ·   Log: $LOG"
else
  echo "❌ Server ishga tushmadi. Oxirgi log:"
  tail -n 25 "$LOG"
  pause_exit 1
fi
