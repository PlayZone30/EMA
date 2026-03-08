"""
Fetch Historical Data: Nifty 50 Spot + ATM Options (CE & PE)
============================================================
Fetches past 3 trading days of data in 1-min and 2-min timeframes.
Saves to CSV files in data/ directory.

Usage: python fetch_historical_data.py
"""

import os
import pandas as pd
import pytz
import time
import logging
from datetime import datetime, timedelta
from fyers_apiv3 import fyersModel
import dotenv

dotenv.load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')
API_DELAY = 0.5  # Delay between API calls to avoid rate limiting

# Market holidays 2026 (update as needed)
HOLIDAYS_2026 = [
    datetime(2026, 1, 26),   # Republic Day
    datetime(2026, 3, 10),   # Maha Shivaratri (example)
    datetime(2026, 3, 17),   # Holi
    datetime(2026, 3, 30),   # Id-Ul-Fitr (example)
    datetime(2026, 4, 2),    # Mahavir Jayanti
    datetime(2026, 4, 3),    # Good Friday
    datetime(2026, 4, 14),   # Ambedkar Jayanti
    datetime(2026, 5, 1),    # Maharashtra Day
    datetime(2026, 8, 15),   # Independence Day
    datetime(2026, 10, 2),   # Gandhi Jayanti
    datetime(2026, 10, 20),  # Dussehra
    datetime(2026, 11, 9),   # Diwali
    datetime(2026, 12, 25),  # Christmas
]


def is_trading_day(date):
    """Check if date is a trading day (not weekend, not holiday)."""
    if date.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    for h in HOLIDAYS_2026:
        if h.date() == date:
            return False
    return True


def get_last_n_trading_days(n, from_date=None):
    """Get the last N trading days before from_date."""
    if from_date is None:
        from_date = datetime.now(IST).date()
    
    trading_days = []
    current = from_date - timedelta(days=1)  # Start from yesterday
    
    while len(trading_days) < n:
        if is_trading_day(current):
            trading_days.append(current)
        current -= timedelta(days=1)
    
    trading_days.reverse()  # Oldest first
    return trading_days


def authenticate():
    """Authenticate with Fyers API and return fyers model object."""
    from main import FyersAuthenticator
    
    CLIENT_ID = os.getenv("CLIENT_ID")
    SECRET_KEY = os.getenv("SECRET_KEY")
    USERNAME = os.getenv("USERNAME")
    PIN = os.getenv("PIN")
    TOTP_KEY = os.getenv("TOTP_KEY")
    
    if not all([CLIENT_ID, SECRET_KEY, USERNAME, PIN, TOTP_KEY]):
        logger.error("Missing environment variables! Check .env file.")
        return None, None
    
    auth = FyersAuthenticator(CLIENT_ID, SECRET_KEY, "https://www.google.com", USERNAME, PIN, TOTP_KEY)
    token, err = auth.get_access_token()
    
    if not token:
        logger.error(f"Authentication failed: {err}")
        return None, None
    
    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=token, log_path="")
    logger.info("Authentication successful!")
    return fyers, CLIENT_ID


def fetch_candle_data(fyers, symbol, resolution, start_date, end_date):
    """
    Fetch historical candle data from Fyers API.
    
    Args:
        fyers: FyersModel instance
        symbol: e.g. "NSE:NIFTY50-INDEX"
        resolution: "1" for 1-min, "2" for 2-min, "5" for 5-min
        start_date: "YYYY-MM-DD"
        end_date: "YYYY-MM-DD"
    
    Returns:
        DataFrame with columns: datetime, open, high, low, close, volume
    """
    time.sleep(API_DELAY)
    
    data = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": start_date,
        "range_to": end_date,
        "cont_flag": "0"
    }
    
    response = fyers.history(data)
    
    if response.get("code") != 200 or "candles" not in response:
        logger.error(f"Error fetching {symbol} (res={resolution}): {response}")
        return pd.DataFrame()
    
    df = pd.DataFrame(response['candles'], columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['datetime'], unit='s')
    df['datetime'] = df['datetime'].dt.tz_localize(pytz.UTC).dt.tz_convert(IST)
    
    logger.info(f"  Fetched {len(df)} candles for {symbol} (res={resolution}, {start_date} to {end_date})")
    return df


def get_nearest_expiry(fyers):
    """Get nearest expiry timestamp for NIFTY options."""
    time.sleep(API_DELAY)
    data = {"symbol": "NSE:NIFTY50-INDEX", "strikecount": 5, "timestamp": ""}
    response = fyers.optionchain(data=data)
    
    if response.get("code") == 200 and "data" in response and "expiryData" in response["data"]:
        expiry_data = response["data"]["expiryData"]
        if expiry_data:
            return str(expiry_data[0]["expiry"])
    
    logger.error(f"Failed to get nearest expiry: {response}")
    return None


def find_atm_options(fyers, spot_price):
    """
    Find ATM CE and PE option symbols for NIFTY.
    
    Args:
        fyers: FyersModel instance
        spot_price: Current/approximate spot price
    
    Returns:
        (atm_strike, ce_symbol, pe_symbol)
    """
    time.sleep(API_DELAY)
    
    nearest_expiry = get_nearest_expiry(fyers)
    if not nearest_expiry:
        return None, None, None
    
    time.sleep(API_DELAY)
    data = {"symbol": "NSE:NIFTY50-INDEX", "strikecount": 30, "timestamp": nearest_expiry}
    response = fyers.optionchain(data=data)
    
    if response.get("code") != 200 or "data" not in response or "optionsChain" not in response["data"]:
        logger.error(f"Failed to get option chain: {response}")
        return None, None, None
    
    options = response["data"]["optionsChain"]
    
    # Get unique strikes
    strikes = list(set(
        opt["strike_price"] for opt in options 
        if "strike_price" in opt and opt["strike_price"] > 0
    ))
    strikes.sort()
    
    if not strikes:
        logger.error("No strikes found in option chain")
        return None, None, None
    
    # Find closest ATM strike
    atm_strike = min(strikes, key=lambda x: abs(x - spot_price))
    
    # Find CE and PE symbols for ATM strike
    ce_symbol = next(
        (opt["symbol"] for opt in options 
         if opt.get("option_type") == "CE" and opt.get("strike_price") == atm_strike), 
        None
    )
    pe_symbol = next(
        (opt["symbol"] for opt in options 
         if opt.get("option_type") == "PE" and opt.get("strike_price") == atm_strike), 
        None
    )
    
    return atm_strike, ce_symbol, pe_symbol


def get_spot_price(fyers):
    """Get current NIFTY 50 spot price."""
    time.sleep(API_DELAY)
    data = {"symbols": "NSE:NIFTY50-INDEX"}
    response = fyers.quotes(data)
    
    if response.get("code") == 200 and "d" in response and len(response["d"]) > 0:
        return response["d"][0]["v"]["lp"]
    
    logger.error(f"Failed to get spot price: {response}")
    return None


def save_to_csv(df, filepath):
    """Save DataFrame to CSV file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    df.to_csv(filepath, index=False)
    logger.info(f"  Saved: {filepath} ({len(df)} rows)")


def main():
    logger.info("=" * 60)
    logger.info("HISTORICAL DATA FETCHER")
    logger.info("Nifty 50 Spot + ATM Options (CE & PE)")
    logger.info("Timeframes: 1-min, 2-min | Past 3 Trading Days")
    logger.info("=" * 60)
    
    # Step 1: Authenticate
    fyers, client_id = authenticate()
    if not fyers:
        return
    
    # Step 2: Get trading days
    trading_days = get_last_n_trading_days(3)
    logger.info(f"\nTrading days to fetch: {[d.strftime('%Y-%m-%d (%A)') for d in trading_days]}")
    
    # Step 3: Get current spot price and find ATM options
    spot_price = get_spot_price(fyers)
    if not spot_price:
        logger.error("Cannot determine spot price. Exiting.")
        return
    
    logger.info(f"\nCurrent Nifty 50 spot price: ₹{spot_price:.2f}")
    
    atm_strike, ce_symbol, pe_symbol = find_atm_options(fyers, spot_price)
    if not ce_symbol or not pe_symbol:
        logger.error("Cannot find ATM options. Exiting.")
        return
    
    logger.info(f"ATM Strike: {atm_strike}")
    logger.info(f"CE Symbol: {ce_symbol}")
    logger.info(f"PE Symbol: {pe_symbol}")
    
    # Step 4: Fetch data for each trading day
    resolutions = [("1", "1min"), ("2", "2min")]
    spot_symbol = "NSE:NIFTY50-INDEX"
    
    all_data = {}  # Store for summary
    
    for day in trading_days:
        date_str = day.strftime("%Y-%m-%d")
        logger.info(f"\n{'='*40}")
        logger.info(f"Fetching data for: {date_str} ({day.strftime('%A')})")
        logger.info(f"{'='*40}")
        
        for res, res_label in resolutions:
            # Fetch spot data
            logger.info(f"\n  [{res_label}] Nifty 50 Spot...")
            spot_df = fetch_candle_data(fyers, spot_symbol, res, date_str, date_str)
            if not spot_df.empty:
                save_to_csv(spot_df, f"data/nifty_spot_{res_label}_{date_str}.csv")
                all_data[f"spot_{res_label}_{date_str}"] = len(spot_df)
            
            # Fetch CE options data
            logger.info(f"  [{res_label}] CE Option ({ce_symbol})...")
            ce_df = fetch_candle_data(fyers, ce_symbol, res, date_str, date_str)
            if not ce_df.empty:
                save_to_csv(ce_df, f"data/nifty_ce_{res_label}_{date_str}.csv")
                all_data[f"ce_{res_label}_{date_str}"] = len(ce_df)
            
            # Fetch PE options data
            logger.info(f"  [{res_label}] PE Option ({pe_symbol})...")
            pe_df = fetch_candle_data(fyers, pe_symbol, res, date_str, date_str)
            if not pe_df.empty:
                save_to_csv(pe_df, f"data/nifty_pe_{res_label}_{date_str}.csv")
                all_data[f"pe_{res_label}_{date_str}"] = len(pe_df)
    
    # Step 5: Summary
    logger.info(f"\n{'='*60}")
    logger.info("FETCH COMPLETE — SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"Trading Days: {[d.strftime('%Y-%m-%d') for d in trading_days]}")
    logger.info(f"ATM Strike: {atm_strike} | CE: {ce_symbol} | PE: {pe_symbol}")
    logger.info(f"Files saved to: data/")
    
    total_files = 0
    total_candles = 0
    for key, count in all_data.items():
        total_files += 1
        total_candles += count
    
    logger.info(f"Total files: {total_files}")
    logger.info(f"Total candles: {total_candles}")
    logger.info(f"{'='*60}")
    
    # Save metadata
    metadata = {
        "fetch_time": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "trading_days": [d.strftime("%Y-%m-%d") for d in trading_days],
        "spot_symbol": spot_symbol,
        "atm_strike": atm_strike,
        "ce_symbol": ce_symbol,
        "pe_symbol": pe_symbol,
        "resolutions": ["1min", "2min"],
    }
    
    import json
    with open("data/metadata.json", "w") as f:
        json.dump(metadata, f, indent=4)
    logger.info("Metadata saved to: data/metadata.json")


if __name__ == "__main__":
    main()
