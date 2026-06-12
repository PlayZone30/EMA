"""
Seed Dashboard DB — Offline seeding and live replay
=====================================================
Seeds the dashboard SQLite DB from data_5min/ CSV files for testing
the dashboard without a live market.

Usage:
  python seed_dashboard_db.py --seed-day 2026-03-04
  python seed_dashboard_db.py --live-sim 2026-03-04
"""

import os
import csv
import json
import time
import random
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytz

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('SeedDB')

IST = pytz.timezone('Asia/Kolkata')
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / 'data_5min'
DATA_DIR = DEFAULT_DATA_DIR  # Can be overridden via --data-dir
METADATA_FILE = None  # Set after DATA_DIR is resolved

# Import our DB
from dashboard_db import DashboardDB


def load_csv_candles(filepath):
    """Load candles from a CSV file. Returns list of dicts."""
    candles = []
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            dt = datetime.fromisoformat(row['datetime'])
            candles.append({
                'time': dt,
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
            })
    return candles


def get_day_data(date_str):
    """Load spot, CE, PE candle data for a given date."""
    spot_file = DATA_DIR / f'nifty_spot_5min_{date_str}.csv'
    ce_file = DATA_DIR / f'nifty_ce_5min_{date_str}.csv'
    pe_file = DATA_DIR / f'nifty_pe_5min_{date_str}.csv'
    
    if not all(f.exists() for f in [spot_file, ce_file, pe_file]):
        logger.error(f"Missing data files for {date_str}")
        return None
    
    # Load metadata for symbol names
    meta = {}
    if METADATA_FILE.exists():
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
    }


def find_divergence_signals(spot_candles, opt_candles, opt_type):
    """Find divergence signals between spot and option candles.
    
    Returns list of (index, spot_candle, opt_candle, type) tuples.
    """
    signals = []
    spot_by_time = {c['time']: c for c in spot_candles}
    
    for i, oc in enumerate(opt_candles):
        sc = spot_by_time.get(oc['time'])
        if not sc:
            continue
        
        # Skip first few candles (need avg size data)
        if i < 5:
            continue
        
        if opt_type == 'PE':
            # Spot GREEN + PE GREEN
            if sc['close'] > sc['open'] and oc['close'] > oc['open']:
                signals.append((i, sc, oc, 'PE_BUY'))
        else:
            # Spot RED + CE GREEN
            if sc['close'] < sc['open'] and oc['close'] > oc['open']:
                signals.append((i, sc, oc, 'CE_BUY'))
    
    return signals


def fabricate_trades(signals, opt_candles, opt_symbol, opt_type):
    """Simulate trades honestly from detected signals by walking the actual candles.

    Entry = breakout of the signal candle's high during the next candle.
    Exit = first candle whose low touches SL or high touches TP (SL checked
    first, conservative), else EOD close on the last candle. SL/TP lines and
    exit markers therefore always agree with the price action on the chart.
    """
    trades = []
    capital = 20000.0
    lot_size = 65
    last_exit_time = None  # no overlapping trades

    for sig_idx, spot_c, opt_c, trade_type in signals:
        if len(trades) >= 3:
            break
        if sig_idx + 1 >= len(opt_candles):
            continue

        entry_candle = opt_candles[sig_idx + 1]
        if last_exit_time is not None and entry_candle['time'] <= last_exit_time:
            continue

        # Entry: first tick above the signal candle's high (small slippage)
        entry_price = round(opt_c['high'] + 0.05, 2)
        if entry_candle['high'] < entry_price:
            continue  # no breakout in the next candle -> signal expired
        entry_time = entry_candle['time'] + timedelta(minutes=2)

        # SL/TP per strategy math (mirrors ActiveTrade5Min)
        lookback = opt_candles[max(0, sig_idx - 10):sig_idx]
        avg_size = sum(c['high'] - c['low'] for c in lookback) / len(lookback) if lookback else 3.0
        orig_risk = entry_price - opt_c['low']
        final_risk = max(orig_risk, avg_size)
        sl = max(entry_price - final_risk, 0.05)
        final_risk = entry_price - sl
        tp = entry_price + 2.5 * final_risk

        lots = int(capital // (entry_price * lot_size))
        if lots <= 0:
            continue

        # Walk forward through real candles to find the actual exit
        exit_price = exit_time = exit_reason = None
        highest = entry_price
        for c in opt_candles[sig_idx + 1:]:
            highest = max(highest, c['high'])
            if c['low'] <= sl:
                exit_price, exit_time, exit_reason = sl, c['time'] + timedelta(minutes=3), 'SL'
                break
            if c['high'] >= tp:
                exit_price, exit_time, exit_reason = tp, c['time'] + timedelta(minutes=3), 'TP'
                break
        if exit_price is None:
            last = opt_candles[-1]
            exit_price, exit_time, exit_reason = last['close'], last['time'] + timedelta(minutes=4), 'EOD_CLOSE'

        last_exit_time = exit_time
        pnl_per_unit = exit_price - entry_price
        pnl_total = pnl_per_unit * lot_size * lots
        capital += pnl_total

        trades.append({
            'symbol': opt_symbol,
            'type': trade_type,
            'entry_price': round(entry_price, 2),
            'entry_time': entry_time,
            'exit_price': round(exit_price, 2),
            'exit_time': exit_time,
            'sl': round(sl, 2),
            'tp': round(tp, 2),
            'risk': round(final_risk, 2),
            'lots': lots,
            'highest_reached': round(min(highest, tp), 2),
            'pnl_per_unit': round(pnl_per_unit, 2),
            'pnl_total': round(pnl_total, 2),
            'exit_reason': exit_reason,
            'reason': f"Spot {'GREEN + PE GREEN' if 'PE' in trade_type else 'RED + CE GREEN'}",
            'signal_time': opt_c['time'],
            'signal_high': opt_c['high'],
            'signal_low': opt_c['low'],
            'capital_after': round(capital, 2),
        })

    return trades


def seed_day(date_str):
    """Seed the dashboard DB with data for a specific day."""
    logger.info(f"Seeding data for {date_str}...")
    
    data = get_day_data(date_str)
    if not data:
        return
    
    db = DashboardDB()
    spot_symbol = 'NSE:NIFTY50-INDEX'
    
    # Insert all candles
    logger.info(f"  Inserting {len(data['spot'])} spot candles...")
    for c in data['spot']:
        db.upsert_candle(spot_symbol, c, is_final=True)
    
    logger.info(f"  Inserting {len(data['ce'])} CE candles ({data['ce_symbol']})...")
    for c in data['ce']:
        db.upsert_candle(data['ce_symbol'], c, is_final=True)
    
    logger.info(f"  Inserting {len(data['pe'])} PE candles ({data['pe_symbol']})...")
    for c in data['pe']:
        db.upsert_candle(data['pe_symbol'], c, is_final=True)
    
    # Find signals and fabricate trades
    ce_signals = find_divergence_signals(data['spot'], data['ce'], 'CE')
    pe_signals = find_divergence_signals(data['spot'], data['pe'], 'PE')
    
    logger.info(f"  Found {len(ce_signals)} CE signals, {len(pe_signals)} PE signals")
    
    all_signals = ce_signals + pe_signals
    ce_trades = fabricate_trades(ce_signals, data['ce'], data['ce_symbol'], 'CE')
    pe_trades = fabricate_trades(pe_signals, data['pe'], data['pe_symbol'], 'PE')
    all_trades = ce_trades + pe_trades
    
    # Sort by entry time
    all_trades.sort(key=lambda t: t['entry_time'])
    
    total_pnl = 0.0
    capital = 20000.0
    for t in all_trades:
        db.insert_trade(t, t['capital_after'])
        total_pnl += t['pnl_total']
        capital = t['capital_after']
        
        # Insert events for each trade
        db.insert_event('SIGNAL', f"{t['type']} signal: {t['symbol']}", {
            'symbol': t['symbol'], 'type': t['type'],
        })
        db.insert_event('ENTRY', f"Entered {t['type']} @ ₹{t['entry_price']:.2f}", {
            'symbol': t['symbol'], 'entry': t['entry_price'],
        })
        db.insert_event('EXIT', f"Exited ({t['exit_reason']}) PnL=₹{t['pnl_total']:+.2f}", {
            'symbol': t['symbol'], 'exit_reason': t['exit_reason'], 'pnl': t['pnl_total'],
        })
    
    logger.info(f"  Inserted {len(all_trades)} trades, total PnL: ₹{total_pnl:+.2f}")
    
    # Insert state blob
    last_spot = data['spot'][-1] if data['spot'] else None
    last_ce = data['ce'][-1] if data['ce'] else None
    last_pe = data['pe'][-1] if data['pe'] else None
    
    state = {
        'heartbeat': datetime.now(IST).isoformat(),
        'ws_connected': False,
        'running_capital': capital,
        'daily_pnl': total_pnl,
        'daily_signals': len(all_signals),
        'spot_ltp': last_spot['close'] if last_spot else None,
        'ce_ltp': last_ce['close'] if last_ce else None,
        'pe_ltp': last_pe['close'] if last_pe else None,
        'ce_symbol': data['ce_symbol'],
        'pe_symbol': data['pe_symbol'],
        'active_trade': None,
        'pending_signals': [],
    }
    db.upsert_state('engine', state)
    
    db.insert_event('INFO', f"EOD — Daily PnL: ₹{total_pnl:+.2f}, Capital: ₹{capital:.2f}")
    
    logger.info(f"✅ Seeding complete for {date_str}!")


def live_sim(date_str):
    """Replay candles at ~1 per 2s, simulating live behavior."""
    logger.info(f"Starting live simulation for {date_str}...")
    
    data = get_day_data(date_str)
    if not data:
        return
    
    db = DashboardDB()
    spot_symbol = 'NSE:NIFTY50-INDEX'
    
    # Merge all candles into a timeline
    timeline = []
    for c in data['spot']:
        timeline.append(('spot', spot_symbol, c))
    for c in data['ce']:
        timeline.append(('ce', data['ce_symbol'], c))
    for c in data['pe']:
        timeline.append(('pe', data['pe_symbol'], c))
    
    # Sort by time
    timeline.sort(key=lambda x: x[2]['time'])
    
    capital = 20000.0
    total_pnl = 0.0
    
    logger.info(f"  Replaying {len(timeline)} candles...")
    
    for i, (kind, symbol, candle) in enumerate(timeline):
        # Write candle as in-progress first
        db.upsert_candle(symbol, candle, is_final=False)
        
        # Update state
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
        
        # Mark as final after a brief pause
        time.sleep(0.5)
        db.upsert_candle(symbol, candle, is_final=True)
        
        if (i + 1) % 10 == 0:
            logger.info(f"  Replayed {i+1}/{len(timeline)} candles")
        
        time.sleep(1.5)
    
    logger.info("✅ Live simulation complete!")


def main():
    global DATA_DIR, METADATA_FILE
    
    parser = argparse.ArgumentParser(description='Seed the dashboard DB from data_5min/ CSV files')
    parser.add_argument('--seed-day', type=str, help='Seed data for a specific date (YYYY-MM-DD)')
    parser.add_argument('--live-sim', type=str, help='Replay candles for live simulation (YYYY-MM-DD)')
    parser.add_argument('--seed-all', action='store_true', help='Seed data for all available dates')
    parser.add_argument('--data-dir', type=str, help='Path to data_5min/ directory (default: ../data_5min)')
    
    args = parser.parse_args()
    
    if args.data_dir:
        DATA_DIR = Path(args.data_dir).resolve()
    METADATA_FILE = DATA_DIR / 'metadata.json'
    
    if not DATA_DIR.exists():
        logger.error(f"Data directory not found: {DATA_DIR}")
        logger.info("Provide the path via --data-dir or ensure data_5min/ exists in the parent directory")
        return
    
    if args.seed_day:
        seed_day(args.seed_day)
    elif args.live_sim:
        live_sim(args.live_sim)
    elif args.seed_all:
        if METADATA_FILE.exists():
            with open(METADATA_FILE) as f:
                meta = json.load(f)
            for date_str in meta.get('dates', []):
                seed_day(date_str)
        else:
            logger.error("metadata.json not found in data_5min/")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
