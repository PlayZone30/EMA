"""
Live 5-Min Divergence Data Collector
=====================================
Connects to Fyers WebSocket, builds 5-min candles from ticks, detects
divergence signals, classifies them, and records tick-level post-breakout
movement for analysis.

Signal Classification:
  Case 1 (VALID)  : Next candle breaks HIGH first
  Case 2 (VALID)  : Next candle breaks LOW first, THEN breaks HIGH (same candle)
  Case 3 (VALID)  : Next candle no break; 2nd candle breaks HIGH
  Case 4 (INVALID): Next candle breaks LOW first, never recovers to HIGH

Post-Breakout Tracking (tick-by-tick, within the breakout candle only):
  - Entry tick  = first tick that crosses above signal_high
  - Upside  (₹) = max tick after entry - signal_high
  - Downside(₹) = signal_high - min tick after entry

Options: 5-min candles, ₹70-80 range (OTM, same logic as live_divergence_1min.py)

Output:
  - Console: real-time status
  - divergence_5min_observations.csv  (one row per signal)
  - live_5min_collector.log

Usage: python live_5min_collector.py
"""

import os
import csv
import json
import time
import pytz
import logging
import threading
import dotenv
from datetime import datetime, time as dt_time
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

dotenv.load_dotenv()

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("live_5min_collector.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("5MinCollector")

IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
SPOT_SYMBOL          = "NSE:NIFTY50-INDEX"
RESOLUTION_SECS      = 300          # 5-minute candles (300 seconds)
OPTION_PRICE_MIN     = 70.0
OPTION_PRICE_MAX     = 80.0
OPTION_PRICE_TARGET  = 75.0
STRIKE_STEP          = 50
OTM_MIN_PTS          = 100
OTM_MAX_PTS          = 800
API_DELAY            = 0.6
OPTION_REFRESH_SECS  = 600          # Re-check options every 10 min
MARKET_OPEN          = dt_time(9, 15)
MARKET_CLOSE         = dt_time(15, 30)
OBS_CSV              = "divergence_5min_observations.csv"


# ─────────────────────────────────────────────
# 5-Min Candle Builder (from ticks)
# ─────────────────────────────────────────────
class CandleManager5Min:
    """Builds 5-minute OHLC candles from tick data."""

    def __init__(self):
        self._candles = {}  # symbol → current partial candle dict

    def _bucket(self):
        now = datetime.now(IST)
        # Round down to nearest 5-min boundary
        minutes = (now.hour * 60 + now.minute) // 5 * 5
        return now.replace(hour=minutes // 60, minute=minutes % 60,
                           second=0, microsecond=0)

    def update(self, symbol, ltp):
        """
        Feed a tick. Returns completed candle dict on candle close, else None.
        Dict: {time, open, high, low, close}
        """
        bucket = self._bucket()
        cur    = self._candles.get(symbol)

        if cur is None or cur["bucket"] != bucket:
            completed = None
            if cur is not None:
                completed = {
                    "time":  cur["bucket"],
                    "open":  cur["open"],
                    "high":  cur["high"],
                    "low":   cur["low"],
                    "close": cur["close"],
                }
            self._candles[symbol] = {
                "bucket": bucket,
                "open":   ltp,
                "high":   ltp,
                "low":    ltp,
                "close":  ltp,
            }
            return completed
        else:
            cur["high"]  = max(cur["high"],  ltp)
            cur["low"]   = min(cur["low"],   ltp)
            cur["close"] = ltp
            return None


# ─────────────────────────────────────────────
# Signal State Machine
# ─────────────────────────────────────────────
class SignalState:
    """
    Tracks a single pending divergence signal through its lifecycle.
    States:
      WATCHING   → waiting for breakout (up to 2 candles)
      TRIGGERED  → breakout tick seen, collecting tick stats within breakout candle
      CLOSED     → result logged
    """
    WATCHING  = "WATCHING"
    TRIGGERED = "TRIGGERED"
    CLOSED    = "CLOSED"

    def __init__(self, signal_type, option, signal_candle, signal_time,
                 spot_candle, signal_seq):
        self.signal_type    = signal_type      # 'CE_BUY' or 'PE_BUY'
        self.option         = option           # 'CE' or 'PE'
        self.signal_candle  = signal_candle    # {time,open,high,low,close}
        self.signal_time    = signal_time
        self.spot_candle    = spot_candle
        self.signal_seq     = signal_seq       # sequential ID

        # Derived from signal candle
        self.sig_high = signal_candle["high"]
        self.sig_low  = signal_candle["low"]

        # Case tracking
        self.candles_seen      = 0             # how many completed candles since signal
        self.low_broken_first  = False         # did the next candle break low before high?

        # Breakout tracking
        self.state             = self.WATCHING
        self.breakout_candle_bucket = None     # which 5-min bucket is the breakout candle
        self.entry_tick        = None          # first tick > sig_high
        self.entry_time        = None
        self.max_tick_after    = None          # highest tick after entry within brkout candle
        self.min_tick_after    = None          # lowest tick after entry within brkout candle
        self.breakout_case     = None          # 1/2/3/4
        self.result_logged     = False

    def tick(self, ltp, timestamp, current_bucket):
        """
        Feed a tick. Returns final result dict when ready to log, else None.
        """
        if self.state == self.CLOSED:
            return None

        if self.state == self.WATCHING:

            # Low broke (checking BEFORE high, order matters tick by tick)
            if ltp < self.sig_low and not self.low_broken_first:
                self.low_broken_first = True
                logger.debug(f"  Signal#{self.signal_seq}: Low broken @ ₹{ltp:.2f}")

            # High broke → TRIGGER
            if ltp > self.sig_high:
                # Determine case
                if self.candles_seen == 0:
                    # Still inside first next candle
                    case = 2 if self.low_broken_first else 1
                elif self.candles_seen == 1 and not self.low_broken_first:
                    case = 3
                else:
                    # Shouldn't get here if invalidated correctly
                    case = 3

                logger.info(
                    f"  Signal#{self.signal_seq} 🚀 BREAKOUT — Case {case} | "
                    f"Entry ₹{ltp:.2f} | SigHigh ₹{self.sig_high:.2f}"
                )
                self.state               = self.TRIGGERED
                self.breakout_case       = case
                self.entry_tick          = ltp
                self.entry_time          = timestamp
                self.breakout_candle_bucket = current_bucket
                self.max_tick_after      = ltp
                self.min_tick_after      = ltp
                return None

            return None

        if self.state == self.TRIGGERED:
            # Collect ticks WITHIN the same 5-min breakout candle
            if current_bucket == self.breakout_candle_bucket:
                self.max_tick_after = max(self.max_tick_after, ltp)
                self.min_tick_after = min(self.min_tick_after, ltp)
                return None
            else:
                # Breakout candle closed → compute and return result
                return self._finalize(timestamp)

        return None

    def invalidate(self, reason):
        """Mark signal as invalid (Case 4 or expired)."""
        self.breakout_case = 4 if "low_before_high" in reason else None
        self.state = self.CLOSED

    def on_new_candle(self, candle, current_bucket):
        """
        Called each time a new 5-min candle completes for this option.
        Returns final result if breakout candle just closed, else None.
        Increments candles_seen.
        """
        if self.state == self.TRIGGERED:
            # The new candle means the breakout candle just closed
            return self._finalize(candle["time"])

        if self.state == self.WATCHING:
            self.candles_seen += 1

            if self.candles_seen == 1 and self.low_broken_first:
                # Case 4: next candle broke low and now closes without breaking high
                logger.info(
                    f"  Signal#{self.signal_seq} ❌ INVALID (Case 4) — "
                    f"broke low, candle closed without breaking high"
                )
                self.invalidate("low_before_high_and_closed")
                return self._build_invalid_result()

            if self.candles_seen > 2:
                # Exceeded 2-candle window
                logger.info(f"  Signal#{self.signal_seq} ❌ EXPIRED (no breakout in 2 candles)")
                self.invalidate("expired")
                return self._build_invalid_result()

        return None

    def _finalize(self, close_time):
        """Compute upside/downside and return result dict."""
        self.state = self.CLOSED
        upside   = self.max_tick_after - self.sig_high
        downside = self.sig_high - self.min_tick_after
        result = {
            "signal_seq":         self.signal_seq,
            "signal_type":        self.signal_type,
            "option":             self.option,
            "signal_time":        self.signal_time,
            "sig_high":           self.sig_high,
            "sig_low":            self.sig_low,
            "sig_open":           self.signal_candle["open"],
            "sig_close":          self.signal_candle["close"],
            "spot_open":          self.spot_candle["open"],
            "spot_close":         self.spot_candle["close"],
            "case":               self.breakout_case,
            "case_desc":          self._case_desc(),
            "validity":           "VALID",
            "entry_tick":         self.entry_tick,
            "entry_time":         self.entry_time,
            "max_after_break":    self.max_tick_after,
            "min_after_break":    self.min_tick_after,
            "upside_rs":          round(upside, 2),
            "downside_rs":        round(downside, 2),
        }
        return result

    def _build_invalid_result(self):
        """Return an INVALID result row for logging."""
        self.state = self.CLOSED
        return {
            "signal_seq":    self.signal_seq,
            "signal_type":   self.signal_type,
            "option":        self.option,
            "signal_time":   self.signal_time,
            "sig_high":      self.sig_high,
            "sig_low":       self.sig_low,
            "sig_open":      self.signal_candle["open"],
            "sig_close":     self.signal_candle["close"],
            "spot_open":     self.spot_candle["open"],
            "spot_close":    self.spot_candle["close"],
            "case":          self.breakout_case,
            "case_desc":     self._case_desc(),
            "validity":      "INVALID",
            "entry_tick":    None,
            "entry_time":    None,
            "max_after_break": None,
            "min_after_break": None,
            "upside_rs":     None,
            "downside_rs":   None,
        }

    def _case_desc(self):
        descs = {
            1: "Next candle: HIGH first",
            2: "Next candle: LOW then HIGH (same candle)",
            3: "2nd candle: HIGH break",
            4: "Next candle: LOW break, HIGH never reached (INVALID)",
            None: "Expired/Unknown",
        }
        return descs.get(self.breakout_case, "Unknown")


# ─────────────────────────────────────────────
# Main Engine
# ─────────────────────────────────────────────
class LiveDataCollector:

    def __init__(self):
        self.fyers         = None
        self.fyers_ws      = None
        self.access_token  = None
        self.client_id     = None
        self.is_running    = False

        self.ce_symbol     = None
        self.pe_symbol     = None
        self.spot_ltp      = None
        self.ce_ltp        = None
        self.pe_ltp        = None

        self._symbol_lock        = threading.Lock()
        self._refresh_thread     = None
        self.last_option_refresh = 0

        self.candle_mgr    = CandleManager5Min()
        self.candle_hist   = {}        # {symbol: [last 3 candles]}

        # Signal tracking
        self.active_signals   = []    # List[SignalState] — currently watching
        self.signal_counter   = 0
        self.total_observed   = 0
        self.valid_count      = 0
        self.invalid_count    = 0

        self._init_csv()

    # ── CSV Init ──────────────────────────────
    def _init_csv(self):
        if os.path.exists(OBS_CSV):
            return
        with open(OBS_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "signal_seq", "signal_type", "option",
                "signal_time", "sig_high", "sig_low", "sig_open", "sig_close",
                "spot_open", "spot_close",
                "case", "case_desc", "validity",
                "entry_tick", "entry_time",
                "max_after_break", "min_after_break",
                "upside_rs", "downside_rs",
            ])
            writer.writeheader()

    def _log_result(self, result):
        with open(OBS_CSV, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=result.keys())
            writer.writerow(result)

        validity = result["validity"]
        case     = result["case"]
        if validity == "VALID":
            self.valid_count += 1
            logger.info(
                f"  📝 Signal#{result['signal_seq']} LOGGED — "
                f"Case {case} | Upside ₹{result['upside_rs']} | "
                f"Downside ₹{result['downside_rs']} | "
                f"Entry ₹{result['entry_tick']}"
            )
        else:
            self.invalid_count += 1
            logger.info(
                f"  📝 Signal#{result['signal_seq']} LOGGED — "
                f"INVALID (Case {case})"
            )

    # ── Auth ──────────────────────────────────
    def authenticate(self):
        from main import FyersAuthenticator
        cid  = os.getenv("CLIENT_ID")
        sk   = os.getenv("SECRET_KEY")
        user = os.getenv("USERNAME")
        pin  = os.getenv("PIN")
        totp = os.getenv("TOTP_KEY")
        if not all([cid, sk, user, pin, totp]):
            logger.error("Missing .env variables!")
            return False
        auth = FyersAuthenticator(cid, sk, "https://www.google.com",
                                  user, pin, totp)
        token, err = auth.get_access_token()
        if not token:
            logger.error(f"Auth failed: {err}")
            return False
        self.access_token = token
        self.client_id    = cid
        self.fyers        = fyersModel.FyersModel(
            client_id=cid, token=token, log_path=""
        )
        logger.info("✅ Authenticated!")
        return True

    # ── Option Selection (70-80 range) ────────
    def find_options_in_range(self):
        try:
            time.sleep(API_DELAY)
            resp = self.fyers.optionchain(
                {"symbol": SPOT_SYMBOL, "strikecount": 1, "timestamp": ""}
            )
            if resp.get("code") != 200:
                return None, None
            expiry_ts = str(resp["data"]["expiryData"][0]["expiry"])

            # Spot price from LTP
            spot = self.spot_ltp or 24000
            rounded_spot = round(spot / STRIKE_STEP) * STRIKE_STEP

            time.sleep(API_DELAY)
            resp = self.fyers.optionchain(
                {"symbol": SPOT_SYMBOL, "strikecount": 20, "timestamp": expiry_ts}
            )
            if resp.get("code") != 200:
                return None, None

            chain = resp["data"]["optionsChain"]

            ce_cands = {
                o["symbol"]: o.get("ltp", 0)
                for o in chain
                if o.get("option_type") == "CE"
                and OPTION_PRICE_MIN <= o.get("ltp", 0) <= OPTION_PRICE_MAX
            }
            pe_cands = {
                o["symbol"]: o.get("ltp", 0)
                for o in chain
                if o.get("option_type") == "PE"
                and OPTION_PRICE_MIN <= o.get("ltp", 0) <= OPTION_PRICE_MAX
            }

            # Fallback to 60-90 range
            if not ce_cands or not pe_cands:
                ce_cands = {
                    o["symbol"]: o.get("ltp", 0)
                    for o in chain
                    if o.get("option_type") == "CE"
                    and 60 <= o.get("ltp", 0) <= 90
                }
                pe_cands = {
                    o["symbol"]: o.get("ltp", 0)
                    for o in chain
                    if o.get("option_type") == "PE"
                    and 60 <= o.get("ltp", 0) <= 90
                }

            if not ce_cands or not pe_cands:
                return None, None

            best_ce = min(ce_cands, key=lambda s: abs(ce_cands[s] - OPTION_PRICE_TARGET))
            best_pe = min(pe_cands, key=lambda s: abs(pe_cands[s] - OPTION_PRICE_TARGET))

            logger.info(f"  CE: {best_ce} @ ₹{ce_cands[best_ce]:.2f}")
            logger.info(f"  PE: {best_pe} @ ₹{pe_cands[best_pe]:.2f}")
            return best_ce, best_pe

        except Exception as e:
            logger.error(f"Option selection error: {e}")
            return None, None

    # ── Background Option Refresh ──────────────
    def _option_refresh_worker(self):
        logger.info("🔄 Option refresh thread started (every 10 min)")
        while self.is_running:
            time.sleep(OPTION_REFRESH_SECS)
            if not self.is_running:
                break

            # Skip if there are active signals being tracked
            if self.active_signals:
                logger.info("🔄 Refresh skipped — active signals being tracked")
                continue

            try:
                logger.info("🔄 Refreshing options...")
                new_ce, new_pe = self.find_options_in_range()
                if not new_ce or not new_pe:
                    continue

                with self._symbol_lock:
                    ce_changed = new_ce != self.ce_symbol
                    pe_changed = new_pe != self.pe_symbol
                    if not ce_changed and not pe_changed:
                        logger.info("🔄 Options unchanged.")
                        continue

                    unsub, sub = [], []
                    if ce_changed:
                        if self.ce_symbol: unsub.append(self.ce_symbol)
                        sub.append(new_ce)
                        self.ce_symbol = new_ce
                        self.ce_ltp    = None
                        logger.info(f"  CE → {new_ce}")
                    if pe_changed:
                        if self.pe_symbol: unsub.append(self.pe_symbol)
                        sub.append(new_pe)
                        self.pe_symbol = new_pe
                        self.pe_ltp    = None
                        logger.info(f"  PE → {new_pe}")

                    # Reset candle history for changed symbols
                    self.candle_hist = {
                        k: v for k, v in self.candle_hist.items()
                        if k == SPOT_SYMBOL
                    }

                if self.fyers_ws:
                    if unsub:
                        self.fyers_ws.unsubscribe(symbols=unsub)
                        time.sleep(0.3)
                    if sub:
                        self.fyers_ws.subscribe(symbols=sub, data_type="SymbolUpdate")
                        logger.info(f"  📡 Subscribed: {sub}")

            except Exception as e:
                logger.error(f"Refresh error: {e}")

    # ── Candle History Helper ──────────────────
    def _store_candle(self, symbol, candle):
        if symbol not in self.candle_hist:
            self.candle_hist[symbol] = []
        self.candle_hist[symbol].append(candle)
        if len(self.candle_hist[symbol]) > 5:
            self.candle_hist[symbol].pop(0)

    def _latest_candle(self, symbol):
        hist = self.candle_hist.get(symbol, [])
        return hist[-1] if hist else None

    # ── Divergence Detection ──────────────────
    def _detect_signals(self, timestamp):
        """Check for new divergence signals on completed 5-min candles."""
        spot_c = self._latest_candle(SPOT_SYMBOL)
        if not spot_c:
            return

        with self._symbol_lock:
            ce_sym = self.ce_symbol
            pe_sym = self.pe_symbol

        # PE BUY: Spot Green + PE Green
        if pe_sym:
            pe_c = self._latest_candle(pe_sym)
            if pe_c and pe_c["time"] == spot_c["time"]:
                if spot_c["close"] > spot_c["open"] and pe_c["close"] > pe_c["open"]:
                    self.signal_counter += 1
                    self.total_observed += 1
                    sig = SignalState(
                        signal_type="PE_BUY",
                        option="PE",
                        signal_candle=pe_c,
                        signal_time=timestamp,
                        spot_candle=spot_c,
                        signal_seq=self.signal_counter,
                    )
                    self.active_signals.append(sig)
                    logger.info(
                        f"\n🎯 Signal#{self.signal_counter} PE_BUY detected | "
                        f"SigHigh ₹{pe_c['high']:.2f} SigLow ₹{pe_c['low']:.2f} | "
                        f"Spot O:{spot_c['open']:.2f} C:{spot_c['close']:.2f}"
                    )

        # CE BUY: Spot Red + CE Green
        if ce_sym:
            ce_c = self._latest_candle(ce_sym)
            if ce_c and ce_c["time"] == spot_c["time"]:
                if spot_c["close"] < spot_c["open"] and ce_c["close"] > ce_c["open"]:
                    self.signal_counter += 1
                    self.total_observed += 1
                    sig = SignalState(
                        signal_type="CE_BUY",
                        option="CE",
                        signal_candle=ce_c,
                        signal_time=timestamp,
                        spot_candle=spot_c,
                        signal_seq=self.signal_counter,
                    )
                    self.active_signals.append(sig)
                    logger.info(
                        f"\n🎯 Signal#{self.signal_counter} CE_BUY detected | "
                        f"SigHigh ₹{ce_c['high']:.2f} SigLow ₹{ce_c['low']:.2f} | "
                        f"Spot O:{spot_c['open']:.2f} C:{spot_c['close']:.2f}"
                    )

    # ── WebSocket Handlers ─────────────────────
    def _on_message(self, message):
        try:
            if not isinstance(message, dict) or "ltp" not in message:
                return
            symbol = message["symbol"]
            ltp    = message["ltp"]
            now    = datetime.now(IST)

            with self._symbol_lock:
                cur_ce = self.ce_symbol
                cur_pe = self.pe_symbol

            # Track LTPs
            if symbol == SPOT_SYMBOL:
                self.spot_ltp = ltp
            elif symbol == cur_ce:
                self.ce_ltp = ltp
            elif symbol == cur_pe:
                self.pe_ltp = ltp
            else:
                return

            # Market close check
            if now.time() > MARKET_CLOSE:
                self._eod(now)
                return

            # ── Active Signal Tick Feed ───────
            current_bucket = self.candle_mgr._bucket()
            completed_by_tick = []
            for sig in self.active_signals:
                # Only feed ticks for the relevant option symbol
                if (sig.option == "CE" and symbol == cur_ce) or \
                   (sig.option == "PE" and symbol == cur_pe):
                    result = sig.tick(ltp, now, current_bucket)
                    if result:
                        completed_by_tick.append((sig, result))

            for sig, result in completed_by_tick:
                self._log_result(result)
                self.active_signals.remove(sig)

            # ── Candle Building ───────────────
            completed = self.candle_mgr.update(symbol, ltp)
            if completed:
                self._store_candle(symbol, completed)

                # Feed candle close event to all active signals for this option
                closed_by_candle = []
                for sig in self.active_signals:
                    if (sig.option == "CE" and symbol == cur_ce) or \
                       (sig.option == "PE" and symbol == cur_pe):
                        result = sig.on_new_candle(completed, current_bucket)
                        if result:
                            closed_by_candle.append((sig, result))

                for sig, result in closed_by_candle:
                    self._log_result(result)
                    if sig in self.active_signals:
                        self.active_signals.remove(sig)

                # New signal detection only on spot candle close
                if symbol == SPOT_SYMBOL:
                    self._detect_signals(now)

            # ── Status Line ───────────────────
            self._status(now)

        except Exception as e:
            logger.error(f"Tick error: {e}", exc_info=True)

    def _on_open(self):
        subs = [SPOT_SYMBOL]
        with self._symbol_lock:
            if self.ce_symbol: subs.append(self.ce_symbol)
            if self.pe_symbol: subs.append(self.pe_symbol)
        logger.info(f"📡 WS Connected! Subscribing: {subs}")
        self.fyers_ws.subscribe(symbols=subs, data_type="SymbolUpdate")
        self.is_running = True
        self.fyers_ws.keep_running()

    def _on_close(self, msg):
        logger.info(f"WS Closed: {msg}")
        self.is_running = False

    def _on_error(self, msg):
        logger.error(f"WS Error: {msg}")

    def _status(self, now):
        parts = [f"[{now.strftime('%H:%M:%S')}]"]
        if self.spot_ltp: parts.append(f"NIFTY:{self.spot_ltp:.2f}")
        with self._symbol_lock:
            ce_s = self.ce_symbol; pe_s = self.pe_symbol
        if ce_s and self.ce_ltp:
            short = ce_s[-10:]
            parts.append(f"CE({short}):₹{self.ce_ltp:.2f}")
        if pe_s and self.pe_ltp:
            short = pe_s[-10:]
            parts.append(f"PE({short}):₹{self.pe_ltp:.2f}")
        parts.append(f"Signals:{self.total_observed}(V:{self.valid_count}/I:{self.invalid_count})")
        parts.append(f"Watching:{len(self.active_signals)}")
        print(f"\r{'  |  '.join(parts):<180}", end="", flush=True)

    # ── EOD ────────────────────────────────────
    def _eod(self, now):
        logger.info("\n📊 Market closed. Finalizing pending signals...")
        # Close any still-watching signals as expired
        for sig in self.active_signals:
            if sig.state != SignalState.CLOSED:
                sig.invalidate("expired")
                result = sig._build_invalid_result()
                self._log_result(result)
        self.active_signals = []

        print()
        logger.info("=" * 60)
        logger.info("📊 SESSION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"  Total Signals   : {self.total_observed}")
        logger.info(f"  Valid Breakouts : {self.valid_count}")
        logger.info(f"  Invalid/Expired : {self.invalid_count}")
        logger.info(f"  Results saved   : {OBS_CSV}")
        logger.info("=" * 60)
        self.is_running = False

    # ── Run ────────────────────────────────────
    def run(self):
        logger.info("=" * 60)
        logger.info("🚀 LIVE 5-MIN DIVERGENCE DATA COLLECTOR")
        logger.info(f"   Option range: ₹{OPTION_PRICE_MIN}-{OPTION_PRICE_MAX}")
        logger.info(f"   Candle size : 5 minutes")
        logger.info(f"   Cases tracked: 1 (High first), 2 (Low→High same candle),")
        logger.info(f"                  3 (2nd candle High), 4 (Low-only INVALID)")
        logger.info("=" * 60)

        if not self.authenticate():
            return

        now = datetime.now(IST)
        if now.time() < MARKET_OPEN:
            target = now.replace(hour=9, minute=15, second=0, microsecond=0)
            wait   = (target - now).total_seconds()
            logger.info(f"⏳ Market opens at 09:15. Waiting {int(wait//60)} min...")
            time.sleep(max(0, wait - 30))

        if now.time() > MARKET_CLOSE:
            logger.info("Market closed for today.")
            return

        # Initial option selection
        logger.info("🔍 Finding options in ₹70-80 range...")
        ce, pe = self.find_options_in_range()
        if not ce or not pe:
            logger.error("No options found. Exiting.")
            return
        with self._symbol_lock:
            self.ce_symbol = ce
            self.pe_symbol = pe
        self.last_option_refresh = time.time()

        # Connect WebSocket
        logger.info("📡 Connecting WebSocket...")
        try:
            self.fyers_ws = data_ws.FyersDataSocket(
                access_token=self.access_token,
                log_path="",
                litemode=False,
                write_to_file=False,
                reconnect=True,
                on_connect=self._on_open,
                on_close=self._on_close,
                on_error=self._on_error,
                on_message=self._on_message,
            )
            self.fyers_ws.connect()

            # Start refresh thread
            self._refresh_thread = threading.Thread(
                target=self._option_refresh_worker, daemon=True
            )
            self._refresh_thread.start()

            while self.is_running:
                time.sleep(1)

        except KeyboardInterrupt:
            print()
            logger.info("⚠️ Interrupted.")
            self._eod(datetime.now(IST))
        except Exception as e:
            logger.error(f"Fatal: {e}", exc_info=True)
            self._eod(datetime.now(IST))


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    LiveDataCollector().run()
