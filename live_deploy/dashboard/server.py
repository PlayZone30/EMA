"""
Dashboard Server — FastAPI + Uvicorn
======================================
Read-only SQLite connection to live_dashboard.db.
Serves static files at / and API endpoints for the frontend.

Run: uvicorn dashboard.server:app --host 0.0.0.0 --port 8765
"""

import os
import json
import time
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger('DashboardServer')

# Resolve paths
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'live_dashboard.db'
STATIC_DIR = Path(__file__).resolve().parent / 'static'

app = FastAPI(title="Live Trading Dashboard", version="1.0.0")


def get_db():
    """Get a read-only SQLite connection."""
    db_uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    return conn


# ---- API Endpoints ----

@app.get("/api/state")
def api_state():
    """Get current engine state + liveness check."""
    try:
        conn = get_db()
        row = conn.execute("SELECT value, updated_at FROM state WHERE key = 'engine'").fetchone()
        conn.close()
        
        if not row:
            return {"state": None, "server_time": datetime.utcnow().isoformat() + 'Z', "live": False}
        
        state = json.loads(row['value'])
        updated_at = row['updated_at']
        
        # Check liveness: heartbeat within last 10s
        live = False
        if state.get('heartbeat'):
            try:
                hb = datetime.fromisoformat(state['heartbeat'].replace('Z', '+00:00'))
                age = (datetime.now(hb.tzinfo) - hb).total_seconds()
                live = age < 10
                state['heartbeat_age'] = round(age, 1)
            except Exception:
                pass
        
        return {
            "state": state,
            "server_time": datetime.utcnow().isoformat() + 'Z',
            "live": live,
        }
    except Exception as e:
        logger.error(f"API state error: {e}")
        return {"state": None, "server_time": datetime.utcnow().isoformat() + 'Z', "live": False, "error": str(e)}


@app.get("/api/candles")
def api_candles(
    symbol: str = Query(..., description="Symbol to fetch candles for"),
    from_ts: int = Query(None, alias="from", description="Start timestamp (UTC epoch)"),
    to_ts: int = Query(None, alias="to", description="End timestamp (UTC epoch)"),
):
    """Get OHLC candle data for a symbol."""
    try:
        conn = get_db()
        query = "SELECT bucket_time as time, open, high, low, close FROM candles WHERE symbol = ?"
        params = [symbol]
        
        if from_ts is not None:
            query += " AND bucket_time >= ?"
            params.append(from_ts)
        if to_ts is not None:
            query += " AND bucket_time <= ?"
            params.append(to_ts)
        
        # Default: today's candles (IST: UTC+5:30)
        if from_ts is None and to_ts is None:
            # Start of today IST = midnight IST in UTC epoch
            import pytz
            ist = pytz.timezone('Asia/Kolkata')
            today_ist = datetime.now(ist).replace(hour=0, minute=0, second=0, microsecond=0)
            today_utc_epoch = int(today_ist.timestamp())
            query += " AND bucket_time >= ?"
            params.append(today_utc_epoch)
        
        query += " ORDER BY bucket_time ASC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"API candles error: {e}")
        return []


@app.get("/api/trades")
def api_trades(
    date: str = Query(None, description="Filter by date (YYYY-MM-DD) or 'all'"),
):
    """Get trade records, optionally filtered by date."""
    try:
        conn = get_db()
        
        if date and date != 'all':
            rows = conn.execute("SELECT * FROM trades WHERE date = ? ORDER BY id ASC", (date,)).fetchall()
        elif date == 'all':
            rows = conn.execute("SELECT * FROM trades ORDER BY id ASC").fetchall()
        else:
            # Default: today
            import pytz
            ist = pytz.timezone('Asia/Kolkata')
            today = datetime.now(ist).strftime('%Y-%m-%d')
            rows = conn.execute("SELECT * FROM trades WHERE date = ? ORDER BY id ASC", (today,)).fetchall()
        
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"API trades error: {e}")
        return []


@app.get("/api/trades/dates")
def api_trade_dates():
    """Get list of distinct trade dates."""
    try:
        conn = get_db()
        rows = conn.execute("SELECT DISTINCT date FROM trades ORDER BY date DESC").fetchall()
        conn.close()
        return [r['date'] for r in rows]
    except Exception as e:
        logger.error(f"API trade dates error: {e}")
        return []


@app.get("/api/trades/{trade_id}")
def api_trade_detail(trade_id: int):
    """Get a single trade with surrounding candle windows for chart rendering."""
    try:
        conn = get_db()
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        
        if not trade:
            conn.close()
            raise HTTPException(status_code=404, detail="Trade not found")
        
        trade_dict = dict(trade)
        
        # Get candle windows: [entry - 45min, exit + 30min]
        try:
            entry_dt = datetime.fromisoformat(trade_dict['entry_time'])
            exit_dt = datetime.fromisoformat(trade_dict['exit_time'])
        except Exception:
            # Fallback: parse as time-only (HH:MM:SS) within the trade date
            conn.close()
            return {"trade": trade_dict, "spot_candles": [], "option_candles": []}
        
        window_start = int((entry_dt - timedelta(minutes=45)).timestamp())
        window_end = int((exit_dt + timedelta(minutes=30)).timestamp())
        
        symbol = trade_dict['symbol']
        
        # Spot candles
        spot_rows = conn.execute(
            "SELECT bucket_time as time, open, high, low, close FROM candles WHERE symbol = 'NSE:NIFTY50-INDEX' AND bucket_time >= ? AND bucket_time <= ? ORDER BY bucket_time ASC",
            (window_start, window_end)
        ).fetchall()
        
        # Option candles
        option_rows = conn.execute(
            "SELECT bucket_time as time, open, high, low, close FROM candles WHERE symbol = ? AND bucket_time >= ? AND bucket_time <= ? ORDER BY bucket_time ASC",
            (symbol, window_start, window_end)
        ).fetchall()
        
        conn.close()
        
        return {
            "trade": trade_dict,
            "spot_candles": [dict(r) for r in spot_rows],
            "option_candles": [dict(r) for r in option_rows],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API trade detail error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/events")
def api_events(
    after_id: int = Query(None, description="Return events with id > after_id"),
    limit: int = Query(50, le=200),
):
    """Get activity events (incremental feed)."""
    try:
        conn = get_db()
        
        if after_id is not None:
            rows = conn.execute(
                "SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT ?",
                (after_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"API events error: {e}")
        return []


# ---- Static file serving ----

# Serve index.html at root
@app.get("/")
def serve_index():
    index_path = STATIC_DIR / 'index.html'
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"error": "index.html not found"}, status_code=404)


# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
