import os
from datetime import date
from dotenv import load_dotenv

load_dotenv()


def _csv(val: str) -> list[str]:
    return [n.strip() for n in val.split(",") if n.strip()]


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
BUY_IN = int(os.environ.get("BUY_IN", "75"))

# All participant info comes from env vars so the public repo doesn't expose
# real names, payment handles, etc.
PARTICIPANTS = _csv(os.environ.get("PARTICIPANTS", ""))

# Display name of the organizer — used in user-facing copy
# ("ask {ORGANIZER} to add you", "Flag to {ORGANIZER}", etc).
ORGANIZER = os.environ.get("ORGANIZER", "the organizer")

# Names of users who already settled their buy-in via other channels.
ALREADY_PAID = _csv(os.environ.get("ALREADY_PAID", ""))

# Venmo username for the buy-in deeplink. Empty string disables the deeplink.
VENMO_USERNAME = os.environ.get("VENMO_USERNAME", "")

# Prize-pool payment endpoints used by the self-fail flow. Both kept out of
# the public repo. Empty string = the option isn't surfaced. If both are
# empty, the self-fail flow falls back to "admin will reach out manually".
PRIZE_POOL_VENMO_USERNAME = os.environ.get("PRIZE_POOL_VENMO_USERNAME", "")
PRIZE_POOL_ZELLE_PHONE = os.environ.get("PRIZE_POOL_ZELLE_PHONE", "")

# DM same-day reminder fires at 10pm in each user's local TZ.
# Only users in these env CSVs get the nudge; missing users get nothing.
USER_TIMEZONES: dict[str, str] = {}
for _name in _csv(os.environ.get("USER_TIMEZONES_ET", "")):
    USER_TIMEZONES[_name] = "US/Eastern"
for _name in _csv(os.environ.get("USER_TIMEZONES_PT", "")):
    USER_TIMEZONES[_name] = "US/Pacific"

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
CB_WORKOUT_OUTDOOR = "workout_outdoor"
CB_WORKOUT_INDOOR = "workout_indoor"
