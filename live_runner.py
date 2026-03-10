"""
Live Runner — Unified Single Entry Point
=========================================
One authentication, one WebSocket connection.
Runs two strategies simultaneously:
  1. LiveDivergenceEngine  — 1-min divergence paper trading with trailing SL
  2. LiveDataCollector     — 5-min divergence data observer (signal case logging)

Both strategies share:
  - The same Fyers access token
  - The same WebSocket connection
  - The same CE/PE option symbols (one option refresh thread, shared)
  - The same spot LTP

Tick Dispatcher: every incoming tick is forwarded to both strategy handlers.

Usage:
    python live_runner.py
    (or via ./run_live.sh)
"""

import os
import time
import logging
import threading
import pytz
import dotenv
from datetime import datetime, time as dt_time

from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

# Import the core classes (stripped of their individual run() / auth / WS logic)
from live_divergence_1min import (
    LiveDivergenceEngine,
    MARKET_OPEN, MARKET_CLOSE,
    SPOT_SYMBOL,
    API_DELAY,
)
from live_5min_collector import LiveDataCollector

dotenv.load_dotenv()

IST = pytz.timezone("Asia/Kolkata")
OPTION_REFRESH_SECONDS = 300    # shared option refresh every 5 min

# ─────────────────────────────────────────────
# Logging (both strategies log to the same place)
# ─────────────────────────────────────────────
LOG_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FMT,
    handlers=[
        logging.FileHandler("live_runner.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("LiveRunner")


# ─────────────────────────────────────────────
# Shared Auth
# ─────────────────────────────────────────────
def authenticate():
    from main import FyersAuthenticator
    cid  = os.getenv("CLIENT_ID")
    sk   = os.getenv("SECRET_KEY")
    user = os.getenv("USERNAME")
    pin  = os.getenv("PIN")
    totp = os.getenv("TOTP_KEY")
    if not all([cid, sk, user, pin, totp]):
        logger.error("❌ Missing .env variables!")
        return None, None, None
    auth = FyersAuthenticator(cid, sk, "https://www.google.com", user, pin, totp)
    token, err = auth.get_access_token()
    if not token:
        logger.error(f"❌ Auth failed: {err}")
        return None, None, None
    fyers = fyersModel.FyersModel(client_id=cid, token=token, log_path="")
    logger.info("✅ Authentication successful (shared)!")
    return fyers, token, cid


# ─────────────────────────────────────────────
# Unified Runner
# ─────────────────────────────────────────────
class UnifiedRunner:
    """
    Owns:
      - One Fyers REST client (shared)
      - One WebSocket connection
      - One option refresh thread

    Delegates:
      - 1-min tick processing → LiveDivergenceEngine._process_tick()
      - 5-min tick processing → LiveDataCollector._process_tick()
    """

    def __init__(self):
        self.fyers        = None
        self.fyers_ws     = None
        self.access_token = None
        self.client_id    = None
        self.is_running   = False

        # Shared option state
        self.ce_symbol    = None
        self.pe_symbol    = None
        self._symbol_lock = threading.Lock()
        self._refresh_thread = None
        self.last_refresh = 0

        # Strategy engines — created after auth so they can share fyers
        self.engine_1min  = None    # LiveDivergenceEngine
        self.engine_5min  = None    # LiveDataCollector

    # ── Auth + Engine Init ─────────────────────
    def _setup(self):
        self.fyers, self.access_token, self.client_id = authenticate()
        if not self.fyers:
            return False

        # Create engines WITHOUT their own auth/WS — we inject shared clients
        self.engine_1min = LiveDivergenceEngine.__new__(LiveDivergenceEngine)
        self.engine_1min.__init__()
        self.engine_1min.fyers        = self.fyers
        self.engine_1min.access_token = self.access_token
        self.engine_1min.client_id    = self.client_id

        self.engine_5min = LiveDataCollector.__new__(LiveDataCollector)
        self.engine_5min.__init__()
        self.engine_5min.fyers        = self.fyers
        self.engine_5min.access_token = self.access_token
        self.engine_5min.client_id    = self.client_id

        logger.info("✅ Both strategy engines initialized.")
        return True

    # ── Shared Option Refresh ──────────────────
    def _find_shared_options(self):
        """
        Find CE/PE options using the 1-min engine's logic (60-70 range).
        Both engines will use the same symbols.
        """
        ce, pe = self.engine_1min.find_options_in_range()
        return ce, pe

    def _option_refresh_worker(self):
        logger.info("🔄 Shared option refresh thread started (every 5 min).")
        while self.is_running:
            time.sleep(OPTION_REFRESH_SECONDS)
            if not self.is_running:
                break

            # Skip if 1-min engine has an active trade
            if self.engine_1min.active_trade is not None:
                logger.info("🔄 Refresh skipped — active trade in progress.")
                continue

            # Skip if 5-min engine has active signals being watched
            if self.engine_5min.active_signals:
                logger.info("🔄 Refresh skipped — 5-min signals being tracked.")
                continue

            try:
                logger.info("🔄 Refreshing shared options...")
                new_ce, new_pe = self._find_shared_options()
                if not new_ce or not new_pe:
                    logger.warning("🔄 Refresh failed — keeping current options.")
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
                        logger.info(f"  CE → {new_ce}")

                    if pe_changed:
                        if self.pe_symbol: unsub.append(self.pe_symbol)
                        sub.append(new_pe)
                        self.pe_symbol = new_pe
                        logger.info(f"  PE → {new_pe}")

                    # Propagate to both engines
                    for eng in [self.engine_1min, self.engine_5min]:
                        with eng._symbol_lock:
                            if ce_changed:
                                eng.ce_symbol = new_ce
                                eng.ce_ltp    = None
                            if pe_changed:
                                eng.pe_symbol = new_pe
                                eng.pe_ltp    = None

                    # Clear stale state in each engine
                    self.engine_1min.pending_signals = {}
                    self.engine_1min.candle_history  = {
                        k: v for k, v in self.engine_1min.candle_history.items()
                        if k == SPOT_SYMBOL
                    }
                    self.engine_5min.candle_hist = {
                        k: v for k, v in self.engine_5min.candle_hist.items()
                        if k == SPOT_SYMBOL
                    }
                    self.engine_5min.active_signals = []

                if self.fyers_ws:
                    if unsub:
                        self.fyers_ws.unsubscribe(symbols=unsub)
                        logger.info(f"  Unsubscribed: {unsub}")
                        time.sleep(0.3)
                    if sub:
                        self.fyers_ws.subscribe(symbols=sub, data_type="SymbolUpdate")
                        logger.info(f"  📡 Subscribed: {sub}")

                self.last_refresh = time.time()
                logger.info(f"✅ Options updated: CE={self.ce_symbol} PE={self.pe_symbol}")

            except Exception as e:
                logger.error(f"Option refresh error: {e}", exc_info=True)

    # ── Tick Dispatcher ────────────────────────
    def _on_message(self, message):
        """
        Single WebSocket message handler.
        Routes every tick to BOTH strategy engines.
        """
        try:
            if not isinstance(message, dict) or "ltp" not in message:
                return

            symbol = message["symbol"]
            ltp    = message["ltp"]
            now    = datetime.now(IST)

            # Update shared LTPs on both engines
            with self._symbol_lock:
                cur_ce = self.ce_symbol
                cur_pe = self.pe_symbol

            if symbol == SPOT_SYMBOL:
                self.engine_1min.spot_ltp = ltp
                self.engine_5min.spot_ltp = ltp
            elif symbol == cur_ce:
                self.engine_1min.ce_ltp = ltp
                self.engine_5min.ce_ltp = ltp
            elif symbol == cur_pe:
                self.engine_1min.pe_ltp = ltp
                self.engine_5min.pe_ltp = ltp
            else:
                return  # Old/unknown symbol

            current_bucket = self.engine_5min.candle_mgr._bucket()

            # ── 1-min engine tick processing ──
            self._tick_1min(symbol, ltp, now, cur_ce, cur_pe)

            # ── 5-min engine tick processing ──
            self._tick_5min(symbol, ltp, now, cur_ce, cur_pe, current_bucket)

            # ── Status ─────────────────────
            self._status(now)

        except Exception as e:
            logger.error(f"Dispatcher error: {e}", exc_info=True)

    def _tick_1min(self, symbol, ltp, now, cur_ce, cur_pe):
        """Forward tick to 1-min engine logic."""
        eng = self.engine_1min

        # Market close?
        if now.time() > MARKET_CLOSE:
            if eng.is_running:
                eng.is_running = False
                eng._handle_eod(now)
            return

        # Active trade management
        if eng.active_trade is not None and symbol == eng.active_trade.symbol:
            result = eng.active_trade.update_tick(ltp, now)
            if result:
                eng._handle_trade_exit(result)

        # Pending signal entry check
        if symbol in eng.pending_signals and eng.active_trade is None:
            sig = eng.pending_signals[symbol]
            if ltp < sig["low"]:
                del eng.pending_signals[symbol]
            elif ltp > sig["high"]:
                eng._enter_trade(symbol, ltp, now, sig)
                del eng.pending_signals[symbol]

        # 1-min candle building
        completed = eng.candle_manager.update(symbol, ltp)
        if completed:
            eng._store_candle(symbol, completed)
            if symbol == SPOT_SYMBOL:
                eng.check_divergence(now)

    def _tick_5min(self, symbol, ltp, now, cur_ce, cur_pe, current_bucket):
        """Forward tick to 5-min collector logic."""
        eng = self.engine_5min

        if now.time() > MARKET_CLOSE:
            if eng.is_running:
                eng.is_running = False
                eng._eod(now)
            return

        # Feed tick to all watching signals
        done = []
        for sig in eng.active_signals:
            if (sig.option == "CE" and symbol == cur_ce) or \
               (sig.option == "PE" and symbol == cur_pe):
                result = sig.tick(ltp, now, current_bucket)
                if result:
                    done.append((sig, result))

        for s, r in done:
            eng._log_result(r)
            if s in eng.active_signals:
                eng.active_signals.remove(s)

        # 5-min candle building
        completed = eng.candle_mgr.update(symbol, ltp)
        if completed:
            eng._store_candle(symbol, completed)

            # Candle close → update signal states
            done2 = []
            for sig in eng.active_signals:
                if (sig.option == "CE" and symbol == cur_ce) or \
                   (sig.option == "PE" and symbol == cur_pe):
                    result = sig.on_new_candle(completed, current_bucket)
                    if result:
                        done2.append((sig, result))

            for s, r in done2:
                eng._log_result(r)
                if s in eng.active_signals:
                    eng.active_signals.remove(s)

            # New 5-min divergence signal detection (on spot candle close)
            if symbol == SPOT_SYMBOL:
                eng._detect_signals(now)

    # ── Status Line ────────────────────────────
    def _status(self, now):
        ts = now.strftime("%H:%M:%S")
        e1 = self.engine_1min
        e5 = self.engine_5min
        parts = [f"[{ts}]"]
        if e1.spot_ltp:
            parts.append(f"NIFTY:{e1.spot_ltp:.2f}")
        with self._symbol_lock:
            ce, pe = self.ce_symbol, self.pe_symbol
        if ce and e1.ce_ltp:
            parts.append(f"CE:₹{e1.ce_ltp:.2f}")
        if pe and e1.pe_ltp:
            parts.append(f"PE:₹{e1.pe_ltp:.2f}")

        # 1-min trade status
        if e1.active_trade:
            t = e1.active_trade
            parts.append(f"[1m]TRADE:{t.trade_type} SL:{t.sl:.2f} TP:{t.tp:.2f}")
        else:
            parts.append(f"[1m]PnL:₹{e1.daily_pnl:+.2f}")

        # 5-min observer status
        parts.append(f"[5m]Watching:{len(e5.active_signals)} V:{e5.valid_count}/I:{e5.invalid_count}")

        print(f"\r{'  |  '.join(parts):<200}", end="", flush=True)

    # ── WebSocket Callbacks ─────────────────────
    def _on_open(self):
        with self._symbol_lock:
            subs = [SPOT_SYMBOL]
            if self.ce_symbol: subs.append(self.ce_symbol)
            if self.pe_symbol: subs.append(self.pe_symbol)
        logger.info(f"📡 WebSocket Connected! Subscribing: {subs}")
        self.fyers_ws.subscribe(symbols=subs, data_type="SymbolUpdate")
        self.is_running = True
        self.engine_1min.is_running = True
        self.engine_5min.is_running = True
        self.fyers_ws.keep_running()

    def _on_close(self, msg):
        logger.info(f"WebSocket Closed: {msg}")
        self.is_running = False

    def _on_error(self, msg):
        logger.error(f"WebSocket Error: {msg}")

    # ── Main Run ───────────────────────────────
    def run(self):
        logger.info("=" * 65)
        logger.info("🚀 UNIFIED LIVE RUNNER")
        logger.info("   ├── 1-min Divergence Paper Trader (₹60-70 options)")
        logger.info("   └── 5-min Divergence Data Collector (Case 1/2/3/4)")
        logger.info("=" * 65)

        # Auth + engine init
        if not self._setup():
            return

        # Wait for market open
        now = datetime.now(IST)
        if now.time() < MARKET_OPEN:
            target   = now.replace(hour=9, minute=15, second=0, microsecond=0)
            wait_sec = (target - now).total_seconds()
            logger.info(f"⏳ Market opens 09:15. Waiting {int(wait_sec // 60)} min...")
            time.sleep(max(0, wait_sec - 30))

        now = datetime.now(IST)
        if now.time() > MARKET_CLOSE:
            logger.info("Market already closed.")
            return

        # Find initial options
        logger.info("🔍 Finding options (₹60-70 range)...")
        ce, pe = self._find_shared_options()
        if not ce or not pe:
            logger.error("No suitable options found. Exiting.")
            return

        with self._symbol_lock:
            self.ce_symbol = ce
            self.pe_symbol = pe

        # Push symbols into both engines
        for eng in [self.engine_1min, self.engine_5min]:
            eng.ce_symbol = ce
            eng.pe_symbol = pe

        logger.info(f"  CE: {ce}")
        logger.info(f"  PE: {pe}")

        # Connect shared WebSocket
        logger.info("📡 Connecting WebSocket (shared)...")
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

            # Inject shared WebSocket into both engines (for their refresh threads)
            self.engine_1min.fyers_ws = self.fyers_ws
            self.engine_5min.fyers_ws = self.fyers_ws

            self.fyers_ws.connect()

            # Start shared option refresh thread
            self._refresh_thread = threading.Thread(
                target=self._option_refresh_worker,
                daemon=True,
                name="SharedOptionRefresh",
            )
            self._refresh_thread.start()
            logger.info("🔄 Shared option refresh thread started.")

            # Keep alive
            while self.is_running:
                time.sleep(1)

        except KeyboardInterrupt:
            print()
            logger.info("⚠️  Interrupted.")
            now = datetime.now(IST)
            self.engine_1min._handle_eod(now)
            self.engine_5min._eod(now)

        except Exception as e:
            logger.error(f"Fatal: {e}", exc_info=True)
            now = datetime.now(IST)
            self.engine_1min._handle_eod(now)
            self.engine_5min._eod(now)


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    UnifiedRunner().run()
