"""
Live Forward Test: 5-Min Divergence Strategy
=============================================================
Uses WebSocket tick data for real-time paper trading.

Architecture (fixed-universe model — no mid-day rotation):
- At startup, pick the nearest weekly expiry and subscribe a FIXED universe of
  STRIKES_PER_SIDE OTM CE strikes (ATM and above) + STRIKES_PER_SIDE OTM PE
  strikes (ATM and below), plus spot. These are held all day — no rotation,
  no mid-bucket re-subscribe, no candle_history wipe. (WS cap is 5000 symbols.)
- Divergence is evaluated on EVERY subscribed strike against the spot candle.
- A candidate signal proceeds only if the option's signal-candle price is in
  [OPTION_PRICE_MIN, OPTION_PRICE_MAX]; ties broken by closeness to
  OPTION_PRICE_TARGET; one position at a time.

Strategy:
- Divergence Signal on 5-min candles:
  - PE Buy: Spot Green AND PE Green
  - CE Buy: Spot Red AND CE Green
- Case 1 Entry Only: Tick LTP > divergence candle's high BEFORE breaking low.
  - If ltp < sig_low -> signal invalid.
  - If next 5-min candle closes without triggering -> signal invalid.
- Signal integrity (History-confirm): the websocket candle only DETECTS a
  candidate; before arming we confirm that bucket's OHLC via the Fyers History
  API (authoritative) and use the History high/low for the entry trigger. This
  prevents phantom signals from sparse/missed option ticks.
- Dynamic Risk Management:
  - avg_candle_size = average high-low of History candles 09:15..signal candle
  - orig_risk = entry - sig_low
  - final_risk = max(orig_risk, avg_candle_size)
  - SL = max(Entry - final_risk, 0.05)  [clamped to prevent negative]
  - TP = Entry + (2.5 * final_risk)  [recomputed from clamped risk]
- Capital Compounding: Starts at ₹20,000 | Lot Size: 65

Charts: live tick candles are NOT persisted. When a trade executes, the traded
contract + spot day candles are backfilled from the History API into the
dashboard DB so the trade-detail modal is always faithful and gap-free.

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
# Fixed-universe model: subscribe this many OTM strikes per side (CE above ATM,
# PE below ATM) once at startup and hold them all day. No mid-day rotation.
STRIKES_PER_SIDE = 30
# Safety net: confirm a signal candle's OHLC against the Fyers History API before
# arming the pending signal (and use the History high/low for the entry trigger).
HISTORY_CONFIRM_SIGNALS = True
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
            # Full signal-candle OHLC for audit (the History-confirmed candle)
            'signal_open': self.signal_candle.get('open'),
            'signal_close': self.signal_candle.get('close'),
        }

class Live5MinEngine:
    def __init__(self):
        self.fyers = None
        self.fyers_ws = None
        self.access_token = None
        self.client_id = None
        self.is_running = False  # WS connected flag only (A4)

        # Fixed-universe model: lists of subscribed CE/PE option symbols (held all
        # day) + per-symbol last traded price. No single "current" CE/PE anymore.
        self.ce_symbols = []
        self.pe_symbols = []
        self.option_symbols = set()
        self.ltps = {}          # symbol -> last traded price (spot + all options)
        self.expiry = None      # nearest-expiry epoch string used for the universe
        self.spot_ltp = None

        self._symbol_lock = threading.Lock()
        # A4: Stop event for clean shutdown — main loop checks this
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
                    'ltp': self.ltps.get(t.symbol),
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
                # Fixed-universe model: there is no single CE/PE. Report the
                # universe size + expiry so the dashboard header can show context.
                'universe_count': len(self.option_symbols),
                'ce_count': len(self.ce_symbols),
                'pe_count': len(self.pe_symbols),
                'expiry': self.expiry,
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

    def build_option_universe(self):
        """Build the FIXED daily option universe (no rotation).

        Picks the nearest weekly expiry, then selects STRIKES_PER_SIDE OTM CE
        strikes (ATM and above) and STRIKES_PER_SIDE OTM PE strikes (ATM and
        below). These are subscribed once at startup and held all day.

        Returns (ce_symbols, pe_symbols) as lists, or (None, None) on failure.
        """
        try:
            time.sleep(API_DELAY)
            data = {"symbol": SPOT_SYMBOL, "strikecount": 5, "timestamp": ""}
            resp = self.fyers.optionchain(data=data)
            if not isinstance(resp, dict) or resp.get('code') != 200:
                return None, None
            nearest_expiry = str(resp['data']['expiryData'][0]['expiry'])

            time.sleep(API_DELAY)
            # strikecount max is 50; 30 returns 30 ITM + 30 OTM per side.
            data = {"symbol": SPOT_SYMBOL, "strikecount": 50, "timestamp": nearest_expiry}
            resp = self.fyers.optionchain(data=data)
            if not isinstance(resp, dict) or resp.get('code') != 200:
                return None, None
            options = resp['data']['optionsChain']

            # Underlying spot: the chain includes an entry with strike_price == -1
            # whose ltp is the index price. Fall back to live spot_ltp if needed.
            spot = None
            for o in options:
                if o.get('strike_price') in (-1, None) and o.get('option_type') in ('', None):
                    spot = o.get('ltp')
                    break
            if not spot:
                spot = self.spot_ltp
            if not spot:
                logger.warning("build_option_universe: could not determine spot/ATM")
                return None, None

            atm = round(spot / STRIKE_STEP) * STRIKE_STEP

            ce_opts = [o for o in options
                       if o.get('option_type') == 'CE' and o.get('strike_price', 0) >= atm]
            pe_opts = [o for o in options
                       if o.get('option_type') == 'PE' and o.get('strike_price', 0) <= atm and o.get('strike_price', 0) > 0]

            # CE: ATM and OTM-above (ascending). PE: ATM and OTM-below (descending).
            ce_opts.sort(key=lambda o: o['strike_price'])
            pe_opts.sort(key=lambda o: o['strike_price'], reverse=True)

            ce_syms = [o['symbol'] for o in ce_opts[:STRIKES_PER_SIDE]]
            pe_syms = [o['symbol'] for o in pe_opts[:STRIKES_PER_SIDE]]

            if not ce_syms or not pe_syms:
                logger.warning("build_option_universe: empty CE/PE list after filtering")
                return None, None

            self.expiry = nearest_expiry
            logger.info(f"  ATM≈{atm} | CE strikes={len(ce_syms)} PE strikes={len(pe_syms)} | expiry={nearest_expiry}")
            return ce_syms, pe_syms
        except Exception as e:
            logger.error(f"Error building option universe: {e}")
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

    def _confirm_and_arm(self, symbol, side, signal_candle_time, reason, tick_candle):
        """Confirm a candidate signal against the History API, then arm it.

        Runs synchronously on the WS thread (only when an in-range divergence
        candidate exists — a few times a day at most). Does ONE History call that
        both (a) confirms the signal candle's true color and (b) computes the
        avg candle size for SL/TP. The authoritative History high/low becomes the
        entry trigger, so phantom signals from sparse option ticks can't fire.
        """
        avg, candles = self._compute_avg_candle_size(symbol, signal_candle_time)

        sig_ts = signal_candle_time.timestamp()
        hist_sig = next((c for c in candles if abs(c['time'].timestamp() - sig_ts) < 1), None)

        candle = tick_candle  # fallback
        if HISTORY_CONFIRM_SIGNALS:
            if hist_sig is not None:
                option_green = hist_sig['close'] > hist_sig['open']
                if not option_green:
                    logger.info(f"  🚫 Signal REJECTED by History (option not green): {symbol} @ {signal_candle_time.strftime('%H:%M')} "
                                f"O={hist_sig['open']:.2f} C={hist_sig['close']:.2f}")
                    self._db_log_event('SIGNAL_REJECTED', f"{side} rejected (History candle not green): {symbol}", {
                        'symbol': symbol, 'type': side,
                        'hist_open': hist_sig['open'], 'hist_close': hist_sig['close'],
                    })
                    return
                candle = hist_sig
            else:
                logger.warning(f"  ⚠️ History unavailable to confirm {symbol} @ {signal_candle_time.strftime('%H:%M')} "
                               f"— arming on websocket candle (degraded)")
        elif hist_sig is not None:
            candle = hist_sig

        # Don't arm if a trade opened while we were confirming.
        if self.active_trade is not None:
            return

        self.daily_signals += 1
        self.pending_signals[symbol] = {
            'type': side, 'high': candle['high'], 'low': candle['low'],
            'candle': candle, 'reason': reason,
            # Entry valid only during the immediately-next 5-min candle
            # ([T+300, T+600)). signal_candle_time is the bucket START (T).
            'expires_at': sig_ts + 600,
            'avg_candle_size': avg,
        }
        avg_disp = f"{avg:.2f}" if avg is not None else "pending"
        print()
        logger.info(f"🎯 {side} ARMED: {symbol} | High: {candle['high']:.2f} | avg={avg_disp}")
        self._db_log_event('SIGNAL', f"{side} signal: {symbol} high={candle['high']:.2f}", {
            'symbol': symbol, 'type': side, 'high': candle['high'], 'low': candle['low'],
        })

    def _backfill_trade_charts(self, symbol, entry_time, exit_time):
        """After a trade EXECUTES, backfill the traded option + spot day candles
        from the History API into the dashboard DB so the trade-detail modal is
        faithful and gap-free. Runs in a background thread; never blocks the WS.
        """
        if not self.db:
            return
        try:
            day_open = entry_time.replace(hour=MARKET_DATA_OPEN.hour,
                                          minute=MARKET_DATA_OPEN.minute,
                                          second=0, microsecond=0)
            range_from = day_open.timestamp()
            # 15 min past exit to give the chart some right-side context.
            range_to = exit_time.timestamp() + 900
            for sym in (symbol, SPOT_SYMBOL):
                candles = self._fetch_history_candles(sym, range_from, range_to)
                for c in candles:
                    self._db_write_candle(sym, c, is_final=True)
            logger.info(f"  📊 Backfilled chart candles for {symbol} + spot")
        except Exception as e:
            logger.error(f"Chart backfill error for {symbol}: {e}")

    def _store_candle(self, symbol, candle):
        # Guard candle_history with the lock (kept for thread-safety even though
        # rotation is gone; backfill threads never touch candle_history).
        with self._symbol_lock:
            if symbol not in self.candle_history:
                self.candle_history[symbol] = []
            self.candle_history[symbol].append(candle)
            # NOT POPPING - keep all candles for accurate avg candle size computation!
        # NOTE: live tick candles are intentionally NOT persisted to the dashboard
        # DB anymore. Charts are backfilled from History only when a trade executes.

    # ---------- Divergence across the full fixed universe ----------
    def check_divergence(self, completed_symbol, timestamp):
        """Evaluate divergence across EVERY subscribed strike on candle completion.

        - Spot GREEN → look for any PE strike whose latest candle is GREEN (PE_BUY).
        - Spot RED   → look for any CE strike whose latest candle is GREEN (CE_BUY).
        Only strikes whose signal-candle close is in [OPTION_PRICE_MIN,
        OPTION_PRICE_MAX] qualify; ties broken by closeness to OPTION_PRICE_TARGET.
        At most ONE signal is armed per spot bucket (single-position strategy),
        and it is History-confirmed before arming.
        """
        # Single-trade rule: while a trade is open, ignore new setups entirely.
        if self.active_trade is not None:
            return

        # Snapshot candle_history under the lock.
        with self._symbol_lock:
            spot_candles = list(self.candle_history.get(SPOT_SYMBOL, []))
            opt_latest = {}
            for sym in self.ce_symbols + self.pe_symbols:
                lst = self.candle_history.get(sym)
                if lst:
                    opt_latest[sym] = lst[-1]

        if not spot_candles:
            return
        spot_candle = spot_candles[-1]
        bucket = spot_candle['time']

        # One signal per bucket (dedupe by bucket time across all strikes).
        if bucket in self.last_signal_bucket:
            return

        spot_green = spot_candle['close'] > spot_candle['open']
        spot_red = spot_candle['close'] < spot_candle['open']
        if not (spot_green or spot_red):
            return  # doji spot → no divergence either side

        if spot_green:
            side, reason, syms = 'PE_BUY', 'Spot GREEN + PE GREEN', self.pe_symbols
        else:
            side, reason, syms = 'CE_BUY', 'Spot RED + CE GREEN', self.ce_symbols

        # Collect in-range green-option candidates for this bucket.
        candidates = []  # (distance_to_target, symbol, option_candle)
        for sym in syms:
            oc = opt_latest.get(sym)
            if not oc or oc['time'] != bucket:
                continue
            if not (oc['close'] > oc['open']):
                continue  # option must be green
            if not (OPTION_PRICE_MIN <= oc['close'] <= OPTION_PRICE_MAX):
                continue  # price-range filter
            candidates.append((abs(oc['close'] - OPTION_PRICE_TARGET), sym, oc))

        if not candidates:
            return

        # Pick the candidate closest to the target price (~₹75).
        candidates.sort(key=lambda x: x[0])
        _, sym, oc = candidates[0]

        # Mark the bucket as handled so we do at most one confirm call per bucket
        # (even if the best candidate is rejected by History).
        self.last_signal_bucket[bucket] = True
        self._confirm_and_arm(sym, side, bucket, reason, oc)


    def _on_message(self, message):
        try:
            if not isinstance(message, dict) or 'ltp' not in message: return
            if 'symbol' not in message: return
            
            # A8: Use explicit now for deterministic behavior
            symbol, ltp, now = message['symbol'], message['ltp'], datetime.now(IST)

            if symbol == SPOT_SYMBOL:
                self.spot_ltp = ltp
            elif symbol not in self.option_symbols:
                return
            self.ltps[symbol] = ltp

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
                # Check divergence whenever ANY symbol's candle completes.
                self.check_divergence(symbol, now)

            # DB: write engine state (heartbeat) only — live tick candles are no
            # longer persisted (charts are backfilled from History on trade exec).
            if self.db:
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

        # Use the accurate History-based avg computed at signal-confirm time.
        # If History was unavailable then (rare), avg is None → ActiveTrade enters
        # with the SAFE SL = signal_low and the avg simply isn't applied.
        avg_sz = sig.get('avg_candle_size')
        self.active_trade = ActiveTrade5Min(symbol, sig['type'], ltp, ts, sig['candle'], sig['reason'], avg_sz, lots)

        # Single-trade rule: drop any other armed signals (preference to this trade).
        for other in list(self.pending_signals.keys()):
            if other != symbol:
                del self.pending_signals[other]

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
            # Executed trades only: backfill the traded option + spot day candles
            # from History so the trade-detail modal is faithful and gap-free.
            threading.Thread(
                target=self._backfill_trade_charts,
                args=(res['symbol'], res['entry_time'], res['exit_time']),
                daemon=True
            ).start()
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
            ltp = self.ltps.get(self.active_trade.symbol)
            res = self.active_trade.close_eod(ltp or self.active_trade.entry_price, now)
            if res: self._handle_trade_exit(res)

        # A4: Set stop event to cleanly stop main loop
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
        subs = [SPOT_SYMBOL] + list(self.option_symbols)
        # Fyers allows up to 5000 symbols per connection; we use ~61.
        self.fyers_ws.subscribe(symbols=subs, data_type="SymbolUpdate")
        logger.info(f"📡 Subscribed {len(subs)} symbols (spot + {len(self.option_symbols)} options)")
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

        # ---- Build the fixed option universe (retry instead of exiting) ----
        ce_syms = pe_syms = None
        attempts = 0
        while not self._stop_event.is_set():
            ce_syms, pe_syms = self.build_option_universe()
            if ce_syms and pe_syms:
                break
            attempts += 1
            logger.warning(f"⚠️ Could not build option universe (attempt {attempts}) — retrying in 20s...")
            if attempts == 1:
                self._db_log_event('INFO', 'Building option universe...')
            # Re-auth periodically in case the token is the problem.
            if attempts % 5 == 0:
                logger.info("🔑 Re-authenticating before next universe build...")
                self.authenticate()
            self._stop_event.wait(timeout=20)
        if self._stop_event.is_set():
            return
        self.ce_symbols, self.pe_symbols = ce_syms, pe_syms
        self.option_symbols = set(ce_syms) | set(pe_syms)
        logger.info(f"📡 Universe: {len(self.ce_symbols)} CE + {len(self.pe_symbols)} PE strikes")
        self._db_write_state(force=True)

        self.fyers_ws = data_ws.FyersDataSocket(
            access_token=self.access_token, log_path="", litemode=False,
            write_to_file=False, reconnect=True,
            on_connect=self._on_open, on_close=self._on_close,
            on_error=self._on_error, on_message=self._on_message
        )
        self.fyers_ws.connect()

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
