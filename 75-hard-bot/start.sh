#!/bin/bash
# Start both the bot and the agent daemon
python -m bot.main &
BOT_PID=$!

# Wait a few seconds for the bot to initialize before starting the agent
sleep 10
python -m agent.daemon &
AGENT_PID=$!

echo "Bot PID: $BOT_PID, Agent PID: $AGENT_PID"

# If either process dies, kill the other and exit (Fly.io will restart)
wait -n $BOT_PID $AGENT_PID
echo "A process exited. Shutting down."
kill $BOT_PID $AGENT_PID 2>/dev/null
wait
