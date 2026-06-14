#!/bin/bash
# Live Trading Engine + Dashboard
# ================================
# Activates the `fyers` conda env, then runs both processes:
#   1. live_5min_collector.py  (Fyers WS strategy engine — DB writer)
#   2. dashboard.server:app    (FastAPI dashboard — read-only DB reader)
#
# Designed to be launched directly (bash run.sh) OR by systemd on EC2 boot.
# Stays in the foreground (waits on both children) so systemd can manage it.
#
# Access dashboard at http://<server-ip>:8765/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Activate the `fyers` conda environment.
# Tries `conda info --base`, then common install locations. Required because
# `python` / `uvicorn` only resolve once the env is active (and systemd boots
# with a minimal PATH that does not include conda).
# ---------------------------------------------------------------------------
CONDA_ENV="${CONDA_ENV:-fyers}"
CONDA_BASE="$(conda info --base 2>/dev/null || true)"
if [ -z "$CONDA_BASE" ]; then
    for d in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" /opt/conda /opt/miniconda3; do
        if [ -d "$d" ]; then CONDA_BASE="$d"; break; fi
    done
fi

if [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
    # shellcheck disable=SC1091
    source "$CONDA_BASE/etc/profile.d/conda.sh"
    conda activate "$CONDA_ENV"
    echo "🐍 Activated conda env: $CONDA_ENV ($(python --version 2>&1))"
else
    echo "⚠️  Could not locate conda; relying on current PATH for python/uvicorn."
fi

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
echo "   Dashboard: http://0.0.0.0:8765/"
echo ""
echo "Press Ctrl+C (or systemctl stop) to stop both."

# Forward SIGINT/SIGTERM (sent by systemd on instance stop) to both children.
trap "echo 'Stopping...'; kill $COLLECTOR_PID $DASHBOARD_PID 2>/dev/null || true; exit 0" SIGINT SIGTERM

# Wait on both. If either exits, keep the other alive until shutdown so the
# dashboard stays reachable after the collector finishes its EOD at 15:30.
wait
