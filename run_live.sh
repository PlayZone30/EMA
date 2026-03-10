#!/bin/bash
# ─────────────────────────────────────────────────
# run_live.sh — Run live_runner.py in the EMA conda env
#
# Usage:
#   ./run_live.sh          → start
#   ./run_live.sh stop     → stop
#   ./run_live.sh status   → check if running
#   ./run_live.sh logs     → tail logs live
#   ./run_live.sh restart  → stop + start
# ─────────────────────────────────────────────────

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/.pid_runner"
LOG_FILE="$DIR/live_runner.log"
CONDA_ENV="EMA"

# Resolve conda's python from the EMA environment
PYTHON="$(conda run -n "$CONDA_ENV" which python 2>/dev/null)"
if [ -z "$PYTHON" ]; then
    echo "❌ Could not find python in conda env '$CONDA_ENV'. Is conda initialised?"
    echo "   Try: conda init zsh"
    exit 1
fi

start() {
    echo "═══════════════════════════════════════════════════"
    echo "  🚀 Starting Live Runner  [conda env: $CONDA_ENV]"
    echo "═══════════════════════════════════════════════════"

    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "  ⚠️  Already running (PID $(cat $PID_FILE))"
        return
    fi

    cd "$DIR"
    nohup "$PYTHON" "$DIR/live_runner.py" >> "$LOG_FILE" 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"
    echo "  ✅ live_runner.py started → PID $PID"
    echo "     Log  : $LOG_FILE"
    echo "─────────────────────────────────────────────────"
    echo "  ./run_live.sh logs    → watch logs"
    echo "  ./run_live.sh stop    → stop"
    echo "═══════════════════════════════════════════════════"
}

stop() {
    echo "═══════════════════════════════════════════════════"
    echo "  🛑 Stopping Live Runner"
    echo "═══════════════════════════════════════════════════"

    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            echo "  ✅ Stopped PID $PID"
        else
            echo "  ℹ️  PID $PID already stopped (stale)"
        fi
        rm -f "$PID_FILE"
    else
        echo "  ℹ️  Not running (no PID file)"
    fi
    echo "═══════════════════════════════════════════════════"
}

status() {
    echo "═══════════════════════════════════════════════════"
    echo "  📊 Status"
    echo "═══════════════════════════════════════════════════"
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "  🟢 RUNNING  live_runner.py  (PID $PID)"
            echo "     Uptime : $(ps -p "$PID" -o etime= 2>/dev/null | xargs)"
            echo "     Log    : $LOG_FILE"
        else
            echo "  🔴 DEAD     live_runner.py  (PID $PID — stale)"
            rm -f "$PID_FILE"
        fi
    else
        echo "  ⚪ STOPPED  live_runner.py"
    fi
    echo "═══════════════════════════════════════════════════"
}

logs() {
    echo "═══════════════════════════════════════════════════"
    echo "  📜 Tailing: $LOG_FILE  (Ctrl+C to stop)"
    echo "═══════════════════════════════════════════════════"
    tail -f "$LOG_FILE"
}

restart() {
    stop
    sleep 2
    start
}

# ── Entry point ────────────────────────────────
case "${1:-start}" in
    start)   start   ;;
    stop)    stop    ;;
    status)  status  ;;
    logs)    logs    ;;
    restart) restart ;;
    *)
        echo "Usage: $0 {start|stop|status|logs|restart}"
        exit 1
        ;;
esac
