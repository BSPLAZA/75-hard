#!/bin/bash
# Loads test bot config from .env.test (gitignored).
set -a
source "$(dirname "$0")/.env.test"
set +a

cd "$(dirname "$0")"
.venv/bin/python -m bot.main
