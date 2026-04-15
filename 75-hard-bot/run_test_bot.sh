#!/bin/bash
export TELEGRAM_BOT_TOKEN="8710932951:AAH4Evs9vcXAi8BFbUIx4vte_g5W0xAaB-8"
export ADMIN_USER_ID="6740203693"
export CHALLENGE_START_DATE="2026-04-13"
export DATABASE_PATH="data/test.db"
export GROUP_CHAT_ID="0"
export ANTHROPIC_API_KEY="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"

cd "$(dirname "$0")"
.venv/bin/python -m bot.main
