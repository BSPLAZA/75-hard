#!/bin/bash
# Run the test bot with test env and test database
# Production Luke stays on Fly.io untouched
export TELEGRAM_BOT_TOKEN="8710932951:AAH4Evs9vcXAi8BFbUIx4vte_g5W0xAaB-8"
export ADMIN_USER_ID="6740203693"
export CHALLENGE_START_DATE="2026-04-15"
export DATABASE_PATH="data/test.db"
export GROUP_CHAT_ID="0"

cd "$(dirname "$0")"
.venv/bin/python -m bot.main
