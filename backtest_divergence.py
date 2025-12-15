import pandas as pd
import pytz
from datetime import datetime, timedelta
from fyers_apiv3 import fyersModel
import os
import logging
from divergence_strategy import DivergenceStrategy

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock Fyers Object for Backtesting
class MockFyers:
    def optionchain(self, data):
        # Mock response for option chain if needed
        # For backtest, we might manually set symbols
        return {'code': 200, 'data': {'expiryData': [{'expiry': 'mock_expiry'}], 'optionsChain': []}}

def fetch_data(fyers, symbol, start, end):
    """Fetch historical data using the user's provided method."""
    data = {
        "symbol": symbol,
        "resolution": "5",  # 5 minute for strategy
        "date_format": "1",
        "range_from": start,
        "range_to": end,
        "cont_flag": "0"
    }
    response = fyers.history(data)
    if response["code"] != 200 or "candles" not in response:
        print(f"Error fetching data for {symbol}: {response}")
        return pd.DataFrame()
    df = pd.DataFrame(response['candles'], columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['datetime'], unit='s')
    utc = pytz.UTC
    ist = pytz.timezone('Asia/Kolkata')
    df['datetime'] = df['datetime'].dt.tz_localize(utc).dt.tz_convert(ist)
    # df.set_index('datetime', inplace=True) # Keep datetime as column for iteration
    return df

def run_backtest():
    # Initialize Fyers (Need real credentials for history)
    client_id = os.getenv("CLIENT_ID")
    access_token = "YOUR_ACCESS_TOKEN" # User needs to provide this or we use what's available
    
    # NOTE: We need a valid fyers object to fetch history. 
    # Since we are in an agent environment, we might not have a valid token unless we reuse the one from main.py logic.
    # For this script, we'll assume the user runs it with their setup or we try to authenticate if possible.
    # However, to keep it simple for the user to "test", we'll use the existing main.py authentication logic if needed,
    # or just ask the user to run it.
    
    # For now, let's assume we have a fyers object. 
    # We will import the authenticator from main to get a token if possible.
    from main import FyersAuthenticator
    
    CLIENT_ID = os.getenv("CLIENT_ID")
    SECRET_KEY = os.getenv("SECRET_KEY")
    USERNAME = os.getenv("USERNAME")
    PIN = os.getenv("PIN")
    TOTP_KEY = os.getenv("TOTP_KEY")
    
    if not all([CLIENT_ID, SECRET_KEY, USERNAME, PIN, TOTP_KEY]):
        logger.error("Env vars missing")
        return

    auth = FyersAuthenticator(CLIENT_ID, SECRET_KEY, "https://www.google.com", USERNAME, PIN, TOTP_KEY)
    token, err = auth.get_access_token()
    
    if not token:
        logger.error(f"Auth failed: {err}")
        return
        
    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=token, log_path="")
    
    # Initialize Strategy
    strategy = DivergenceStrategy(fyers, log_file="backtest_results.csv")
    
    # Date to test
    test_date = "2025-11-24" # Friday
    
    logger.info(f"Fetching data for {test_date}...")
    
    # 1. Fetch Spot Data (5-min for Signal)
    spot_symbol = "NSE:NIFTY50-INDEX"
    spot_df = fetch_data(fyers, spot_symbol, test_date, test_date)
    
    if spot_df.empty:
        logger.error("No spot data found")
        return
        
    logger.info(f"Fetched {len(spot_df)} spot candles")
    
    # 2. Use User-Provided Symbols
    ce_symbol = "NSE:NIFTY25NOV25950CE"
    pe_symbol = "NSE:NIFTY25NOV25950PE"
    
    logger.info(f"Testing with symbols: CE={ce_symbol}, PE={pe_symbol}")
    
    # Manually set current symbols in strategy
    strategy.current_ce_symbol = ce_symbol
    strategy.current_pe_symbol = pe_symbol
    
    # Fetch Real Data for Options (1-min for Execution)
    logger.info("Fetching real option data (1-min)...")
    
    def fetch_1min_data(fyers, symbol, start, end):
        data = {
            "symbol": symbol,
            "resolution": "1",
            "date_format": "1",
            "range_from": start,
            "range_to": end,
            "cont_flag": "0"
        }
        response = fyers.history(data)
        if response["code"] != 200 or "candles" not in response:
            print(f"Error fetching data for {symbol}: {response}")
            return pd.DataFrame()
        df = pd.DataFrame(response['candles'], columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['datetime'], unit='s')
        utc = pytz.UTC
        ist = pytz.timezone('Asia/Kolkata')
        df['datetime'] = df['datetime'].dt.tz_localize(utc).dt.tz_convert(ist)
        return df

    ce_df_1min = fetch_1min_data(fyers, ce_symbol, test_date, test_date)
    pe_df_1min = fetch_1min_data(fyers, pe_symbol, test_date, test_date)
    
    if ce_df_1min.empty or pe_df_1min.empty:
        logger.error("Failed to fetch 1-min option data. Cannot proceed with real backtest.")
        return

    # Resample 1-min to 5-min for Signal Generation
    def resample_to_5min(df):
        df_resampled = df.resample('5min', on='datetime').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last'
        }).dropna()
        return df_resampled

    ce_df_5min = resample_to_5min(ce_df_1min)
    pe_df_5min = resample_to_5min(pe_df_1min)
    
    # Align 5-min data for Signals
    spot_df.set_index('datetime', inplace=True)
    
    # Merge 5-min data for easy access during iteration
    # We iterate 1-min data, but need to know which 5-min candle just finished
    
    # Strategy:
    # Iterate through 1-min timestamps.
    # At each step, update LTP (Open, Low, High, Close) for execution.
    # If the 1-min timestamp corresponds to the END of a 5-min block (e.g. 09:19, 09:24...),
    # OR if we just check if a 5-min candle exists for the current time bucket.
    
    # Actually, simpler:
    # Iterate 1-min data.
    # Keep track of "current 5-min bucket".
    # When 1-min time crosses to new 5-min bucket, process the PREVIOUS 5-min candle.
    
    # Let's merge 1-min CE and PE
    ce_df_1min = ce_df_1min.add_suffix('_ce')
    pe_df_1min = pe_df_1min.add_suffix('_pe')
    ce_df_1min.set_index('datetime_ce', inplace=True)
    pe_df_1min.set_index('datetime_pe', inplace=True)
    
    merged_1min = ce_df_1min.join(pe_df_1min, how='outer').sort_index()
    
    logger.info(f"Aligned {len(merged_1min)} 1-min candles for execution")
    logger.info("Starting Backtest Feed...")
    
    current_5min_bucket = None
    
    for timestamp, row in merged_1min.iterrows():
        # Determine 5-min bucket for this timestamp
        # 09:15 -> 09:15 bucket
        # 09:19 -> 09:15 bucket
        # 09:20 -> 09:20 bucket
        bucket_time = timestamp.floor('5min')
        
        # Check if we moved to a new bucket
        if current_5min_bucket is not None and bucket_time > current_5min_bucket:
            # Process the COMPLETED 5-min candle for the PREVIOUS bucket
            # We fetch it from our pre-calculated 5-min DFs
            prev_bucket = current_5min_bucket
            
            # Spot Candle
            if prev_bucket in spot_df.index:
                spot_row = spot_df.loc[prev_bucket]
                if isinstance(spot_row, pd.DataFrame):
                    spot_row = spot_row.iloc[0]
                spot_candle = {
                    'time': prev_bucket,
                    'open': float(spot_row['open']),
                    'high': float(spot_row['high']),
                    'low': float(spot_row['low']),
                    'close': float(spot_row['close'])
                }
                strategy.process_candle(spot_symbol, spot_candle)
            
            # CE Candle
            if prev_bucket in ce_df_5min.index:
                ce_row = ce_df_5min.loc[prev_bucket]
                if isinstance(ce_row, pd.DataFrame):
                    ce_row = ce_row.iloc[0]
                ce_candle = {
                    'time': prev_bucket,
                    'open': float(ce_row['open']),
                    'high': float(ce_row['high']),
                    'low': float(ce_row['low']),
                    'close': float(ce_row['close'])
                }
                strategy.process_candle(ce_symbol, ce_candle)
                
            # PE Candle
            if prev_bucket in pe_df_5min.index:
                pe_row = pe_df_5min.loc[prev_bucket]
                if isinstance(pe_row, pd.DataFrame):
                    pe_row = pe_row.iloc[0]
                pe_candle = {
                    'time': prev_bucket,
                    'open': float(pe_row['open']),
                    'high': float(pe_row['high']),
                    'low': float(pe_row['low']),
                    'close': float(pe_row['close'])
                }
                strategy.process_candle(pe_symbol, pe_candle)
                
        current_5min_bucket = bucket_time
        
        # --- Execution Logic (1-min Ticks) ---
        
        # Update CE
        if not pd.isna(row['open_ce']):
            # Simulate Ticks: Open -> Low -> High -> Close
            strategy.update_ltp(ce_symbol, row['open_ce'], timestamp)
            strategy.update_ltp(ce_symbol, row['low_ce'], timestamp)
            
            # Check for Breakout Tick (Interpolation)
            # We do this manually here because update_ltp just checks against current price.
            # But if price jumped from Low to High, we might miss the exact trigger level if we just send High.
            # So we check if pending signal exists and if High crossed it.
            if ce_symbol in strategy.pending_signals:
                signal = strategy.pending_signals[ce_symbol]
                if signal['type'] == 'BUY': # CE Buy
                    sig_high = signal['high']
                    if row['low_ce'] <= sig_high < row['high_ce']:
                         # Inject precise tick
                         strategy.update_ltp(ce_symbol, sig_high + 0.05, timestamp)
            
            strategy.update_ltp(ce_symbol, row['high_ce'], timestamp)
            strategy.update_ltp(ce_symbol, row['close_ce'], timestamp)
            
        # Update PE
        if not pd.isna(row['open_pe']):
            strategy.update_ltp(pe_symbol, row['open_pe'], timestamp)
            strategy.update_ltp(pe_symbol, row['low_pe'], timestamp)
            
            if pe_symbol in strategy.pending_signals:
                signal = strategy.pending_signals[pe_symbol]
                if signal['type'] == 'BUY': # PE Buy
                    sig_high = signal['high']
                    if row['low_pe'] <= sig_high < row['high_pe']:
                         strategy.update_ltp(pe_symbol, sig_high + 0.05, timestamp)
            
            strategy.update_ltp(pe_symbol, row['high_pe'], timestamp)
            strategy.update_ltp(pe_symbol, row['close_pe'], timestamp)

    logger.info("Backtest completed. Check backtest_results.csv")

if __name__ == "__main__":
    run_backtest()
