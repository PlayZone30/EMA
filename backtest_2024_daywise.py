"""
2024 Daywise 5-Min Divergence Backtest Engine
=============================================
Iterates through all trading days in the `2024_daywise` folder.
Needs `2024_daywise_spot` folder populated with Nifty 50 spot data.

Strategy Rules:
1. Signal: (Spot 5m Green & PE 5m Green) -> PE Buy
           (Spot 5m Red & CE 5m Green) -> CE Buy
2. Entry & SL Cases (Triggered by Option price breaking Signal High):
    - Case 1: Next candle breaks High first. (SL = Signal Low)
    - Case 2: Next candle breaks Low, then breaks High. (SL = Breakout Candle Low)
    - Case 3: Next candle breaks neither. 2nd candle breaks High. (SL = Signal Low)
    - Case 4: Invalid (breaks low, never recovers).
3. Take Profit:
    - Exactly 1:3 Risk:Reward
    - Risk = max(Entry - SL_orig, Average_Candle_Size_Until_Signal)
    - TP = Entry + (3 * Risk)
4. Resolution:
    - Overlapping Trades: Ignored. We will stay out of trades if already in one.
    - Check forward candle by candle.
        If candle Low <= SL and candle High >= TP: Assume SL hit first (pessimistic).
        Except: Case 2 entry candle (assumed valid entry AFTER low was made).
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime
import csv

BASE_OPTIONS_DIR = "/Users/pavanreddy/EMA/2024_daywise"
BASE_SPOT_DIR = "/Users/pavanreddy/EMA/2024_daywise_spot"
OUT_CSV = "backtest_2024_results.csv"

def parse_day_folder(folder_name):
    """Parse '01APR24' into a standard 'YYYY-MM-DD' date string for spot lookup."""
    try:
        dt = datetime.strptime(folder_name, "%d%b%y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None

def load_data(date_str, folder_path):
    """Loads Spot, CE, and PE DataFrames for a given day."""
    spot_file = os.path.join(BASE_SPOT_DIR, f"nifty_spot_5min_{date_str}.csv")
    if not os.path.exists(spot_file):
        return None, None, None
    
    spot_df = pd.read_csv(spot_file)
    spot_df.set_index('datetime', inplace=True)
    
    ce_df, pe_df = None, None
    for f in os.listdir(folder_path):
        if not f.endswith(".csv"): continue
        full_path = os.path.join(folder_path, f)
        if "CE" in f:
            ce_df = pd.read_csv(full_path)
            ce_df['symbol'] = f.replace(".csv", "")
            ce_df.set_index('datetime', inplace=True)
        elif "PE" in f:
            pe_df = pd.read_csv(full_path)
            pe_df['symbol'] = f.replace(".csv", "")
            pe_df.set_index('datetime', inplace=True)
            
    return spot_df, ce_df, pe_df

def process_day(date_str, spot_df, ce_df, pe_df, trade_log):
    """Scans one single day for signals and resolves trades."""
    times = sorted(list(set(spot_df.index)))
    pending_signals = [] 
    
    for t_idx, t in enumerate(times):
        if t not in spot_df.index: continue
        
        spot = spot_df.loc[t]
        spot_is_green = spot['close'] > spot['open']
        spot_is_red = spot['close'] < spot['open']
        
        if ce_df is not None and t in ce_df.index:
            ce = ce_df.loc[t]
            ce_is_green = ce['close'] > ce['open']
            if spot_is_red and ce_is_green:
                opt_idx = ce_df.index.get_loc(t)
                pending_signals.append({
                    'time': t, 'type': 'CE_BUY', 'symbol': ce['symbol'],
                    'df': ce_df, 'sig_high': ce['high'], 'sig_low': ce['low'],
                    'sig_idx': opt_idx, 'resolve_start_idx': opt_idx + 1
                })
                
        if pe_df is not None and t in pe_df.index:
            pe = pe_df.loc[t]
            pe_is_green = pe['close'] > pe['open']
            if spot_is_green and pe_is_green:
                opt_idx = pe_df.index.get_loc(t)
                pending_signals.append({
                    'time': t, 'type': 'PE_BUY', 'symbol': pe['symbol'],
                    'df': pe_df, 'sig_high': pe['high'], 'sig_low': pe['low'],
                    'sig_idx': opt_idx, 'resolve_start_idx': opt_idx + 1
                })

    # Sort signals chronologically to handle overlapping trades properly
    pending_signals.sort(key=lambda x: x['time'])
    
    in_trade = False
    trade_end_time = None

    for sig in pending_signals:
        # Prevent overlapping trades. If we are in a trade, skip signals until we exit.
        if in_trade and trade_end_time is not None and sig['time'] < trade_end_time:
            continue
            
        # No trades before 09:30
        if sig['time'] < "09:30":
            continue
            
        df = sig['df']
        idx = sig['resolve_start_idx']
        
        if idx >= len(df): continue 
        
        # Look ahead exactly 1 candle (since we only care about Case 1 now)
        c1 = df.iloc[idx] if idx < len(df) else None
        
        # Buffer of 1 point for entry and SL
        buffered_entry_price = sig['sig_high'] 
        buffered_sl_price = sig['sig_low'] 
        
        entry_price = buffered_entry_price
        sl_orig = None
        case_name = None
        trade_start_idx = None
        entry_time = None
        entry_candle = None
        
        broke_low_c2 = False 
        
        if c1 is not None:
            broke_high_c1 = c1['high'] >= buffered_entry_price
            broke_low_c1 = c1['low'] <= buffered_sl_price
            
            if broke_high_c1 and not broke_low_c1:
                case_name = "Case 1"
                sl_orig = buffered_sl_price
                trade_start_idx = idx
                entry_candle = c1
                entry_time = c1.name
            
        if not case_name:
            continue
            
        # Prevent entry if the entry time itself is during a previous trade
        if in_trade and trade_end_time is not None and entry_time < trade_end_time:
            continue
            
        # Dynamic Stop Loss based on Average Candle Size
        past_candles = df.iloc[:sig['sig_idx']+1]
        if len(past_candles) > 0:
            avg_candle_size = (past_candles['high'] - past_candles['low']).mean()
        else:
            avg_candle_size = 0
            
        orig_risk = entry_price - sl_orig
        if orig_risk <= 0: continue
        
        # Take the min of Original Risk vs Average Candle Size
        final_risk = max(orig_risk, avg_candle_size)
        
        final_sl = entry_price - final_risk
        tp = entry_price + (2.5 * final_risk)
        
        outcome = None
        exit_price = None
        exit_time = None
        
        for i in range(trade_start_idx, len(df)):
            curr = df.iloc[i]
            
            if i == trade_start_idx and (case_name == "Case 2" or (case_name == "Case 3" and broke_low_c2)):
                hit_sl = False
                hit_tp = curr['high'] >= tp
            else:
                hit_sl = curr['low'] <= final_sl
                hit_tp = curr['high'] >= tp
            
            if hit_sl and hit_tp:
                outcome = "LOSS"
                exit_price = final_sl
                exit_time = curr.name
                break
            elif hit_sl:
                outcome = "LOSS"
                exit_price = final_sl
                exit_time = curr.name
                break
            elif hit_tp:
                outcome = "PROFIT"
                exit_price = tp
                exit_time = curr.name
                break
                
        if not outcome:
            last = df.iloc[-1]
            exit_price = last['close']
            exit_time = last.name
            outcome = "EOD"
            
        pnl = exit_price - entry_price
        
        trade_log.append({
            'Date': date_str,
            'Symbol': sig['symbol'],
            'Type': sig['type'],
            'Case': case_name,
            'Signal_Time': sig['time'],
            'Signal_High': round(sig['sig_high'], 2),
            'Signal_Low': round(sig['sig_low'], 2),
            'Entry_Time': entry_time,
            'Entry_Candle_High': round(entry_candle['high'], 2),
            'Entry_Candle_Low': round(entry_candle['low'], 2),
            'Entry_Price': round(entry_price, 2),
            'SL_Orig': round(sl_orig, 2),
            'Avg_Candle_Size': round(avg_candle_size, 2),
            'SL': round(final_sl, 2),
            'TP': round(tp, 2),
            'Risk': round(final_risk, 2),
            'Exit_Time': exit_time,
            'Exit_Price': round(exit_price, 2),
            'PnL': round(pnl, 2),
            'Outcome': outcome
        })
        
        in_trade = True
        trade_end_time = exit_time

def main():
    print(f"Starting 2024 Daywise Backtest...")
    
    if not os.path.exists(BASE_OPTIONS_DIR):
        print(f"Options dir not found: {BASE_OPTIONS_DIR}")
        return
        
    folders = [f for f in os.listdir(BASE_OPTIONS_DIR) if os.path.isdir(os.path.join(BASE_OPTIONS_DIR, f))]
    print(f"Found {len(folders)} day folders.")
    
    trade_log = []
    
    for folder in sorted(folders):
        date_str = parse_day_folder(folder)
        if not date_str: continue
        
        spot_df, ce_df, pe_df = load_data(date_str, os.path.join(BASE_OPTIONS_DIR, folder))
        
        if spot_df is None:
            print(f"Skipping {date_str} - No Spot data found.")
            continue
            
        process_day(date_str, spot_df, ce_df, pe_df, trade_log)
        
    if not trade_log:
        print("No trades found!")
        return
        
    df_res = pd.DataFrame(trade_log)
    df_res.to_csv(OUT_CSV, index=False)
    
    print("\n" + "="*50)
    print("BACKTEST RESULTS (2024 Year)")
    print("="*50)
    print(f"Total Trades : {len(df_res)}")
    
    wins = df_res[df_res['Outcome'] == 'PROFIT']
    losses = df_res[df_res['Outcome'] == 'LOSS']
    
    win_rate = len(wins) / len(df_res) * 100
    total_pts = df_res['PnL'].sum()
    
    print(f"Wins         : {len(wins)}")
    print(f"Losses       : {len(losses)}")
    print(f"EOD Closes   : {len(df_res[df_res['Outcome'] == 'EOD'])}")
    print(f"Win Rate     : {win_rate:.2f}%")
    print(f"Total PnL    : {total_pts:.2f} points")
    print("-" * 50)
    print("Breakdown by Case:")
    for c in sorted(df_res['Case'].unique()):
        sub = df_res[df_res['Case'] == c]
        sub_pts = sub['PnL'].sum()
        sub_wins = len(sub[sub['Outcome'] == 'PROFIT'])
        sub_wr = sub_wins / len(sub) * 100
        print(f"  {c}: {len(sub)} trades | {sub_wr:.1f}% WR | {sub_pts:.2f} pts")
    print("="*50)
    print(f"Saved logs to {OUT_CSV}")

if __name__ == "__main__":
    main()
