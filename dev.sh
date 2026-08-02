#!/usr/bin/env bash
# Quick local dev startup (no Docker)
# Requires: Postgres running locally, Python venv, Node

set -e

cd "$(dirname "$0")"

# Backend
cd backend
[ ! -d .venv ] && python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ ! -f .env ] && cp .env.example .env && echo "Created backend/.env — edit DATABASE_URL and SECRET_KEY"
DATABASE_URL_SYNC="${DATABASE_URL_SYNC:-postgresql+psycopg2://trademind:trademind@localhost:5432/trademind}"
export DATABASE_URL_SYNC
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
cd ..

# Frontend
cd frontend
npm install -q
npm run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "TradeMind running:"
echo "  API:      http://localhost:8000"
echo "  Docs:     http://localhost:8000/docs"
echo "  Frontend: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
