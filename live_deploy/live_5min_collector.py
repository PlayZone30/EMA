"""
Live Forward Test: 5-Min Divergence Strategy 
=============================================================
Uses WebSocket tick data for real-time paper trading.

Strategy:
- Divergence Signal on 5-min candles:
  - PE Buy: Spot Green AND PE Green
  - CE Buy: Spot Red AND CE Green
- Case 1 Entry Only: Tick LTP > divergence candle's high BEFORE breaking low.
  - If ltp < sig_low -> signal invalid.
  - If next 5-min candle closes without triggering -> signal invalid.
- Dynamic Risk Management:
  - avg_candle_size = average high-low of past candles today
  - orig_risk = entry - sig_low
  - final_risk = max(orig_risk, avg_candle_size)
  - SL = max(Entry - final_risk, 0.05)  [clamped to prevent negative]
  - TP = Entry + (2.5 * final_risk)  [recomputed from clamped risk]
- Capital Compounding: Starts at ₹20,000 | Lot Size: 65

Bug fixes applied (audit A1-A9):
  A1: Missed-signal race — check divergence on ANY candle completion
  A2: Expired-signal entry leak — sweep every tick, store expires_at
  A3: Remove cur_ce/cur_pe guard (folded into A2)
  A4: WS disconnect resilience — _stop_event, _on_close only logs
  A5: Repeated EOD — one-shot _eod_done flag
  A6: Negative SL — clamp to 0.05, recompute risk/TP
  A7: Entry trigger — strict < instead of <=
  A8: Deterministic time — thread `now` through CandleManager
  A9: Signal candle tracking in ActiveTrade + _close()

Usage: python live_5min_collector.py
"""

import os
import csv
import json
import time
import pytz
import logging
import dotenv
import threading
from datetime import datetime, time as dt_time
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

dotenv.load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('live_5min_trades.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('Live5MinDivergence')

IST = pytz.timezone('Asia/Kolkata')

# CONSTANTS
SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
CAPITAL = 20000
LOT_SIZE = 65
OPTION_PRICE_MIN = 70.0
OPTION_PRICE_MAX = 80.0
OPTION_PRICE_TARGET = 75.0
STRIKE_STEP = 50
OPTION_REFRESH_SECONDS = 600
API_DELAY = 0.6
TRADE_LOG_FILE = "live_5min_trades.csv"
MARKET_DATA_OPEN = dt_time(9, 15)  # Start collecting candles here (NSE open) — feeds avg candle size
MARKET_OPEN = dt_time(9, 30)  # No ENTRIES before 9:30 (data still collected from 9:15)
MARKET_CLOSE = dt_time(15, 30)

# ---------- A8: Deterministic time through CandleManager ----------
class CandleManager5Min:
    def __init__(self):
        self.current = {}
    
    def _bucket(self, now):
        """Compute 5-min bucket from explicit timestamp (A8)."""
        m = (now.hour * 60 + now.minute) // 5 * 5
        return now.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
        
    def update(self, symbol, ltp, now):
        """Update candle for symbol. Returns completed candle dict or None.
        
        Args:
            symbol: Instrument symbol
            ltp: Last traded price
            now: Explicit datetime for deterministic behavior (A8)
        """
        b = self._bucket(now)
        cur = self.current.get(symbol)
        if cur is None or cur["bucket"] != b:
            comp = None
            if cur is not None:
                comp = {'time': cur['bucket'], 'open': cur['open'], 'high': cur['high'], 'low': cur['low'], 'close': cur['close']}
            self.current[symbol] = {'bucket': b, 'open': ltp, 'high': ltp, 'low': ltp, 'close': ltp}
            return comp
        else:
            cur['high'] = max(cur['high'], ltp)
            cur['low'] = min(cur['low'], ltp)
            cur['close'] = ltp
            return None

    def get_in_progress(self, symbol):
        """Return the current in-progress candle for a symbol (for dashboard DB writes)."""
        cur = self.current.get(symbol)
        if cur is None:
            return None
        return {'time': cur['bucket'], 'open': cur['open'], 'high': cur['high'], 'low': cur['low'], 'close': cur['close']}

# ---------- A6 + A9: ActiveTrade with SL clamp and signal_candle ----------
class ActiveTrade5Min:
    def __init__(self, symbol, trade_type, entry_price, entry_time, signal_candle, reason, avg_candle_size, lots):
        self.symbol = symbol
        self.trade_type = trade_type
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.signal_candle = signal_candle  # A9: keep full signal candle
        self.reason = reason
        self.lots = lots
        
        sig_low = signal_candle['low']
        orig_risk = entry_price - sig_low
        orig_risk = orig_risk if orig_risk > 0 else 0.1
        self._orig_risk = orig_risk  # base risk from signal low (fallback)

        # Lock guards SL/TP which may be updated by the background avg-fetch
        # thread after entry (History API) while the WS thread reads them.
        self._lock = threading.Lock()
        # True until the real History-based average has been applied.
        self.avg_pending = (avg_candle_size is None)

        self.final_risk = max(orig_risk, avg_candle_size or 0.0)

        # A6: Clamp SL to minimum 0.05 to prevent negative SL
        self.sl = max(entry_price - self.final_risk, 0.05)
        # A6: Recompute final_risk and TP from clamped SL so 2.5R stays truthful
        self.final_risk = entry_price - self.sl
        self.tp = entry_price + (2.5 * self.final_risk)

        self.highest_reached = entry_price
        self.is_open = True
        self.exit_price = None
        self.exit_time = None
        self.exit_reason = None

    def apply_avg_candle_size(self, avg_candle_size):
        """Recompute SL/TP once the real (History API) average is known.

        Called from the background fetch thread. Only widens risk
        (final_risk = max(orig_risk, avg)). Returns True if SL/TP changed.
        """
        if avg_candle_size is None:
            return False
        with self._lock:
            if not self.is_open:
                return False
            new_risk = max(self._orig_risk, avg_candle_size)
            new_sl = max(self.entry_price - new_risk, 0.05)
            new_risk = self.entry_price - new_sl
            new_tp = self.entry_price + (2.5 * new_risk)
            changed = (abs(new_sl - self.sl) > 1e-9) or (abs(new_tp - self.tp) > 1e-9)
            self.final_risk = new_risk
            self.sl = new_sl
            self.tp = new_tp
            self.avg_pending = False
            return changed

    def update_tick(self, ltp, timestamp):
        if not self.is_open: return None
        self.highest_reached = max(self.highest_reached, ltp)

        with self._lock:
            sl, tp = self.sl, self.tp
        if ltp <= sl:
            return self._close(sl, timestamp, 'SL')
        if ltp >= tp:
            return self._close(tp, timestamp, 'TP')
        return None
        
    def close_eod(self, ltp, timestamp):
        if self.is_open:
            return self._close(ltp, timestamp, 'EOD_CLOSE')
        return None
        
    def _close(self, exit_price, timestamp, reason):
        self.is_open = False
        self.exit_price = exit_price
        self.exit_time = timestamp
        self.exit_reason = reason
        
        pnl_per_unit = exit_price - self.entry_price
        pnl_total = pnl_per_unit * LOT_SIZE * self.lots
        return {
            'symbol': self.symbol, 'type': self.trade_type,
            'entry_price': self.entry_price, 'entry_time': self.entry_time,
            'exit_price': exit_price, 'exit_time': timestamp,
            'sl': self.sl, 'tp': self.tp, 'risk': self.final_risk, 'lots': self.lots,
            'highest_reached': self.highest_reached, 'pnl_per_unit': pnl_per_unit,
            'pnl_total': pnl_total, 'exit_reason': reason, 'reason': self.reason,
            # A9: signal candle info for trade records
            'signal_time': self.signal_candle['time'],
            'signal_high': self.signal_candle['high'],
            'signal_low': self.signal_candle['low'],
        }

class Live5MinEngine:
    def __init__(self):
        self.fyers = None
        self.fyers_ws = None
        self.access_token = None
        self.client_id = None
        self.is_running = False  # WS connected flag only (A4)

        self.ce_symbol = None
        self.pe_symbol = None
        self.ce_ltp = None
        self.pe_ltp = None
        self.spot_ltp = None
        
        self._symbol_lock = threading.Lock()
        self._refresh_thread = None
        # A4: Stop event for clean shutdown — main loop + refresh worker check this
        self._stop_event = threading.Event()
        # A5: One-shot EOD flag
        self._eod_done = False

        self.candle_manager = CandleManager5Min()
        self.candle_history = {} # Keep ALL today's 5min candles for avg size
        
        self.pending_signals = {}
        # A1: Deduplication — track which (symbol, bucket) combos already raised a signal
        self.last_signal_bucket = {}
        self.active_trade = None
        self.trade_history = []
        self.daily_signals = 0

        self.running_capital = CAPITAL
        self.daily_pnl = 0.0
        
        # DB persistence (Part B) — initialized in run() if dashboard_db available
        self.db = None
        self._last_db_write = 0.0  # throttle in-progress writes to ~1/sec
        
        self._init_csv()

    def _init_csv(self):
        if not os.path.exists(TRADE_LOG_FILE):
            with open(TRADE_LOG_FILE, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Date', 'Type', 'Symbol', 'Lots', 'Entry Time', 'Entry Price',
                    'Exit Time', 'Exit Price', 'SL', 'TP', 'Risk',
                    'Highest Reached', 'PnL/Unit', 'PnL Total', 'Capital After',
                    'Exit Reason', 'Signal Reason'
                ])

    def _log_trade_csv(self, trade):
        with open(TRADE_LOG_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            entry_t = trade['entry_time'].strftime('%H:%M:%S')
            exit_t = trade['exit_time'].strftime('%H:%M:%S')
            writer.writerow([
                datetime.now(IST).strftime('%Y-%m-%d'),
                trade['type'], trade['symbol'], trade['lots'],
                entry_t, f"{trade['entry_price']:.2f}",
                exit_t, f"{trade['exit_price']:.2f}",
                f"{trade['sl']:.2f}", f"{trade['tp']:.2f}", f"{trade['risk']:.2f}",
                f"{trade['highest_reached']:.2f}", f"{trade['pnl_per_unit']:.2f}",
                f"{trade['pnl_total']:.2f}", f"{self.running_capital:.2f}",
                trade['exit_reason'], trade.get('reason', ''),
            ])

    def _init_db(self):
        """Try to initialize dashboard DB. Failure is non-fatal."""
        try:
            from dashboard_db import DashboardDB
            self.db = DashboardDB()
            logger.info("📊 Dashboard DB initialized")
            self._resume_from_db()
        except Exception as e:
            logger.warning(f"Dashboard DB not available (non-fatal): {e}")
            self.db = None

    def _resume_from_db(self):
        """Resume compounding capital (and today's P&L) from the DB.

        EC2 stops daily and the EBS volume persists live_dashboard.db, so on the
        next start we carry the running capital forward instead of resetting to
        the ₹20,000 base. If the process restarts mid-day, today's realized P&L
        is also restored so the dashboard header stays consistent.
        """
        try:
            last_cap = self.db.get_last_capital()
            if last_cap is not None and last_cap > 0:
                self.running_capital = last_cap
                logger.info(f"💰 Resumed running capital from DB: ₹{last_cap:.2f}")
            else:
                logger.info(f"💰 No prior capital in DB — starting at ₹{CAPITAL:.2f}")

            today = datetime.now(IST).strftime('%Y-%m-%d')
            self.daily_pnl = self.db.get_daily_pnl(today)
            if self.daily_pnl:
                logger.info(f"📈 Resumed today's P&L from DB: ₹{self.daily_pnl:+.2f}")
        except Exception as e:
            logger.warning(f"Could not resume from DB (non-fatal): {e}")

    def _db_log_event(self, kind, message, data=None):
        """Log an event to the dashboard DB. Never raises."""
        if self.db:
            try:
                self.db.insert_event(kind, message, data)
            except Exception:
                pass

    def _db_write_state(self, force=False):
        """Write engine state to dashboard DB, throttled unless force=True."""
        if not self.db:
            return
        now_ts = time.time()
        if not force and (now_ts - self._last_db_write) < 1.0:
            return
        self._last_db_write = now_ts
        try:
            active_trade_data = None
            if self.active_trade and self.active_trade.is_open:
                t = self.active_trade
                active_trade_data = {
                    'symbol': t.symbol, 'type': t.trade_type,
                    'entry': t.entry_price, 'sl': t.sl, 'tp': t.tp,
                    'lots': t.lots, 'highest': t.highest_reached,
                    'reason': t.reason, 'entry_time': t.entry_time.isoformat(),
                }
            
            pending_list = []
            for sym, sig in self.pending_signals.items():
                pending_list.append({
                    'symbol': sym, 'type': sig['type'],
                    'high': sig['high'], 'low': sig['low'],
                    'expires_at': sig['expires_at'],
                })
            
            state = {
                'heartbeat': datetime.now(IST).isoformat(),
                'ws_connected': self.is_running,
                'running_capital': self.running_capital,
                'daily_pnl': self.daily_pnl,
                'daily_signals': self.daily_signals,
                'spot_ltp': self.spot_ltp,
                'ce_ltp': self.ce_ltp,
                'pe_ltp': self.pe_ltp,
                'ce_symbol': self.ce_symbol,
                'pe_symbol': self.pe_symbol,
                'active_trade': active_trade_data,
                'pending_signals': pending_list,
            }
            self.db.upsert_state('engine', state)
        except Exception:
            pass

    def _db_write_candle(self, symbol, candle, is_final):
        """Write a candle (final or in-progress) to the dashboard DB."""
        if not self.db:
            return
        try:
            self.db.upsert_candle(symbol, candle, is_final)
        except Exception:
            pass

    def authenticate(self):
        from auth_helper import FyersAuthenticator
        cid = os.getenv("CLIENT_ID")
        sk = os.getenv("SECRET_KEY")
        user = os.getenv("USERNAME")
        pin = os.getenv("PIN")
        totp = os.getenv("TOTP_KEY")
        
        auth = FyersAuthenticator(cid, sk, "https://www.google.com", user, pin, totp)
        token, err = auth.get_access_token()
        if not token:
            logger.error(f"Auth failed: {err}")
            return False

        self.access_token = token
        self.client_id = cid
        self.fyers = fyersModel.FyersModel(client_id=cid, token=token, log_path="")
        logger.info("✅ Authentication successful!")
        return True

    def find_options_in_range(self):
        try:
            time.sleep(API_DELAY)
            data = {"symbol": SPOT_SYMBOL, "strikecount": 5, "timestamp": ""}
            resp = self.fyers.optionchain(data=data)
            if resp.get('code') != 200: return None, None
            nearest_expiry = str(resp['data']['expiryData'][0]['expiry'])

            time.sleep(API_DELAY)
            data = {"symbol": SPOT_SYMBOL, "strikecount": 30, "timestamp": nearest_expiry}
            resp = self.fyers.optionchain(data=data)
            if resp.get('code') != 200: return None, None
            options = resp['data']['optionsChain']

            ce_cand = [o for o in options if o.get('option_type') == 'CE' and OPTION_PRICE_MIN <= o.get('ltp', 0) <= OPTION_PRICE_MAX]
            pe_cand = [o for o in options if o.get('option_type') == 'PE' and OPTION_PRICE_MIN <= o.get('ltp', 0) <= OPTION_PRICE_MAX]
            
            if not ce_cand or not pe_cand: # fallback
                ce_cand = [o for o in options if o.get('option_type') == 'CE' and 50 <= o.get('ltp', 0) <= 80]
                pe_cand = [o for o in options if o.get('option_type') == 'PE' and 50 <= o.get('ltp', 0) <= 80]

            ce_pick = min(ce_cand, key=lambda o: abs(o['ltp'] - OPTION_PRICE_TARGET)) if ce_cand else None
            pe_pick = min(pe_cand, key=lambda o: abs(o['ltp'] - OPTION_PRICE_TARGET)) if pe_cand else None

            if ce_pick and pe_pick:
                logger.info(f"  CE: {ce_pick['symbol']} | PE: {pe_pick['symbol']}")
                return ce_pick['symbol'], pe_pick['symbol']
            return None, None
        except Exception as e:
            logger.error(f"Error finding options: {e}")
            return None, None

    # ---------- History API: accurate avg candle size (rotation-proof) ----------
    def _fetch_history_candles(self, symbol, range_from, range_to):
        """Fetch completed 5-min candles for `symbol` via Fyers History API.

        Returns a list of dicts {time(datetime), open, high, low, close} in IST,
        or [] on any failure. Safe to call from a background thread.
        """
        try:
            data = {
                "symbol": symbol,
                "resolution": "5",
                "date_format": "0",
                "range_from": str(int(range_from)),
                "range_to": str(int(range_to)),
                "cont_flag": "1",
            }
            resp = self.fyers.history(data=data)
            if not isinstance(resp, dict) or resp.get('s') != 'ok':
                logger.warning(f"History API non-ok for {symbol}: {resp.get('s') if isinstance(resp, dict) else resp}")
                return []
            out = []
            for c in resp.get('candles', []):
                # [epoch, open, high, low, close, volume]
                out.append({
                    'time': datetime.fromtimestamp(c[0], IST),
                    'open': c[1], 'high': c[2], 'low': c[3], 'close': c[4],
                })
            return out
        except Exception as e:
            logger.error(f"History fetch error for {symbol}: {e}")
            return []

    def _compute_avg_candle_size(self, symbol, signal_candle_time):
        """Average (high-low) over the option's 5-min candles from 09:15 up to
        AND INCLUDING the signal candle — mirrors the backtest. History API based
        so it's correct even after option rotation wiped the websocket history.

        Returns (avg or None, candle_list). candle_list is also used to backfill
        the DB so the dashboard chart is gap-free for traded contracts.
        """
        try:
            day_open = signal_candle_time.replace(hour=MARKET_DATA_OPEN.hour,
                                                  minute=MARKET_DATA_OPEN.minute,
                                                  second=0, microsecond=0)
            range_from = day_open.timestamp()
            # include the signal candle (bucket start) and its full 5 min
            range_to = signal_candle_time.timestamp() + 299
            candles = self._fetch_history_candles(symbol, range_from, range_to)
            # Keep only candles up to & including the signal candle
            candles = [c for c in candles if c['time'] <= signal_candle_time]
            if not candles:
                return None, []
            avg = sum(c['high'] - c['low'] for c in candles) / len(candles)
            return avg, candles
        except Exception as e:
            logger.error(f"compute_avg_candle_size error for {symbol}: {e}")
            return None, []

    def _prefetch_signal_avg(self, symbol, signal_candle_time):
        """Background worker: compute the History-based avg for a pending signal
        and store it on the signal. If the signal has already become the live
        trade and is awaiting the avg, update its SL/TP. Never blocks the WS."""
        avg, candles = self._compute_avg_candle_size(symbol, signal_candle_time)

        # Backfill the option's candles into the DB (fills dashboard chart gaps)
        if candles and self.db:
            for c in candles:
                self._db_write_candle(symbol, c, is_final=True)

        if avg is None:
            return

        # Store on the pending signal (used at entry if still pending)
        sig = self.pending_signals.get(symbol)
        if sig is not None:
            sig['avg_candle_size'] = avg

        # If this signal already became the active trade waiting for the avg, update it
        at = self.active_trade
        if at is not None and at.symbol == symbol and at.is_open and at.avg_pending:
            changed = at.apply_avg_candle_size(avg)
            if changed:
                logger.info(f"  🔧 SL/TP updated from History avg={avg:.2f} → SL {at.sl:.2f} TP {at.tp:.2f}")
                self._db_log_event('INFO', f"SL/TP updated (avg={avg:.2f}): SL={at.sl:.2f} TP={at.tp:.2f}", {
                    'symbol': symbol, 'sl': at.sl, 'tp': at.tp,
                })
                self._db_write_state(force=True)

    def _option_refresh_worker(self):
        # A4: Loop on _stop_event instead of is_running
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=OPTION_REFRESH_SECONDS)
            if self._stop_event.is_set():
                break
            if self.active_trade is not None or len(self.pending_signals) > 0:
                continue

            try:
                new_ce, new_pe = self.find_options_in_range()
                if not new_ce or not new_pe: continue
                with self._symbol_lock:
                    unsub, sub = [], []
                    if new_ce != self.ce_symbol:
                        if self.ce_symbol: unsub.append(self.ce_symbol)
                        sub.append(new_ce)
                        self.ce_symbol = new_ce
                        self.ce_ltp = None
                    if new_pe != self.pe_symbol:
                        if self.pe_symbol: unsub.append(self.pe_symbol)
                        sub.append(new_pe)
                        self.pe_symbol = new_pe
                        self.pe_ltp = None

                    self.candle_history = {k: v for k, v in self.candle_history.items() if k == SPOT_SYMBOL}
                    
                if self.fyers_ws:
                    if unsub:
                        self.fyers_ws.unsubscribe(symbols=unsub)
                        time.sleep(0.3)
                    if sub:
                        self.fyers_ws.subscribe(symbols=sub, data_type="SymbolUpdate")
                        logger.info(f"📡 Subscribed: {sub}")
            except Exception as e:
                logger.error(f"Refresh error: {e}")

    def _store_candle(self, symbol, candle):
        # A4-race fix: guard candle_history (refresh worker may replace the dict
        # under _symbol_lock concurrently). Same-thread as _on_message, but the
        # lock here is released before check_divergence runs (no nesting).
        with self._symbol_lock:
            if symbol not in self.candle_history:
                self.candle_history[symbol] = []
            self.candle_history[symbol].append(candle)
            # NOT POPPING - keep all candles for accurate avg candle size computation!

        # DB: write final candle
        self._db_write_candle(symbol, candle, is_final=True)

    # ---------- A1: Check divergence on ANY candle completion ----------
    def check_divergence(self, completed_symbol, timestamp):
        """Check for divergence signals.
        
        A1 fix: Called whenever ANY symbol's candle completes (not just SPOT).
        Evaluates each pair only when both latest candles share the same bucket.
        Deduplicates via self.last_signal_bucket.
        """
        # Single-trade rule: while a trade is open, ignore new setups entirely
        # (matches the backtest's overlap guard — preference to the first trade).
        if self.active_trade is not None:
            return

        # Snapshot candle_history references under the lock (refresh worker may
        # replace the dict concurrently).
        with self._symbol_lock:
            spot_candles = list(self.candle_history.get(SPOT_SYMBOL, []))
            pe_candles = list(self.candle_history.get(self.pe_symbol, [])) if self.pe_symbol else []
            ce_candles = list(self.candle_history.get(self.ce_symbol, [])) if self.ce_symbol else []

        if not spot_candles:
            return
        spot_candle = spot_candles[-1]

        # Check PE divergence (Spot GREEN + PE GREEN)
        if self.pe_symbol:
            if pe_candles and pe_candles[-1]['time'] == spot_candle['time']:
                pe_c = pe_candles[-1]
                # A1: Dedupe — skip if signal already raised for this (pe_symbol, bucket)
                bucket_key = (self.pe_symbol, spot_candle['time'])
                if bucket_key not in self.last_signal_bucket:
                    if spot_candle['close'] > spot_candle['open'] and pe_c['close'] > pe_c['open']:
                        self.daily_signals += 1
                        self.last_signal_bucket[bucket_key] = True
                        rsn = f"Spot GREEN + PE GREEN"
                        # A2: Entry is only valid during the candle IMMEDIATELY
                        # after the signal candle. The signal candle's bucket
                        # starts at T and ends at T+300; the next (entry) candle
                        # spans [T+300, T+600). pe_c['time'] is the bucket START
                        # (T), so expires_at = T+600 = end of that next candle.
                        # => one 5-min bucket to break the high, else it expires.
                        self.pending_signals[self.pe_symbol] = {
                            'type': 'PE_BUY', 'high': pe_c['high'], 'low': pe_c['low'],
                            'candle': pe_c, 'reason': rsn,
                            'expires_at': pe_c['time'].timestamp() + 600,
                            'avg_candle_size': None,  # filled by background History fetch
                        }
                        print()
                        logger.info(f"🎯 PE Signal Detected: {self.pe_symbol} | High: {pe_c['high']:.2f}")
                        self._db_log_event('SIGNAL', f"PE_BUY signal: {self.pe_symbol} high={pe_c['high']:.2f}", {
                            'symbol': self.pe_symbol, 'type': 'PE_BUY', 'high': pe_c['high'], 'low': pe_c['low']
                        })
                        # Pre-fetch the accurate avg candle size (History API) in
                        # the background so SL is correct at entry despite rotation.
                        threading.Thread(
                            target=self._prefetch_signal_avg,
                            args=(self.pe_symbol, pe_c['time']), daemon=True
                        ).start()

        # Check CE divergence (Spot RED + CE GREEN)
        if self.ce_symbol:
            if ce_candles and ce_candles[-1]['time'] == spot_candle['time']:
                ce_c = ce_candles[-1]
                # A1: Dedupe
                bucket_key = (self.ce_symbol, spot_candle['time'])
                if bucket_key not in self.last_signal_bucket:
                    if spot_candle['close'] < spot_candle['open'] and ce_c['close'] > ce_c['open']:
                        self.daily_signals += 1
                        self.last_signal_bucket[bucket_key] = True
                        rsn = f"Spot RED + CE GREEN"
                        # A2: same as PE above — entry valid only during the
                        # immediately-next 5-min candle ([T+300, T+600)).
                        self.pending_signals[self.ce_symbol] = {
                            'type': 'CE_BUY', 'high': ce_c['high'], 'low': ce_c['low'],
                            'candle': ce_c, 'reason': rsn,
                            'expires_at': ce_c['time'].timestamp() + 600,
                            'avg_candle_size': None,  # filled by background History fetch
                        }
                        print()
                        logger.info(f"🎯 CE Signal Detected: {self.ce_symbol} | High: {ce_c['high']:.2f}")
                        self._db_log_event('SIGNAL', f"CE_BUY signal: {self.ce_symbol} high={ce_c['high']:.2f}", {
                            'symbol': self.ce_symbol, 'type': 'CE_BUY', 'high': ce_c['high'], 'low': ce_c['low']
                        })
                        threading.Thread(
                            target=self._prefetch_signal_avg,
                            args=(self.ce_symbol, ce_c['time']), daemon=True
                        ).start()

    def _on_message(self, message):
        try:
            if not isinstance(message, dict) or 'ltp' not in message: return
            if 'symbol' not in message: return
            
            # A8: Use explicit now for deterministic behavior
            symbol, ltp, now = message['symbol'], message['ltp'], datetime.now(IST)

            with self._symbol_lock:
                cur_ce, cur_pe = self.ce_symbol, self.pe_symbol

            if symbol == SPOT_SYMBOL: self.spot_ltp = ltp
            elif symbol == cur_ce: self.ce_ltp = ltp
            elif symbol == cur_pe: self.pe_ltp = ltp
            else: return

            # A5: One-shot EOD check
            if now.time() > MARKET_CLOSE:
                if not self._eod_done:
                    self._handle_eod(now)
                return

            # ---- A2: Sweep ALL pending_signals for expiry FIRST, every tick ----
            now_ts = now.timestamp()
            expired_syms = [s for s, sig in self.pending_signals.items() if now_ts > sig['expires_at']]
            for s in expired_syms:
                logger.info(f"  ❌ Signal expired for {s} (No breakout in time)")
                self._db_log_event('SIGNAL_EXPIRED', f"Signal expired: {s}")
                del self.pending_signals[s]

            # Active Trade Management
            if self.active_trade is not None and symbol == self.active_trade.symbol:
                res = self.active_trade.update_tick(ltp, now)
                if res: self._handle_trade_exit(res)

            # Pending Signal Check (runs AFTER expiry sweep — A2 fix)
            if symbol in self.pending_signals and self.active_trade is None and now.time() >= MARKET_OPEN:
                sig = self.pending_signals[symbol]
                
                # A7: strict < instead of <=
                if ltp < sig['low']:
                    logger.info(f"  ❌ Signal invalidated for {symbol} (Low broke first)")
                    self._db_log_event('SIGNAL_INVALID', f"Signal invalidated: {symbol} (low broke)")
                    del self.pending_signals[symbol]
                elif ltp >= sig['high']:
                    self._enter_trade(symbol, ltp, now, sig)
                    del self.pending_signals[symbol]

            # Candle Building — A8: pass `now` explicitly
            comp = self.candle_manager.update(symbol, ltp, now)
            if comp:
                self._store_candle(symbol, comp)
                
                # A1: Check divergence whenever ANY symbol's candle completes
                # (old code only checked when SPOT candle completed)
                self.check_divergence(symbol, now)
            
            # DB: write in-progress candle + state (throttled)
            if self.db:
                in_prog = self.candle_manager.get_in_progress(symbol)
                if in_prog:
                    self._db_write_candle(symbol, in_prog, is_final=False)
                self._db_write_state()

            self._print_status(now)

        except Exception as e:
            logger.error(f"Tick error: {e}", exc_info=True)

    def _enter_trade(self, symbol, ltp, ts, sig):
        cost_per_lot = ltp * LOT_SIZE
        # Fixed 1 lot (65 units) per trade — matches backtest_2024_daywise.py
        # and seed_dashboard_db.py (no dynamic/compounding position sizing).
        lots = 1

        if self.running_capital < cost_per_lot:
            logger.warning(f"  ⚠️ Insufficient capital: Need ₹{cost_per_lot:.2f}, have ₹{self.running_capital:.2f}")
            return

        # Use the accurate History-based avg pre-fetched at signal time if ready.
        # If not ready yet (rare — fetch started at signal, entry is the next
        # candle), pass None → ActiveTrade enters with the SAFE SL = signal_low
        # and the background thread updates SL/TP the moment the avg arrives.
        avg_sz = sig.get('avg_candle_size')
        self.active_trade = ActiveTrade5Min(symbol, sig['type'], ltp, ts, sig['candle'], sig['reason'], avg_sz, lots)

        # Single-trade rule: drop any other armed signals (preference to this trade).
        for other in list(self.pending_signals.keys()):
            if other != symbol:
                del self.pending_signals[other]

        # If the avg wasn't ready, kick a fetch now so SL/TP get corrected ASAP.
        if avg_sz is None:
            threading.Thread(
                target=self._prefetch_signal_avg,
                args=(symbol, sig['candle']['time']), daemon=True
            ).start()

        avg_disp = avg_sz if avg_sz is not None else 0.0
        print()
        logger.info("="*60)
        logger.info(f"📈 TRADE ENTERED: {sig['type']} on {symbol}")
        logger.info(f"  Entry: ₹{ltp:.2f} | SL: ₹{self.active_trade.sl:.2f} | TP: ₹{self.active_trade.tp:.2f}")
        logger.info(f"  Risk Used: {self.active_trade.final_risk:.2f} (Avg Sz: {avg_disp:.2f}{' [pending]' if avg_sz is None else ''})")
        logger.info(f"  Lots: {lots} | Cost: ₹{cost_per_lot*lots:.2f} | Acc Bal: ₹{self.running_capital:.2f}")
        logger.info("="*60)
        
        self._db_log_event('ENTRY', f"Entered {sig['type']} on {symbol} @ ₹{ltp:.2f}", {
            'symbol': symbol, 'type': sig['type'], 'entry': ltp,
            'sl': self.active_trade.sl, 'tp': self.active_trade.tp, 'lots': lots,
        })
        self._db_write_state(force=True)

    def _handle_trade_exit(self, res):
        self.trade_history.append(res)
        self.daily_pnl += res['pnl_total']
        self.running_capital += res['pnl_total']
        self._log_trade_csv(res)

        e = "✅" if res['pnl_total'] > 0 else "❌"
        print()
        logger.info("="*60)
        logger.info(f"{e} TRADE CLOSED: {res['type']} on {res['symbol']}")
        logger.info(f"  Entry: ₹{res['entry_price']:.2f} → Exit: ₹{res['exit_price']:.2f} ({res['exit_reason']})")
        logger.info(f"  PnL: ₹{res['pnl_total']:+.2f} | Capital: ₹{self.running_capital:.2f}")
        logger.info("="*60)
        self.active_trade = None
        
        # DB: insert trade + event + forced state write
        if self.db:
            try:
                self.db.insert_trade(res, self.running_capital)
            except Exception:
                pass
        self._db_log_event('EXIT', f"Exited {res['type']} on {res['symbol']} ({res['exit_reason']}) PnL=₹{res['pnl_total']:+.2f}", {
            'symbol': res['symbol'], 'exit_reason': res['exit_reason'], 'pnl': res['pnl_total'],
        })
        self._db_write_state(force=True)

    def _handle_eod(self, now):
        # A5: One-shot flag
        if self._eod_done:
            return
        self._eod_done = True
        
        if self.active_trade:
            ltp = self.ce_ltp if self.active_trade.symbol == self.ce_symbol else self.pe_ltp
            res = self.active_trade.close_eod(ltp or self.active_trade.entry_price, now)
            if res: self._handle_trade_exit(res)
        
        # A4: Set stop event to cleanly stop main loop + refresh worker
        self._stop_event.set()
        print("\n✅ Market Closed. Daily PnL: ₹", self.daily_pnl)
        
        self._db_log_event('INFO', f"EOD — Daily PnL: ₹{self.daily_pnl:+.2f}, Capital: ₹{self.running_capital:.2f}")
        self._db_write_state(force=True)

    def _print_status(self, now):
        stts = f"[{now.strftime('%H:%M:%S')}] PnL: ₹{self.daily_pnl:+.2f} | Cap: ₹{self.running_capital:.2f}"
        if self.active_trade:
            t = self.active_trade
            stts += f" | TRADE {t.trade_type} SL {t.sl:.2f} TP {t.tp:.2f}"
        print(f"\r{stts:<100}", end='', flush=True)

    def _on_open(self):
        subs = [SPOT_SYMBOL]
        if self.ce_symbol: subs.append(self.ce_symbol)
        if self.pe_symbol: subs.append(self.pe_symbol)
        self.fyers_ws.subscribe(symbols=subs, data_type="SymbolUpdate")
        self.is_running = True  # A4: WS connected flag
        self.fyers_ws.keep_running()

    def _on_error(self, message): 
        logger.error(f"WS Err: {message}")
        
    def _on_close(self, message):
        # A4: Only log, don't set is_running=False or stop the loop.
        # reconnect=True in FyersDataSocket will restore the connection.
        logger.warning(f"WS closed: {message}")
        self.is_running = False  # Mark WS as disconnected (for heartbeat)

    def run(self):
        logger.info("🚀 LIVE 5-MIN TRADER | SL=max(Risk,Avg), TP=2.5x RR")

        # Initialize dashboard DB first (no auth needed). Resumes capital and
        # gives the dashboard a state row even before the market opens.
        self._init_db()

        # ---- Wait for MARKET DATA OPEN (09:15) BEFORE authenticating ----
        # We connect at 09:15 (NSE open) so candles from 09:15 onward are
        # captured — these feed avg_candle_size for the SL. Entries are still
        # gated at 09:30 (MARKET_OPEN) inside _on_message.
        # Authenticating just before open also keeps the Fyers token fresh
        # (a boot that authed hours earlier had a stale token by open).
        now = datetime.now(IST)
        if now.time() < MARKET_DATA_OPEN:
            tgt = now.replace(hour=MARKET_DATA_OPEN.hour, minute=MARKET_DATA_OPEN.minute,
                              second=0, microsecond=0)
            wait = (tgt - now).total_seconds()
            logger.info(f"⏳ Waiting {int(wait)}s until {MARKET_DATA_OPEN.strftime('%H:%M')} (NSE open) before authenticating...")
            self._stop_event.wait(timeout=max(0, wait - 30))

        if self._stop_event.is_set():
            return

        # If the process starts after market close, there's nothing to trade.
        if datetime.now(IST).time() > MARKET_CLOSE:
            logger.info("⏹️  Started after market close — nothing to do today.")
            self._eod_done = True
            self._db_write_state(force=True)
            return

        # ---- Authenticate (retry instead of exiting) ----
        while not self._stop_event.is_set():
            if self.authenticate():
                break
            logger.warning("⚠️ Auth failed — retrying in 30s...")
            self._db_log_event('INFO', 'Authentication failed, retrying in 30s')
            self._stop_event.wait(timeout=30)
        if self._stop_event.is_set():
            return

        # ---- Find tradable options (retry instead of exiting) ----
        ce = pe = None
        attempts = 0
        while not self._stop_event.is_set():
            ce, pe = self.find_options_in_range()
            if ce and pe:
                break
            attempts += 1
            logger.warning(f"⚠️ Could not find options (attempt {attempts}) — retrying in 20s...")
            if attempts == 1:
                self._db_log_event('INFO', 'Searching for tradable options...')
            # Re-auth periodically in case the token is the problem.
            if attempts % 5 == 0:
                logger.info("🔑 Re-authenticating before next option search...")
                self.authenticate()
            self._stop_event.wait(timeout=20)
        if self._stop_event.is_set():
            return
        self.ce_symbol, self.pe_symbol = ce, pe
        logger.info(f"📡 Trading {self.ce_symbol} / {self.pe_symbol}")
        self._db_write_state(force=True)

        self.fyers_ws = data_ws.FyersDataSocket(
            access_token=self.access_token, log_path="", litemode=False,
            write_to_file=False, reconnect=True,
            on_connect=self._on_open, on_close=self._on_close,
            on_error=self._on_error, on_message=self._on_message
        )
        self.fyers_ws.connect()
        self._refresh_thread = threading.Thread(target=self._option_refresh_worker, daemon=True)
        self._refresh_thread.start()
        
        try:
            # A4: Main loop checks _stop_event, not is_running.
            # Also checks clock to trigger EOD if ticks stop after 15:30.
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1)
                # Safety: if ticks stopped and we're past market close, trigger EOD
                check_now = datetime.now(IST)
                if check_now.time() > MARKET_CLOSE and not self._eod_done:
                    logger.info("⏰ Clock-based EOD trigger (no ticks received)")
                    self._handle_eod(check_now)
        except KeyboardInterrupt:
            self._handle_eod(datetime.now(IST))

if __name__ == "__main__":
    Live5MinEngine().run()
