"""
Seed Dashboard DB — Offline seeding and live replay
=====================================================
Seeds the dashboard SQLite DB from CSV files for testing the dashboard
without a live market.

Supports two data sources:
  1. data_5min/ — full ISO timestamps (the original live dataset).
  2. 2024_daywise/ + 2024_daywise_spot/ — historical backtest data with
     per-day folders of CE/PE option files and separate spot files.

Usage:
  # data_5min dataset (existing behavior):
  python seed_dashboard_db.py --seed-day 2026-03-04
  python seed_dashboard_db.py --live-sim 2026-03-04

  # 2024_daywise backtest dataset:
  python seed_dashboard_db.py --seed-daywise 01APR24
  python seed_dashboard_db.py --seed-daywise-all
"""

import os
import csv
import glob
import json
import time
import argparse
import logging
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path

import pytz

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('SeedDB')

IST = pytz.timezone('Asia/Kolkata')

# Strategy constants — kept in lockstep with backtest_2024_daywise.py
CAPITAL = 20000.0
LOT_SIZE = 65            # Nifty: 1 lot = 65 units
LOTS = 1                 # Fixed 1 lot per trade
TP_RR = 2.5             # Take-profit risk:reward multiple
MARKET_OPEN_TIME = dt_time(9, 30)  # No entries before 09:30

# Paths — set from CLI or defaults
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / 'data_5min'
DATA_DIR = DEFAULT_DATA_DIR
METADATA_FILE = None

DAYWISE_DIR = REPO_ROOT / '2024_daywise'
DAYWISE_SPOT_DIR = REPO_ROOT / '2024_daywise_spot'

# Import our DB
from dashboard_db import DashboardDB


# =============================================================================
# CSV loading
# =============================================================================

def load_csv_candles(filepath, base_date=None):
    """Load candles from a CSV file. Returns list of dicts.

    Handles two ``datetime`` column formats:
      - Full ISO timestamps (e.g. ``2026-03-04 09:15:00+05:30``) — data_5min.
      - Time-only values (e.g. ``09:15``) — 2024_daywise. In this case
        ``base_date`` (a ``datetime.date``) is combined with the time and
        localized to IST.
    """
    candles = []
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row['datetime'].strip()
            if base_date is not None and len(raw) <= 5 and ':' in raw:
                hh, mm = raw.split(':')
                naive = datetime(base_date.year, base_date.month, base_date.day,
                                 int(hh), int(mm))
                dt = IST.localize(naive)
            else:
                dt = datetime.fromisoformat(raw)
            candles.append({
                'time': dt,
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
            })
    return candles


# =============================================================================
# Data loaders
# =============================================================================

def _parse_daywise_folder(folder_name):
    """Convert a 2024_daywise folder name (e.g. '01APR24') to a date."""
    return datetime.strptime(folder_name, '%d%b%y').date()


def _safe_parse(folder_name):
    """Return True if folder_name parses as DDMMMYY date."""
    try:
        _parse_daywise_folder(folder_name)
        return True
    except ValueError:
        return False


def get_daywise_day_data(folder_name):
    """Load spot, CE, PE candles for a day from the 2024_daywise dataset."""
    day_dir = DAYWISE_DIR / folder_name
    if not day_dir.is_dir():
        logger.error(f"Day folder not found: {day_dir}")
        return None

    try:
        day_date = _parse_daywise_folder(folder_name)
    except ValueError:
        logger.error(f"Could not parse date from folder name: {folder_name}")
        return None

    spot_file = DAYWISE_SPOT_DIR / f"nifty_spot_5min_{day_date.strftime('%Y-%m-%d')}.csv"
    ce_files = sorted(glob.glob(str(day_dir / '*CE_*.csv')))
    pe_files = sorted(glob.glob(str(day_dir / '*PE_*.csv')))

    if not spot_file.exists():
        logger.error(f"Missing spot file: {spot_file}")
        return None
    if not ce_files or not pe_files:
        logger.error(f"Missing CE/PE files in {day_dir}")
        return None

    ce_file = Path(ce_files[0])
    pe_file = Path(pe_files[0])

    ce_symbol = f"NSE:NIFTY-{ce_file.stem}"
    pe_symbol = f"NSE:NIFTY-{pe_file.stem}"

    return {
        'spot': load_csv_candles(spot_file, base_date=day_date),
        'ce': load_csv_candles(ce_file, base_date=day_date),
        'pe': load_csv_candles(pe_file, base_date=day_date),
        'ce_symbol': ce_symbol,
        'pe_symbol': pe_symbol,
        'date_str': day_date.strftime('%Y-%m-%d'),
    }


def get_day_data(date_str):
    """Load spot, CE, PE candle data for a given date (data_5min)."""
    spot_file = DATA_DIR / f'nifty_spot_5min_{date_str}.csv'
    ce_file = DATA_DIR / f'nifty_ce_5min_{date_str}.csv'
    pe_file = DATA_DIR / f'nifty_pe_5min_{date_str}.csv'

    if not all(f.exists() for f in [spot_file, ce_file, pe_file]):
        logger.error(f"Missing data files for {date_str}")
        return None

    meta = {}
    if METADATA_FILE and METADATA_FILE.exists():
        with open(METADATA_FILE) as f:
            meta = json.load(f)

    day_meta = meta.get('days', {}).get(date_str, {})
    ce_symbol = day_meta.get('ce_symbol', f'NSE:NIFTY_CE_{date_str}')
    pe_symbol = day_meta.get('pe_symbol', f'NSE:NIFTY_PE_{date_str}')

    return {
        'spot': load_csv_candles(spot_file),
        'ce': load_csv_candles(ce_file),
        'pe': load_csv_candles(pe_file),
        'ce_symbol': ce_symbol,
        'pe_symbol': pe_symbol,
        'date_str': date_str,
    }


# =============================================================================
# Strategy logic  — mirrors backtest_2024_daywise.py (Case 1 only)
# =============================================================================

def run_day_backtest(data, start_capital=CAPITAL):
    """Replicate backtest_2024_daywise.py's process_day on loaded candle dicts.

    Rules (identical to the backtest):
      - Signal: (Spot GREEN & PE GREEN) -> PE_BUY ; (Spot RED & CE GREEN) -> CE_BUY
      - Case 1 entry ONLY: the candle immediately after the signal must break the
        signal HIGH and must NOT break the signal LOW. Otherwise the signal is
        discarded (no Case 2/3 entry).
      - Entry price = signal high (no buffer).
      - Risk = max(entry - signal_low, avg_candle_size), where avg_candle_size is
        the mean (high-low) over EVERY candle from the open up to AND INCLUDING
        the signal candle.
      - SL = entry - risk (no clamp).  TP = entry + 2.5 * risk.
      - Single position at a time across BOTH CE and PE (chronological), no cap.
      - No entries before 09:30. SL is pessimistic if SL & TP hit same candle.

    Args:
        start_capital: Running capital carried in from prior days. Each trade's
            ``capital_after`` is computed against this so multi-day seeding
            compounds correctly instead of resetting to ₹20,000 every day.

    Returns (trades, signal_count, end_capital).
    """
    spot_by_time = {c['time']: c for c in data['spot']}

    signal_defs = (
        (data['ce'], data['ce_symbol'], 'CE_BUY'),
        (data['pe'], data['pe_symbol'], 'PE_BUY'),
    )

    # --- Detect signals ---
    signals = []
    for candles, symbol, sig_type in signal_defs:
        for idx, oc in enumerate(candles):
            sc = spot_by_time.get(oc['time'])
            if sc is None:
                continue
            spot_green = sc['close'] > sc['open']
            spot_red = sc['close'] < sc['open']
            opt_green = oc['close'] > oc['open']
            if (sig_type == 'CE_BUY' and spot_red and opt_green) or \
               (sig_type == 'PE_BUY' and spot_green and opt_green):
                signals.append({
                    'time': oc['time'], 'type': sig_type, 'symbol': symbol,
                    'candles': candles, 'sig_idx': idx,
                    'sig_high': oc['high'], 'sig_low': oc['low'],
                })

    signals.sort(key=lambda s: s['time'])

    # --- Resolve trades (single position, chronological) ---
    trades = []
    capital = start_capital
    trade_end_time = None  # exit time of the currently/last open trade

    for sig in signals:
        # No entries before 09:30
        if sig['time'].time() < MARKET_OPEN_TIME:
            continue
        # Overlap guard: skip signals that fire while a trade is still open
        if trade_end_time is not None and sig['time'] < trade_end_time:
            continue

        candles = sig['candles']
        entry_idx = sig['sig_idx'] + 1
        if entry_idx >= len(candles):
            continue

        c1 = candles[entry_idx]
        entry_price = sig['sig_high']
        broke_high = c1['high'] >= entry_price
        broke_low = c1['low'] <= sig['sig_low']
        # Case 1 only: must break high WITHOUT breaking low
        if not (broke_high and not broke_low):
            continue

        entry_time = c1['time']
        if trade_end_time is not None and entry_time < trade_end_time:
            continue

        # Average candle size over ALL candles up to & including the signal candle
        past = candles[:sig['sig_idx'] + 1]
        avg_size = sum(c['high'] - c['low'] for c in past) / len(past) if past else 0.0

        orig_risk = entry_price - sig['sig_low']
        if orig_risk <= 0:
            continue
        final_risk = max(orig_risk, avg_size)
        sl = entry_price - final_risk
        tp = entry_price + TP_RR * final_risk

        # Walk forward from the entry candle to find the exit
        exit_price = exit_time = exit_reason = None
        highest = entry_price
        for c in candles[entry_idx:]:
            highest = max(highest, c['high'])
            hit_sl = c['low'] <= sl
            hit_tp = c['high'] >= tp
            if hit_sl:  # pessimistic: SL wins ties
                exit_price, exit_time, exit_reason = sl, c['time'], 'SL'
                break
            if hit_tp:
                exit_price, exit_time, exit_reason = tp, c['time'], 'TP'
                break
        if exit_price is None:
            last = candles[-1]
            exit_price, exit_time, exit_reason = last['close'], last['time'], 'EOD_CLOSE'

        trade_end_time = exit_time
        pnl_per_unit = exit_price - entry_price
        pnl_total = pnl_per_unit * LOT_SIZE * LOTS
        capital += pnl_total

        trades.append({
            'symbol': sig['symbol'],
            'type': sig['type'],
            'entry_price': round(entry_price, 2),
            'entry_time': entry_time,
            'exit_price': round(exit_price, 2),
            'exit_time': exit_time,
            'sl': round(sl, 2),
            'tp': round(tp, 2),
            'risk': round(final_risk, 2),
            'lots': LOTS,
            'highest_reached': round(min(highest, tp), 2),
            'pnl_per_unit': round(pnl_per_unit, 2),
            'pnl_total': round(pnl_total, 2),
            'exit_reason': exit_reason,
            'reason': f"Spot {'GREEN + PE GREEN' if 'PE' in sig['type'] else 'RED + CE GREEN'}",
            'signal_time': sig['time'],
            'signal_high': sig['sig_high'],
            'signal_low': sig['sig_low'],
            'capital_after': round(capital, 2),
        })

    return trades, len(signals), capital


# =============================================================================
# DB seeding
# =============================================================================

def _seed_data(data, start_capital=CAPITAL):
    """Core seeding logic — inserts candles, runs strategy, writes trades.

    Returns the running capital AFTER this day so callers can compound across
    multiple days.
    """
    date_str = data['date_str']
    db = DashboardDB()
    spot_symbol = 'NSE:NIFTY50-INDEX'

    # Insert candles
    logger.info(f"  Inserting {len(data['spot'])} spot candles...")
    for c in data['spot']:
        db.upsert_candle(spot_symbol, c, is_final=True)

    logger.info(f"  Inserting {len(data['ce'])} CE candles ({data['ce_symbol']})...")
    for c in data['ce']:
        db.upsert_candle(data['ce_symbol'], c, is_final=True)

    logger.info(f"  Inserting {len(data['pe'])} PE candles ({data['pe_symbol']})...")
    for c in data['pe']:
        db.upsert_candle(data['pe_symbol'], c, is_final=True)

    # Run the backtest (mirrors backtest_2024_daywise.py), compounding capital
    all_trades, signal_count, capital = run_day_backtest(data, start_capital=start_capital)
    logger.info(f"  Found {signal_count} signals -> {len(all_trades)} trades taken")

    total_pnl = 0.0
    for t in all_trades:
        db.insert_trade(t, t['capital_after'])
        total_pnl += t['pnl_total']

        # Use the actual trade times as event timestamps so /api/summary
        # can match events to dates correctly via date(ts).
        sig_ts    = t['signal_time'].isoformat() if hasattr(t['signal_time'], 'isoformat') else str(t['signal_time'])
        entry_ts  = t['entry_time'].isoformat()  if hasattr(t['entry_time'],  'isoformat') else str(t['entry_time'])
        exit_ts   = t['exit_time'].isoformat()   if hasattr(t['exit_time'],   'isoformat') else str(t['exit_time'])

        db.insert_event('SIGNAL', f"{t['type']} signal: {t['symbol']}", {
            'symbol': t['symbol'], 'type': t['type'],
        }, ts=sig_ts)
        db.insert_event('ENTRY', f"Entered {t['type']} @ ₹{t['entry_price']:.2f}", {
            'symbol': t['symbol'], 'entry': t['entry_price'],
        }, ts=entry_ts)
        db.insert_event('EXIT', f"Exited ({t['exit_reason']}) PnL=₹{t['pnl_total']:+.2f}", {
            'symbol': t['symbol'], 'exit_reason': t['exit_reason'], 'pnl': t['pnl_total'],
        }, ts=exit_ts)

    logger.info(f"  Inserted {len(all_trades)} trades, total PnL: ₹{total_pnl:+.2f}")

    # EOD info event — timestamp = end of trading day for this date
    eod_ts = (all_trades[-1]['exit_time'].isoformat()
              if all_trades and hasattr(all_trades[-1]['exit_time'], 'isoformat')
              else datetime.now(IST).isoformat())
    db.insert_event('INFO', f"EOD — Daily PnL: ₹{total_pnl:+.2f}, Capital: ₹{capital:.2f}", ts=eod_ts)

    # State blob
    last_spot = data['spot'][-1] if data['spot'] else None
    last_ce = data['ce'][-1] if data['ce'] else None
    last_pe = data['pe'][-1] if data['pe'] else None

    state = {
        'heartbeat': datetime.now(IST).isoformat(),
        'ws_connected': False,
        'running_capital': capital,
        'daily_pnl': total_pnl,
        'daily_signals': signal_count,
        'spot_ltp': last_spot['close'] if last_spot else None,
        'ce_ltp': last_ce['close'] if last_ce else None,
        'pe_ltp': last_pe['close'] if last_pe else None,
        'ce_symbol': data['ce_symbol'],
        'pe_symbol': data['pe_symbol'],
        'active_trade': None,
        'pending_signals': [],
    }
    db.upsert_state('engine', state)
    logger.info(f"✅ Seeding complete for {date_str}! (capital: ₹{capital:.2f})")
    return capital


def seed_day(date_str, start_capital=None):
    """Seed from data_5min dataset. Resumes capital from DB unless given."""
    logger.info(f"Seeding data for {date_str}...")
    data = get_day_data(date_str)
    if not data:
        return start_capital if start_capital is not None else CAPITAL
    if start_capital is None:
        start_capital = _resume_capital()
    return _seed_data(data, start_capital=start_capital)


def seed_daywise(folder_name, start_capital=None):
    """Seed from 2024_daywise dataset. Resumes capital from DB unless given."""
    logger.info(f"Seeding 2024_daywise data for {folder_name}...")
    data = get_daywise_day_data(folder_name)
    if not data:
        return start_capital if start_capital is not None else CAPITAL
    if start_capital is None:
        start_capital = _resume_capital()
    return _seed_data(data, start_capital=start_capital)


def _resume_capital():
    """Return the last trade's capital_after from the DB, or the base capital."""
    try:
        last = DashboardDB().get_last_capital()
        return last if last is not None and last > 0 else CAPITAL
    except Exception:
        return CAPITAL


# =============================================================================
# Live sim (data_5min only)
# =============================================================================

def live_sim(date_str):
    """Replay candles at ~1 per 2s, simulating live behavior."""
    logger.info(f"Starting live simulation for {date_str}...")

    data = get_day_data(date_str)
    if not data:
        return

    db = DashboardDB()
    spot_symbol = 'NSE:NIFTY50-INDEX'

    timeline = []
    for c in data['spot']:
        timeline.append(('spot', spot_symbol, c))
    for c in data['ce']:
        timeline.append(('ce', data['ce_symbol'], c))
    for c in data['pe']:
        timeline.append(('pe', data['pe_symbol'], c))
    timeline.sort(key=lambda x: x[2]['time'])

    capital = 20000.0
    total_pnl = 0.0
    logger.info(f"  Replaying {len(timeline)} candles...")

    for i, (kind, symbol, candle) in enumerate(timeline):
        db.upsert_candle(symbol, candle, is_final=False)

        state = {
            'heartbeat': datetime.now(IST).isoformat(),
            'ws_connected': True,
            'running_capital': capital,
            'daily_pnl': total_pnl,
            'daily_signals': 0,
            'spot_ltp': candle['close'] if kind == 'spot' else None,
            'ce_ltp': candle['close'] if kind == 'ce' else None,
            'pe_ltp': candle['close'] if kind == 'pe' else None,
            'ce_symbol': data['ce_symbol'],
            'pe_symbol': data['pe_symbol'],
            'active_trade': None,
            'pending_signals': [],
        }
        db.upsert_state('engine', state)

        time.sleep(0.5)
        db.upsert_candle(symbol, candle, is_final=True)

        if (i + 1) % 10 == 0:
            logger.info(f"  Replayed {i+1}/{len(timeline)} candles")
        time.sleep(1.5)

    logger.info("✅ Live simulation complete!")


# =============================================================================
# CLI
# =============================================================================

def _reset_db():
    """Wipe all rows so a re-seed produces correct (non-duplicated) totals."""
    db = DashboardDB()
    conn = db._get_conn()
    for tbl in ('candles', 'trades', 'events', 'state'):
        conn.execute(f"DELETE FROM {tbl}")
    try:
        conn.execute("DELETE FROM sqlite_sequence")  # reset AUTOINCREMENT counters
    except Exception:
        pass
    conn.commit()
    logger.info("🧹 Cleared candles/trades/events/state (fresh DB).")


def main():
    global DATA_DIR, METADATA_FILE, DAYWISE_DIR, DAYWISE_SPOT_DIR

    parser = argparse.ArgumentParser(
        description='Seed the dashboard DB from CSV datasets (data_5min or 2024_daywise)')
    # data_5min options
    parser.add_argument('--seed-day', type=str, help='Seed from data_5min for date YYYY-MM-DD')
    parser.add_argument('--live-sim', type=str, help='Replay candles from data_5min (YYYY-MM-DD)')
    parser.add_argument('--seed-all', action='store_true', help='Seed all dates in data_5min')
    parser.add_argument('--data-dir', type=str, help='Path to data_5min/ directory')
    # 2024_daywise options
    parser.add_argument('--seed-daywise', type=str, metavar='DDMMMYY',
                        help='Seed a day from 2024_daywise (folder name, e.g. 01APR24)')
    parser.add_argument('--seed-daywise-all', action='store_true',
                        help='Seed ALL days in 2024_daywise')
    parser.add_argument('--daywise-dir', type=str, help='Path to 2024_daywise/ directory')
    parser.add_argument('--daywise-spot-dir', type=str, help='Path to 2024_daywise_spot/ directory')
    parser.add_argument('--reset', action='store_true',
                        help='Wipe the DB before seeding (recommended for *-all to avoid duplicate rows)')

    args = parser.parse_args()

    # Resolve paths
    if args.data_dir:
        DATA_DIR = Path(args.data_dir).resolve()
    METADATA_FILE = DATA_DIR / 'metadata.json'

    if args.daywise_dir:
        DAYWISE_DIR = Path(args.daywise_dir).resolve()
    if args.daywise_spot_dir:
        DAYWISE_SPOT_DIR = Path(args.daywise_spot_dir).resolve()

    if args.reset:
        _reset_db()

    # Dispatch
    if args.seed_daywise:
        if not DAYWISE_DIR.exists():
            logger.error(f"2024_daywise directory not found: {DAYWISE_DIR}")
            return
        seed_daywise(args.seed_daywise)

    elif args.seed_daywise_all:
        if not DAYWISE_DIR.exists():
            logger.error(f"2024_daywise directory not found: {DAYWISE_DIR}")
            return
        folders = sorted(
            [p.name for p in DAYWISE_DIR.iterdir() if p.is_dir()],
            key=lambda n: _parse_daywise_folder(n) if _safe_parse(n) else datetime.max.date()
        )
        logger.info(f"Found {len(folders)} day folders in {DAYWISE_DIR}")
        # Compound capital chronologically across all days.
        capital = CAPITAL if args.reset else _resume_capital()
        for folder_name in folders:
            if _safe_parse(folder_name):
                capital = seed_daywise(folder_name, start_capital=capital)
            else:
                logger.warning(f"Skipping unrecognized folder: {folder_name}")
        logger.info(f"💰 Final compounded capital: ₹{capital:.2f}")

    elif args.seed_day:
        if not DATA_DIR.exists():
            logger.error(f"Data directory not found: {DATA_DIR}")
            return
        seed_day(args.seed_day)

    elif args.live_sim:
        if not DATA_DIR.exists():
            logger.error(f"Data directory not found: {DATA_DIR}")
            return
        live_sim(args.live_sim)

    elif args.seed_all:
        if not DATA_DIR.exists():
            logger.error(f"Data directory not found: {DATA_DIR}")
            return
        if METADATA_FILE.exists():
            with open(METADATA_FILE) as f:
                meta = json.load(f)
            capital = CAPITAL if args.reset else _resume_capital()
            for date_str in meta.get('dates', []):
                capital = seed_day(date_str, start_capital=capital)
            logger.info(f"💰 Final compounded capital: ₹{capital:.2f}")
        else:
            logger.error("metadata.json not found in data_5min/")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
