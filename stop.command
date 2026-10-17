#!/bin/bash
# ⏹ BUXGALTER AI serverini to'xtatish
cd "$(dirname "$0")" || exit 1
PORT="${PORT:-8000}"
stopped=0
for pid in $(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null); do
  if [[ "$(ps -o command= -p "$pid")" == *main.py* ]]; then
    kill "$pid" && stopped=1 && echo "⏹ Server to'xtatildi (PID $pid)"
  fi
done
rm -f data/server.pid
[ "$stopped" = 0 ] && echo "ℹ️  BUXGALTER AI serveri ishlamayapti"
