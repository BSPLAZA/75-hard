import os
from datetime import date
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "0"))
CHALLENGE_START_DATE = date.fromisoformat(os.environ.get("CHALLENGE_START_DATE", "2026-04-15"))
DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/75hard.db")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

CHALLENGE_DAYS = 75
WATER_GOAL = 16  # cups
WORKOUT_TYPES = ["run", "lift", "yoga", "bike", "swim", "other"]
WORKOUT_LOCATIONS = ["outdoor", "indoor"]

# Pre-loaded participant names for registration matching
PARTICIPANTS = ["Bryan", "Kat", "Yumna", "Gaurav", "Dev"]

# Callback data prefixes (used to route inline button presses)
CB_WATER = "water_plus"
CB_WORKOUT = "workout_start"
CB_WORKOUT_TYPE = "wtype_"
CB_WORKOUT_LOC = "wloc_"
CB_READ = "read_start"
CB_READ_SAME = "read_same"
CB_READ_NEW = "read_new"
CB_PHOTO = "photo_start"
CB_DIET = "diet_toggle"
