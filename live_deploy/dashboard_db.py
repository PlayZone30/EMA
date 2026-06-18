"""
Dashboard DB — SQLite + WAL persistence layer
================================================
Thread-safe writer for the live trading dashboard.
One connection per thread via threading.local().
Every method is try/except-logged — a DB error MUST NEVER break tick processing.

Schema:
  - candles: OHLC candle data (symbol, bucket_time, open, high, low, close, is_final)
  - state: Engine state blob (key='engine')
  - trades: Completed trade records
  - events: Activity log (SIGNAL, ENTRY, EXIT, INFO, etc.)

Usage:
  db = DashboardDB()
  db.upsert_candle(symbol, candle_dict, is_final=True)
  db.upsert_state('engine', state_dict)
  db.insert_trade(trade_result, capital_after)
  db.insert_event('SIGNAL', 'CE_BUY detected', {'symbol': '...'})
"""

import os
import json
import time
import sqlite3
import logging
import threading
from datetime import datetime

logger = logging.getLogger('DashboardDB')

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live_dashboard.db')


class DashboardDB:
    """Thread-safe SQLite writer with WAL mode for concurrent reads."""
    
    def __init__(self, db_path=None):
        self._db_path = db_path or DB_FILE
        self._local = threading.local()
        # Initialize schema on the calling thread's connection
        self._ensure_schema()
    
    def _get_conn(self):
        """Get or create a connection for the current thread."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=3000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn
    
    def _ensure_schema(self):
        """Create tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                bucket_time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                is_final INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (symbol, bucket_time)
            );
            
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                lots INTEGER NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                risk REAL NOT NULL,
                highest_reached REAL NOT NULL,
                pnl_per_unit REAL NOT NULL,
                pnl_total REAL NOT NULL,
                capital_after REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                signal_reason TEXT,
                signal_time TEXT,
                signal_high REAL,
                signal_low REAL,
                signal_open REAL,
                signal_close REAL
            );
            
            CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date);
            
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                data TEXT
            );
            
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
        """)
        conn.commit()
        self._migrate()

    def _migrate(self):
        """Add columns introduced after the original schema (idempotent).

        Existing EBS-persisted DBs were created before signal_open/signal_close
        existed; add them if missing so insert_trade never fails.
        """
        try:
            conn = self._get_conn()
            cols = {r['name'] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
            for col in ('signal_open', 'signal_close'):
                if col not in cols:
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {col} REAL")
            conn.commit()
        except Exception as e:
            logger.error(f"DB migrate error: {e}")
    
    # ---- Candles ----
    
    def upsert_candle(self, symbol, candle, is_final):
        """Insert or update a candle row.
        
        Args:
            symbol: Instrument symbol
            candle: Dict with keys: time (datetime), open, high, low, close
            is_final: Whether the candle is complete
        """
        try:
            conn = self._get_conn()
            bucket_ts = int(candle['time'].timestamp()) if hasattr(candle['time'], 'timestamp') else int(candle['time'])
            conn.execute("""
                INSERT INTO candles (symbol, bucket_time, open, high, low, close, is_final)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, bucket_time) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    is_final = excluded.is_final
            """, (symbol, bucket_ts, candle['open'], candle['high'], candle['low'], candle['close'],
                  1 if is_final else 0))
            conn.commit()
        except Exception as e:
            logger.error(f"DB upsert_candle error: {e}")
    
    # ---- State ----
    
    def upsert_state(self, key, value_dict):
        """Insert or update the engine state blob."""
        try:
            conn = self._get_conn()
            now = datetime.utcnow().isoformat() + 'Z'
            conn.execute("""
                INSERT INTO state (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (key, json.dumps(value_dict, default=str), now))
            conn.commit()
        except Exception as e:
            logger.error(f"DB upsert_state error: {e}")
    
    # ---- Trades ----
    
    def insert_trade(self, trade, capital_after):
        """Insert a completed trade record.
        
        Args:
            trade: Dict from ActiveTrade5Min._close()
            capital_after: Running capital after the trade
        """
        try:
            conn = self._get_conn()
            
            entry_time = trade['entry_time'].isoformat() if hasattr(trade['entry_time'], 'isoformat') else str(trade['entry_time'])
            exit_time = trade['exit_time'].isoformat() if hasattr(trade['exit_time'], 'isoformat') else str(trade['exit_time'])
            signal_time = None
            if 'signal_time' in trade and trade['signal_time'] is not None:
                signal_time = trade['signal_time'].isoformat() if hasattr(trade['signal_time'], 'isoformat') else str(trade['signal_time'])
            
            date_str = trade['entry_time'].strftime('%Y-%m-%d') if hasattr(trade['entry_time'], 'strftime') else str(trade['entry_time'])[:10]
            
            conn.execute("""
                INSERT INTO trades (date, type, symbol, lots, entry_time, exit_time,
                    entry_price, exit_price, sl, tp, risk, highest_reached,
                    pnl_per_unit, pnl_total, capital_after, exit_reason, signal_reason,
                    signal_time, signal_high, signal_low, signal_open, signal_close)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date_str, trade['type'], trade['symbol'], trade['lots'],
                entry_time, exit_time,
                trade['entry_price'], trade['exit_price'],
                trade['sl'], trade['tp'], trade['risk'],
                trade['highest_reached'], trade['pnl_per_unit'], trade['pnl_total'],
                capital_after, trade['exit_reason'], trade.get('reason', ''),
                signal_time, trade.get('signal_high'), trade.get('signal_low'),
                trade.get('signal_open'), trade.get('signal_close'),
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"DB insert_trade error: {e}")
    
    # ---- Events ----
    
    def insert_event(self, kind, message, data=None, ts=None):
        """Insert an activity event.
        
        Args:
            kind: Event type (SIGNAL, SIGNAL_INVALID, SIGNAL_EXPIRED, ENTRY, EXIT, INFO)
            message: Human-readable description
            data: Optional dict with extra info
            ts: Optional explicit UTC ISO timestamp string. Defaults to now.
        """
        try:
            conn = self._get_conn()
            event_ts = ts if ts else (datetime.utcnow().isoformat() + 'Z')
            data_json = json.dumps(data, default=str) if data else None
            conn.execute("""
                INSERT INTO events (ts, kind, message, data) VALUES (?, ?, ?, ?)
            """, (event_ts, kind, message, data_json))
            conn.commit()
        except Exception as e:
            logger.error(f"DB insert_event error: {e}")
    
    # ---- Read helpers (for dashboard API) ----
    
    def get_state(self, key='engine'):
        """Read the engine state blob."""
        try:
            conn = self._get_conn()
            row = conn.execute("SELECT value, updated_at FROM state WHERE key = ?", (key,)).fetchone()
            if row:
                return json.loads(row['value']), row['updated_at']
            return None, None
        except Exception as e:
            logger.error(f"DB get_state error: {e}")
            return None, None
    
    def get_candles(self, symbol, from_ts=None, to_ts=None):
        """Get candles for a symbol in a time range."""
        try:
            conn = self._get_conn()
            query = "SELECT bucket_time, open, high, low, close, is_final FROM candles WHERE symbol = ?"
            params = [symbol]
            if from_ts is not None:
                query += " AND bucket_time >= ?"
                params.append(int(from_ts))
            if to_ts is not None:
                query += " AND bucket_time <= ?"
                params.append(int(to_ts))
            query += " ORDER BY bucket_time ASC"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"DB get_candles error: {e}")
            return []
    
    def get_trades(self, date=None):
        """Get trades, optionally filtered by date."""
        try:
            conn = self._get_conn()
            if date:
                rows = conn.execute("SELECT * FROM trades WHERE date = ? ORDER BY id ASC", (date,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM trades ORDER BY id ASC").fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"DB get_trades error: {e}")
            return []
    
    def get_trade_by_id(self, trade_id):
        """Get a single trade by ID."""
        try:
            conn = self._get_conn()
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"DB get_trade_by_id error: {e}")
            return None
    
    def get_last_capital(self):
        """Return capital_after of the most recent trade, or None if no trades.

        Used by the collector to resume the compounding capital across daily
        restarts (EC2 stop/start) instead of resetting to the ₹20,000 base.
        """
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT capital_after FROM trades ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row and row['capital_after'] is not None:
                return float(row['capital_after'])
            return None
        except Exception as e:
            logger.error(f"DB get_last_capital error: {e}")
            return None

    def get_daily_pnl(self, date):
        """Return summed pnl_total for a given trading date (YYYY-MM-DD).

        Lets the collector resume today's running P&L if it restarts mid-day.
        """
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl_total), 0) AS pnl FROM trades WHERE date = ?",
                (date,)
            ).fetchone()
            return float(row['pnl']) if row else 0.0
        except Exception as e:
            logger.error(f"DB get_daily_pnl error: {e}")
            return 0.0

    def get_trade_dates(self):
        """Get list of distinct trade dates."""
        try:
            conn = self._get_conn()
            rows = conn.execute("SELECT DISTINCT date FROM trades ORDER BY date DESC").fetchall()
            return [r['date'] for r in rows]
        except Exception as e:
            logger.error(f"DB get_trade_dates error: {e}")
            return []
    
    def get_events(self, after_id=None, limit=50):
        """Get events, optionally after a certain ID."""
        try:
            conn = self._get_conn()
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
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"DB get_events error: {e}")
            return []
