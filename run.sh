#!/usr/bin/env bash
# تشغيل «لقيت» على ماك أو لينكس: ./run.sh
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "أول مرة: عم جهّز البيئة…"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
set -a; . ./.env; set +a
if [ ! -f data/laqeet.db ]; then
  echo "ما في قاعدة بيانات. عم ابنيها من المنشورات المحفوظة (دقيقة أو دقيقتين)…"
  .venv/bin/python -m app.collect --offline
fi
echo "الجامع عم يشتغل بالخلفية (كل ${UPDATE_MINUTES:-30} دقيقة)…"
.venv/bin/python -m app.run > collect.log 2>&1 &
trap "kill $! 2>/dev/null" EXIT
echo "افتح المتصفح على: http://localhost:8000"
.venv/bin/uvicorn app.api:app --port 8000
