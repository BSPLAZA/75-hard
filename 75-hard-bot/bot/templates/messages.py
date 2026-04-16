# Welcome / Onboarding
WELCOME_GROUP = """👋 I'm the 75 Hard bot. I'll be tracking your challenge starting tomorrow.

Before we begin, I need each of you to DM me once so I can send you private check-ins (photos, reading prompts, reminders).

Tap this link to start: t.me/{bot_username}?start=register

Waiting for: {waiting_names} ({registered}/{total})"""

WELCOME_ALL_REGISTERED = """✅ Everyone's in! 75 Hard starts tomorrow, April 15.

I'll post the first daily card at 7:00 AM ET.
Check the pinned message for rules and FAQ.

Let's do this. 💪"""

DM_REGISTRATION_ASK_NAME = "Welcome! What's your name? I'll match you to the participant list."
DM_REGISTRATION_SUCCESS = "Welcome, {name}! You're registered for 75 Hard. I'll send you check-in prompts here and track your progress in the group."
DM_REGISTRATION_NOT_FOUND = "I don't see that name on the participant list. Ask Bryan to add you."
DM_REGISTRATION_ALREADY = "You're already registered, {name}! Nothing to do here."

# Workout
WORKOUT_PICK_TYPE = "@{username} — Log your workout:"
WORKOUT_PICK_LOCATION = "@{username} — {emoji} {wtype}"
WORKOUT_LOGGED = "{emoji} {name} — {location} {wtype} · Workout {num}/2 ✅"
WORKOUT_BOTH_DONE = "{emoji} {name} — {location} {wtype} · Both workouts done ✅✅"
WORKOUT_WRONG_LOCATION = "Your other workout was {other_loc}. This one needs to be {needed_loc}."
WORKOUT_ALREADY_DONE = "You've already logged both workouts today! Use /workout_undo to fix a mistake."

# Reading
READ_CHECK_DM = "Check your DMs 📖"
READ_SAME_BOOK = 'Still reading "{book}"?'
READ_ASK_BOOK = "What book are you reading?"
READ_ASK_TAKEAWAY = "Share a takeaway, favorite quote, or what stuck with you:"
READ_ALREADY_DONE = "You already logged reading today! If you want to update your entry, type /reread."

# Photo
PHOTO_CHECK_DM = "Send your photo in DMs 📸"
PHOTO_ASK = "Send me your progress photo for Day {day}."
PHOTO_SAVED = "Day {day} photo saved! 📸"
PHOTO_UPDATED = "Day {day} photo updated! 📸"
PHOTO_NOT_REGISTERED = "I can't DM you yet! Tap t.me/{bot_username}?start=register to start a chat with me first."
PHOTO_GROUP_NOTIFY = "📸 {name} checked in ({count}/{total} today)"
PHOTO_NEED_PHOTO = "I need a photo, not a file! Send me a picture."

# Diet
DIET_ON = "Diet logged ✅"
DIET_OFF = "Diet un-logged. Re-confirm when ready."

# Water
WATER_POPUP = "💧 {cups}/16"
WATER_FULL = "You already hit your gallon! 🎉"
WATER_SET = "Water set to {cups}/16 cups."

# Failure
FAIL_CONFIRM = "Are you sure? This is final. Type CONFIRM to proceed."
FAIL_DONE = """{name} completed {days} days of 75 Hard. That's further than most people ever get. 💪

${returned} returned · ${remaining} stays in the prize pool
Prize pool: ${pool} · {active} still standing"""

# Feedback
FEEDBACK_CONFIRM = "Got it — logged your {type}. Bryan will see it. 👍"

# Card
CARD_EXPIRED = "This card has expired. Use today's card ☝️ or type /card to jump to it."

# Pinned FAQ (the full rules + FAQ document)
PINNED_FAQ = """📌 75 HARD — RULES & FAQ

━━━ THE RULES ━━━
Every day for 75 days. Miss one task, you're out.

1. 🏋️  Two 45-min workouts (one indoor, one outdoor)
2. 💧  Drink a gallon of water (16 cups)
3. 🍽️  Follow your diet (your choice — no alcohol, no cheat meals)
4. 📖  Read 10 pages of non-fiction
5. 📸  Take a progress photo

━━━ HOW TO USE THE BOT ━━━
Each morning I post a daily card with buttons:
  💧 Water +1  — tap each time you drink a cup
  🏋️ Workout   — tap to log type + indoor/outdoor
  📖 Read       — tap and I'll DM you for book + takeaway
  📸 Photo      — tap and send your photo in DMs
  🍽️ Diet       — tap to confirm you followed your diet

━━━ FAQ ━━━
Q: What if I work out after midnight?
A: Log it for the day you're in. If it's 1 AM Tuesday, it counts for Tuesday.

Q: What if I forget to log something?
A: You can backfill until 12pm PT / 3pm ET the next day. I'll remind you at 11pm ET / 8pm PT if you still have unchecked tasks.

Q: What counts as a "cup" of water?
A: 8 oz / ~250 ml. A gallon = 16 cups = 128 oz.

Q: Can I change my diet mid-challenge?
A: Yes. The rule is "follow A diet." Whatever you commit to, follow it. No alcohol and no cheat meals are non-negotiable.

Q: What if I fail?
A: DM me /fail. You'll get $1 back for each day completed. The rest stays in the prize pool. You stay in the group to cheer everyone on.

Q: I tapped water too many times. How do I fix it?
A: Type /water set [number] to correct your cup count.

Q: I logged the wrong workout. How do I fix it?
A: Type /workout_undo to clear your last workout, then re-log it.

Q: Can I see my stats?
A: DM me /stats for your personal progress breakdown.

Q: I have an idea to make this bot better.
A: Type /suggest [your idea] and Bryan will see it.

Q: Something is broken.
A: Type /bug [what happened] and Bryan will fix it.

Q: Where is today's card? I can't find it.
A: Type /card and I'll link you to it. It's also pinned.

Q: What if the bot goes down?
A: Bryan will fix it. Log your tasks when it's back up. Honor system in the meantime.

━━━ STAKES ━━━
💰 $75 buy-in · $375 total prize pool
Winners split the pot from those who didn't make it.
If everyone finishes — everyone gets their $75 back. Respect.

━━━ START DATE ━━━
April 15, 2026 → June 28, 2026"""
