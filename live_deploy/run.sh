#!/bin/bash
# Live Trading Engine + Dashboard
# ================================
# Run both processes on the server.
# Usage: bash run.sh
#
# Access dashboard at http://<server-ip>:8765/

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🚀 Starting Live 5-Min Collector..."
python live_5min_collector.py &
COLLECTOR_PID=$!

echo "📊 Starting Dashboard Server on port 8765..."
uvicorn dashboard.server:app --host 0.0.0.0 --port 8765 &
DASHBOARD_PID=$!

echo ""
echo "✅ Both processes started!"
echo "   Collector PID: $COLLECTOR_PID"
echo "   Dashboard PID: $DASHBOARD_PID"
echo ""
echo "   Dashboard: http://0.0.0.0:8765/"
echo ""
echo "Press Ctrl+C to stop both."

# Trap SIGINT/SIGTERM to kill both
trap "echo 'Stopping...'; kill $COLLECTOR_PID $DASHBOARD_PID 2>/dev/null; exit 0" SIGINT SIGTERM

wait
