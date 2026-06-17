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
    date: str = Query(None, description="Trading date YYYY-MM-DD (overrides from/to)"),
):
    """Get OHLC candle data for a symbol."""
    try:
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        conn = get_db()
        query = "SELECT bucket_time as time, open, high, low, close FROM candles WHERE symbol = ?"
        params = [symbol]

        if date:
            # Explicit date: full IST day window
            from datetime import date as date_type
            d = datetime.strptime(date, "%Y-%m-%d")
            day_start = ist.localize(d.replace(hour=0, minute=0, second=0, microsecond=0))
            day_end   = ist.localize(d.replace(hour=23, minute=59, second=59, microsecond=0))
            query += " AND bucket_time >= ? AND bucket_time <= ?"
            params += [int(day_start.timestamp()), int(day_end.timestamp())]
        elif from_ts is not None or to_ts is not None:
            if from_ts is not None:
                query += " AND bucket_time >= ?"
                params.append(from_ts)
            if to_ts is not None:
                query += " AND bucket_time <= ?"
                params.append(to_ts)
        else:
            # Default: use the most recent date that has candle data for this symbol
            latest = conn.execute(
                "SELECT MAX(bucket_time) as mx FROM candles WHERE symbol = ?", (symbol,)
            ).fetchone()
            if latest and latest['mx']:
                latest_dt = datetime.fromtimestamp(latest['mx'], tz=ist)
                day_start = ist.localize(
                    datetime(latest_dt.year, latest_dt.month, latest_dt.day, 0, 0, 0)
                )
                query += " AND bucket_time >= ?"
                params.append(int(day_start.timestamp()))
            # else: no candles at all — return empty

        query += " ORDER BY bucket_time ASC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"API candles error: {e}")
        return []


@app.get("/api/symbols")
def api_symbols(
    date: str = Query(None, description="Trading date YYYY-MM-DD; omit for most recent day with data"),
):
    """List option symbols (CE/PE) that have candle data for the given date.

    Powers the dashboard's dynamic option dropdown — since the engine rotates
    the tracked option through the day, a date can have several CE/PE contracts.
    """
    try:
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        spot = 'NSE:NIFTY50-INDEX'
        conn = get_db()

        if date and date != 'all':
            d = datetime.strptime(date, "%Y-%m-%d")
            day_start = ist.localize(d.replace(hour=0, minute=0, second=0, microsecond=0))
            day_end = ist.localize(d.replace(hour=23, minute=59, second=59, microsecond=0))
            rows = conn.execute(
                "SELECT symbol, COUNT(*) c, MIN(bucket_time) mn, MAX(bucket_time) mx "
                "FROM candles WHERE symbol != ? AND bucket_time >= ? AND bucket_time <= ? "
                "GROUP BY symbol ORDER BY mn ASC",
                (spot, int(day_start.timestamp()), int(day_end.timestamp()))
            ).fetchall()
        else:
            # Default: most recent day that has any candle data
            latest = conn.execute("SELECT MAX(bucket_time) mx FROM candles").fetchone()
            if not latest or not latest['mx']:
                conn.close()
                return []
            ld = datetime.fromtimestamp(latest['mx'], tz=ist)
            day_start = ist.localize(datetime(ld.year, ld.month, ld.day, 0, 0, 0))
            rows = conn.execute(
                "SELECT symbol, COUNT(*) c, MIN(bucket_time) mn, MAX(bucket_time) mx "
                "FROM candles WHERE symbol != ? AND bucket_time >= ? "
                "GROUP BY symbol ORDER BY mn ASC",
                (spot, int(day_start.timestamp()))
            ).fetchall()

        conn.close()
        out = []
        for r in rows:
            sym = r['symbol']
            # Live Fyers symbols end with CE/PE; seed symbols embed CE_/PE_.
            opt_type = 'CE' if 'CE' in sym else ('PE' if 'PE' in sym else '?')
            out.append({
                'symbol': sym,
                'type': opt_type,
                'count': r['c'],
                'from': r['mn'],
                'to': r['mx'],
            })
        return out
    except Exception as e:
        logger.error(f"API symbols error: {e}")
        return []


@app.get("/api/trades")
def api_trades(
    date: str = Query(None, description="Filter by date (YYYY-MM-DD), 'all', or omit for most recent"),
):
    """Get trade records, optionally filtered by date."""
    try:
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        conn = get_db()

        if date == 'all':
            rows = conn.execute("SELECT * FROM trades ORDER BY id ASC").fetchall()
        elif date:
            rows = conn.execute(
                "SELECT * FROM trades WHERE date = ? ORDER BY id ASC", (date,)
            ).fetchall()
        else:
            # Default: most recent date that has trades
            # (falls back to today for live mode when trades are being written)
            latest = conn.execute(
                "SELECT MAX(date) as md FROM trades"
            ).fetchone()
            today = datetime.now(ist).strftime('%Y-%m-%d')
            use_date = today  # prefer today when running live
            if latest and latest['md']:
                # Use today if it has trades, otherwise fall back to latest with data
                has_today = conn.execute(
                    "SELECT COUNT(*) c FROM trades WHERE date = ?", (today,)
                ).fetchone()
                if not has_today or has_today['c'] == 0:
                    use_date = latest['md']
            rows = conn.execute(
                "SELECT * FROM trades WHERE date = ? ORDER BY id ASC", (use_date,)
            ).fetchall()

        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"API trades error: {e}")
        return []


@app.get("/api/summary")
def api_summary(
    date: str = Query(None, description="Date YYYY-MM-DD; omit for most recent date with data"),
):
    """Daily summary: Day P&L and signal count for a given date.

    Used by the header metrics that change when the user selects a date.
    Capital and Spot are NOT returned here — they come from /api/state.
    """
    try:
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        conn = get_db()

        # "all" → totals across every trade / signal ever recorded.
        if date == 'all':
            row = conn.execute(
                "SELECT COUNT(*) as trade_count, COALESCE(SUM(pnl_total), 0) as daily_pnl FROM trades"
            ).fetchone()
            sig_row = conn.execute(
                "SELECT COUNT(*) as sc FROM events WHERE kind = 'SIGNAL'"
            ).fetchone()
            conn.close()
            return {
                "date": "all",
                "daily_pnl": round(row['daily_pnl'], 2) if row else 0.0,
                "trade_count": row['trade_count'] if row else 0,
                "signal_count": sig_row['sc'] if sig_row else 0,
            }

        if not date:
            # Default: today if it has trades, else most recent
            today = datetime.now(ist).strftime('%Y-%m-%d')
            has_today = conn.execute(
                "SELECT COUNT(*) c FROM trades WHERE date = ?", (today,)
            ).fetchone()
            if has_today and has_today['c'] > 0:
                date = today
            else:
                latest = conn.execute("SELECT MAX(date) as md FROM trades").fetchone()
                date = latest['md'] if latest and latest['md'] else today

        row = conn.execute(
            "SELECT COUNT(*) as trade_count, COALESCE(SUM(pnl_total), 0) as daily_pnl "
            "FROM trades WHERE date = ?",
            (date,)
        ).fetchone()

        # Signal count: count SIGNAL events whose timestamp falls on this date.
        # Since events store UTC timestamps we match by date prefix.
        sig_row = conn.execute(
            "SELECT COUNT(*) as sc FROM events WHERE kind = 'SIGNAL' AND date(ts) = ?",
            (date,)
        ).fetchone()

        conn.close()
        return {
            "date": date,
            "daily_pnl": round(row['daily_pnl'], 2) if row else 0.0,
            "trade_count": row['trade_count'] if row else 0,
            "signal_count": sig_row['sc'] if sig_row else 0,
        }
    except Exception as e:
        logger.error(f"API summary error: {e}")
        return {"date": date, "daily_pnl": 0.0, "trade_count": 0, "signal_count": 0}


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
