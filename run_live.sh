#!/bin/bash
# ─────────────────────────────────────────────────
# run_live.sh — Start both live scripts in background
# Usage:
#   ./run_live.sh          → start both
#   ./run_live.sh stop     → stop both
#   ./run_live.sh status   → check if running
#   ./run_live.sh logs     → tail both logs live
# ─────────────────────────────────────────────────

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_1MIN="$DIR/.pid_1min"
PID_5MIN="$DIR/.pid_5min"
LOG_1MIN="$DIR/live_divergence_1min.log"
LOG_5MIN="$DIR/live_5min_collector.log"

start() {
    echo "═══════════════════════════════════════════════"
    echo "  🚀 Starting Live Scripts"
    echo "═══════════════════════════════════════════════"

    # ── 1-min live divergence ──────────────────
    if [ -f "$PID_1MIN" ] && kill -0 "$(cat "$PID_1MIN")" 2>/dev/null; then
        echo "  ⚠️  live_divergence_1min.py already running (PID $(cat $PID_1MIN))"
    else
        nohup python3 "$DIR/live_divergence_1min.py" >> "$LOG_1MIN" 2>&1 &
        echo $! > "$PID_1MIN"
        echo "  ✅ live_divergence_1min.py started  → PID $! | Log: $LOG_1MIN"
    fi

    # ── 5-min data collector ───────────────────
    if [ -f "$PID_5MIN" ] && kill -0 "$(cat "$PID_5MIN")" 2>/dev/null; then
        echo "  ⚠️  live_5min_collector.py already running (PID $(cat $PID_5MIN))"
    else
        nohup python3 "$DIR/live_5min_collector.py" >> "$LOG_5MIN" 2>&1 &
        echo $! > "$PID_5MIN"
        echo "  ✅ live_5min_collector.py started   → PID $! | Log: $LOG_5MIN"
    fi

    echo "───────────────────────────────────────────────"
    echo "  Use:  ./run_live.sh logs    → to watch both logs"
    echo "        ./run_live.sh stop    → to stop both"
    echo "═══════════════════════════════════════════════"
}

stop() {
    echo "═══════════════════════════════════════════════"
    echo "  🛑 Stopping Live Scripts"
    echo "═══════════════════════════════════════════════"

    for pidf in "$PID_1MIN" "$PID_5MIN"; do
        if [ -f "$pidf" ]; then
            pid=$(cat "$pidf")
            name=$(basename "$pidf" | sed 's/.pid_//')
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid"
                echo "  ✅ Stopped PID $pid ($pidf)"
            else
                echo "  ℹ️  PID $pid already stopped ($pidf)"
            fi
            rm -f "$pidf"
        else
            echo "  ℹ️  No PID file: $pidf (not running?)"
        fi
    done
    echo "═══════════════════════════════════════════════"
}

status() {
    echo "═══════════════════════════════════════════════"
    echo "  📊 Status"
    echo "═══════════════════════════════════════════════"
    for pidf in "$PID_1MIN" "$PID_5MIN"; do
        label=$(basename "$pidf")
        if [ -f "$pidf" ]; then
            pid=$(cat "$pidf")
            if kill -0 "$pid" 2>/dev/null; then
                echo "  🟢 RUNNING  $label  (PID $pid)"
            else
                echo "  🔴 DEAD     $label  (PID $pid — stale)"
            fi
        else
            echo "  ⚪ STOPPED  $label"
        fi
    done
    echo "═══════════════════════════════════════════════"
}

logs() {
    echo "═══════════════════════════════════════════════"
    echo "  📜 Tailing both logs (Ctrl+C to stop)"
    echo "═══════════════════════════════════════════════"
    # tail both logs with file label prefix
    tail -f "$LOG_1MIN" "$LOG_5MIN"
}

# ── Entry point ────────────────────────────────
case "${1:-start}" in
    start)  start  ;;
    stop)   stop   ;;
    status) status ;;
    logs)   logs   ;;
    *)
        echo "Usage: $0 {start|stop|status|logs}"
        exit 1
        ;;
esac
