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
  - SL = Entry - final_risk
  - TP = Entry + (2.5 * final_risk)
- Capital Compounding: Starts at ₹20,000 | Lot Size: 65

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
MARKET_OPEN = dt_time(9, 30)  # No trades before 9:30 AM
MARKET_CLOSE = dt_time(15, 30)

class CandleManager5Min:
    def __init__(self):
        self.current = {}
    
    def _bucket(self):
        now = datetime.now(IST)
        m = (now.hour * 60 + now.minute) // 5 * 5
        return now.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
        
    def update(self, symbol, ltp):
        b = self._bucket()
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

class ActiveTrade5Min:
    def __init__(self, symbol, trade_type, entry_price, entry_time, signal_candle, reason, avg_candle_size, lots):
        self.symbol = symbol
        self.trade_type = trade_type
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.reason = reason
        self.lots = lots
        
        sig_low = signal_candle['low']
        orig_risk = entry_price - sig_low
        orig_risk = orig_risk if orig_risk > 0 else 0.1
        
        self.final_risk = max(orig_risk, avg_candle_size)
        self.sl = entry_price - self.final_risk
        self.tp = entry_price + (2.5 * self.final_risk)
        
        self.highest_reached = entry_price
        self.is_open = True
        self.exit_price = None
        self.exit_time = None
        self.exit_reason = None
        
    def update_tick(self, ltp, timestamp):
        if not self.is_open: return None
        self.highest_reached = max(self.highest_reached, ltp)
        
        if ltp <= self.sl:
            return self._close(self.sl, timestamp, 'SL')
        if ltp >= self.tp:
            return self._close(self.tp, timestamp, 'TP')
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
            'pnl_total': pnl_total, 'exit_reason': reason, 'reason': self.reason
        }

class Live5MinEngine:
    def __init__(self):
        self.fyers = None
        self.fyers_ws = None
        self.access_token = None
        self.client_id = None
        self.is_running = False

        self.ce_symbol = None
        self.pe_symbol = None
        self.ce_ltp = None
        self.pe_ltp = None
        self.spot_ltp = None
        
        self._symbol_lock = threading.Lock()
        self._refresh_thread = None

        self.candle_manager = CandleManager5Min()
        self.candle_history = {} # Keep ALL today's 5min candles for avg size
        
        self.pending_signals = {}
        self.active_trade = None
        self.trade_history = []
        self.daily_signals = 0

        self.running_capital = CAPITAL
        self.daily_pnl = 0.0
        
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

    def authenticate(self):
        from main import FyersAuthenticator
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

    def _option_refresh_worker(self):
        while self.is_running:
            time.sleep(OPTION_REFRESH_SECONDS)
            if not self.is_running: break
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
        if symbol not in self.candle_history:
            self.candle_history[symbol] = []
        self.candle_history[symbol].append(candle)
        # NOT POPPING - keep all candles for accurate avg candle size computation!

    def check_divergence(self, timestamp):
        spot_candle = self.candle_history.get(SPOT_SYMBOL, [])[-1] if SPOT_SYMBOL in self.candle_history else None
        if not spot_candle: return

        if self.pe_symbol:
            pe_candles = self.candle_history.get(self.pe_symbol, [])
            if pe_candles and pe_candles[-1]['time'] == spot_candle['time']:
                pe_c = pe_candles[-1]
                if spot_candle['close'] > spot_candle['open'] and pe_c['close'] > pe_c['open']:
                    self.daily_signals += 1
                    rsn = f"Spot GREEN + PE GREEN"
                    self.pending_signals[self.pe_symbol] = {'type': 'PE_BUY', 'high': pe_c['high'], 'low': pe_c['low'], 'candle': pe_c, 'reason': rsn, 'expire_tick': pe_c['time'].timestamp() + 300}
                    print()
                    logger.info(f"🎯 PE Signal Detected: {self.pe_symbol} | High: {pe_c['high']:.2f}")

        if self.ce_symbol:
            ce_candles = self.candle_history.get(self.ce_symbol, [])
            if ce_candles and ce_candles[-1]['time'] == spot_candle['time']:
                ce_c = ce_candles[-1]
                if spot_candle['close'] < spot_candle['open'] and ce_c['close'] > ce_c['open']:
                    self.daily_signals += 1
                    rsn = f"Spot RED + CE GREEN"
                    self.pending_signals[self.ce_symbol] = {'type': 'CE_BUY', 'high': ce_c['high'], 'low': ce_c['low'], 'candle': ce_c, 'reason': rsn, 'expire_tick': ce_c['time'].timestamp() + 300}
                    print()
                    logger.info(f"🎯 CE Signal Detected: {self.ce_symbol} | High: {ce_c['high']:.2f}")

    def _on_message(self, message):
        try:
            if not isinstance(message, dict) or 'ltp' not in message: return
            if 'symbol' not in message: return
            
            symbol, ltp, now = message['symbol'], message['ltp'], datetime.now(IST)

            with self._symbol_lock:
                cur_ce, cur_pe = self.ce_symbol, self.pe_symbol

            if symbol == SPOT_SYMBOL: self.spot_ltp = ltp
            elif symbol == cur_ce: self.ce_ltp = ltp
            elif symbol == cur_pe: self.pe_ltp = ltp
            else: return

            if now.time() > MARKET_CLOSE:
                self._handle_eod(now)
                return

            # Active Trade Management
            if self.active_trade is not None and symbol == self.active_trade.symbol:
                res = self.active_trade.update_tick(ltp, now)
                if res: self._handle_trade_exit(res)

            # Pending Signal Check 
            if symbol in self.pending_signals and self.active_trade is None and now.time() >= MARKET_OPEN:
                sig = self.pending_signals[symbol]
                
                if ltp <= sig['low']:
                    logger.info(f"  ❌ Signal invalidated for {symbol} (Low broke first)")
                    del self.pending_signals[symbol]
                elif ltp >= sig['high']:
                    self._enter_trade(symbol, ltp, now, sig)
                    del self.pending_signals[symbol]

            # Candle Building
            comp = self.candle_manager.update(symbol, ltp)
            if comp:
                self._store_candle(symbol, comp)
                
                # Expiration: If a full 5-min candle completed and signal didn't trigger -> expire
                if symbol in self.pending_signals and cur_ce and cur_pe:
                    if float(now.timestamp()) > self.pending_signals[symbol]['expire_tick']:
                        logger.info(f"  ❌ Signal expired for {symbol} (No breakout in next candle)")
                        del self.pending_signals[symbol]
                
                if symbol == SPOT_SYMBOL:
                    self.check_divergence(now)

            self._print_status(now)

        except Exception as e:
            logger.error(f"Tick error: {e}", exc_info=True)

    def _enter_trade(self, symbol, ltp, ts, sig):
        cost_per_lot = ltp * LOT_SIZE
        lots = int(self.running_capital // cost_per_lot)
        
        if lots <= 0:
            logger.warning(f"  ⚠️ Insufficient capital: Need ₹{cost_per_lot:.2f}, have ₹{self.running_capital:.2f}")
            return
            
        hist = self.candle_history.get(symbol, [])
        avg_sz = sum(c['high'] - c['low'] for c in hist) / len(hist) if hist else 0.0

        self.active_trade = ActiveTrade5Min(symbol, sig['type'], ltp, ts, sig['candle'], sig['reason'], avg_sz, lots)
        
        print()
        logger.info("="*60)
        logger.info(f"📈 TRADE ENTERED: {sig['type']} on {symbol}")
        logger.info(f"  Entry: ₹{ltp:.2f} | SL: ₹{self.active_trade.sl:.2f} | TP: ₹{self.active_trade.tp:.2f}")
        logger.info(f"  Risk Used: {self.active_trade.final_risk:.2f} (Avg Sz: {avg_sz:.2f})")
        logger.info(f"  Lots: {lots} | Cost: ₹{cost_per_lot*lots:.2f} | Acc Bal: ₹{self.running_capital:.2f}")
        logger.info("="*60)

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

    def _handle_eod(self, now):
        if self.active_trade:
            ltp = self.ce_ltp if self.active_trade.symbol == self.ce_symbol else self.pe_ltp
            res = self.active_trade.close_eod(ltp or self.active_trade.entry_price, now)
            if res: self._handle_trade_exit(res)
        self.is_running = False
        print("\n✅ Market Closed. Daily PnL: ₹", self.daily_pnl)

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
        self.is_running = True
        self.fyers_ws.keep_running()

    def _on_error(self, message): 
        logger.error(f"WS Err: {message}")
        
    def _on_close(self, message): 
        self.is_running = False

    def run(self):
        logger.info("🚀 LIVE 5-MIN TRADER | SL=max(Risk,Avg), TP=2.5x RR")
        if not self.authenticate(): return
        now = datetime.now(IST)
        if now.time() < MARKET_OPEN:
            tgt = now.replace(hour=9, minute=30, second=0, microsecond=0)
            wait = (tgt - now).total_seconds()
            logger.info(f"⏳ Waiting {int(wait)}s for 09:30 AM open...")
            time.sleep(max(0, wait - 10))

        ce, pe = self.find_options_in_range()
        if not ce or not pe: return
        self.ce_symbol, self.pe_symbol = ce, pe

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
            while self.is_running: time.sleep(1)
        except KeyboardInterrupt:
            self._handle_eod(datetime.now(IST))

if __name__ == "__main__":
    Live5MinEngine().run()
