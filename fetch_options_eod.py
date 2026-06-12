"""
Options Data Downloader
====================================
Runs manually to download 1-min options data for the nearest expiry.

What it does:
  1. Authenticates with Fyers API
  2. Queries option chain for NIFTY to get the nearest expiry date
  3. Gets all available CE and PE strikes for that expiry
  4. Downloads 1-min OHLCV data for today's date
  5. Saves them to:  <month>/<expiry_day>/<current_date>/CE/<symbol>.csv
                    <month>/<expiry_day>/<current_date>/PE/<symbol>.csv

Usage:
  python fetch_options_eod.py              # downloads today
  python fetch_options_eod.py 2026-03-25  # downloads specific date
"""

import os
import sys
import time
import logging
import dotenv
import pytz
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore
from fyers_apiv3 import fyersModel

dotenv.load_dotenv()

# ──────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────
IST          = pytz.timezone("Asia/Kolkata")
SPOT_SYMBOL  = "NSE:NIFTY50-INDEX"
RESOLUTION   = "1"           # 1-minute candles
MAX_WORKERS  = 5             # parallel threads (reduced for stability)
RATE_DELAY   = 0.5           # seconds between requests (increased to avoid 429)

# SET A DATE HERE to fetch for a specific day (Format: "YYYY-MM-DD")
# If None, it defaults to today. Alternatively, pass it via command line: 
# python fetch_options_eod.py 2026-03-25
TARGET_DATE  = "2026-05-15"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("fetch_options_eod.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("EODFetcher")
_sem = Semaphore(MAX_WORKERS)


# ──────────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────────
def authenticate():
    from main import FyersAuthenticator
    cid  = os.getenv("CLIENT_ID")
    sk   = os.getenv("SECRET_KEY")
    user = os.getenv("USERNAME")
    pin  = os.getenv("PIN")
    totp = os.getenv("TOTP_KEY")
    if not all([cid, sk, user, pin, totp]):
        logger.error("❌ Missing .env variables!")
        return None, None
    auth = FyersAuthenticator(cid, sk, "https://www.google.com", user, pin, totp)
    token, err = auth.get_access_token()
    if not token:
        logger.error(f"❌ Auth failed: {err}")
        return None, None
    fyers = fyersModel.FyersModel(client_id=cid, token=token, log_path="")
    logger.info("✅ Authentication successful!")
    return fyers, cid


# ──────────────────────────────────────────────
# SPOT PRICE - for ATM filtering
# ──────────────────────────────────────────────
def get_nifty_spot(fyers, date_str=None):
    """
    Fetch NIFTY 50 spot price. 
    If date_str is in the past, it attempts to get the Close price of that day
    for better ATM strike calculation.
    """
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    
    # If fetching for a past date, get that day's closing price
    if date_str and date_str != today_str:
        logger.info(f"📊 Fetching historical spot price for {SPOT_SYMBOL} on {date_str}...")
        resp = fyers.history({
            "symbol":      SPOT_SYMBOL,
            "resolution":  "D",
            "date_format": "1",
            "range_from":  date_str,
            "range_to":    date_str,
            "cont_flag":   "0"
        })
        if resp.get("code") == 200 and resp.get("candles"):
            close_price = resp["candles"][0][4]
            logger.info(f"  Historical Close on {date_str}: {close_price}")
            return float(close_price)
        else:
            logger.warning(f"  ⚠️ Could not fetch historical spot for {date_str}. Using current price as fallback.")

    logger.info(f"📊 Fetching current spot price for {SPOT_SYMBOL}...")
    resp = fyers.quotes({"symbols": SPOT_SYMBOL})
    if resp.get("code") != 200 or "d" not in resp:
        logger.error(f"Failed to fetch spot price: {resp}")
        return None
    
    lp = resp["d"][0].get("v", {}).get("lp")
    if lp is None:
        logger.error("Spot price not found in quotes response.")
        return None
    
    logger.info(f"  Current Spot: {lp}")
    return float(lp)


# ──────────────────────────────────────────────
# OPTION CHAIN — get all strikes and expiry
# ──────────────────────────────────────────────
def get_nearest_expiry_and_symbols(fyers, atm_strike=None):
    """
    Fetch option chain for nearest expiry with strikecount=50 (max).
    Returns (expiry_date_str, ce_symbols, pe_symbols)
    """
    import re
    logger.info("📋 Fetching option chain (nearest expiry)...")
    time.sleep(1.0)
    
    # Send empty timestamp to get the base list of expiries
    resp = fyers.optionchain({"symbol": SPOT_SYMBOL, "strikecount": 5, "timestamp": ""})
    if resp.get("code") != 200 or "data" not in resp:
        logger.error(f"Option chain error (expiry fetch): {resp}")
        return None, [], []

    expiry_list = resp["data"].get("expiryData", [])
    if not expiry_list:
        logger.error("No expiry contracts found. Is the market closed/clearing cache?")
        return None, [], []

    nearest_expiry_ts = str(expiry_list[0]["expiry"])
    nearest_expiry_date = expiry_list[0].get("date", "")  # format: '30-03-2026'
    logger.info(f"  Nearest expiry: {nearest_expiry_date}")

    # Now fetch with max strikecount to get all symbols
    time.sleep(1.0)
    resp2 = fyers.optionchain({
        "symbol":      SPOT_SYMBOL,
        "strikecount": 50,          # maximum — gets widest possible chain
        "timestamp":   nearest_expiry_ts
    })
    
    if resp2.get("code") != 200:
        logger.error(f"Option chain error (full chain): {resp2}")
        return nearest_expiry_date, [], []

    chain = resp2["data"]["optionsChain"]
    ce_symbols, pe_symbols = [], []
    
    # Range filter: ATM ± 1000 points (20 strikes * 50)
    lower_bound = (atm_strike - 1000) if atm_strike else 0
    upper_bound = (atm_strike + 1000) if atm_strike else 999999
    
    if atm_strike:
        logger.info(f"  Filtering strikes between {lower_bound} and {upper_bound} (ATM ± 1000)")

    for opt in chain:
        sym = opt.get("symbol", "")
        opt_type = opt.get("option_type", "")
        
        # Skip invalid entries or the spot index itself which has no option_type
        if not sym or not opt_type:
            continue
            
        strike_val = opt.get("strike_price")
        if strike_val is not None:
            if not (lower_bound <= strike_val <= upper_bound):
                continue

        if opt_type == "CE":
            ce_symbols.append(sym)
        elif opt_type == "PE":
            pe_symbols.append(sym)

    logger.info(f"  Found {len(ce_symbols)} CE strikes, {len(pe_symbols)} PE strikes in range")
    return nearest_expiry_date, ce_symbols, pe_symbols


# ──────────────────────────────────────────────
# FOLDER STRUCTURE LOGIC
# PATH: <month>/<expiry_day>/<current_date>/<CE_or_PE>
# Example: march/30/25/CE/
# ──────────────────────────────────────────────
def build_directories(date_str, expiry_date_str):
    # expiry_date_str format: '30-03-2026' or '07-04-2026'
    # date_str format: '2026-03-25'
    
    # 1. month string from expiry date (e.g. "march")
    exp_dt = datetime.strptime(expiry_date_str, "%d-%m-%Y")
    month_str = exp_dt.strftime("%B").lower()  # "march"
    
    # 2. expiry_day (e.g. "30")
    expiry_day = exp_dt.strftime("%d")
    
    # 3. current_date (e.g. "25")
    curr_dt = datetime.strptime(date_str, "%Y-%m-%d")
    current_day = curr_dt.strftime("%d")
    
    base_dir = os.path.join(month_str, expiry_day, current_day)
    ce_dir = os.path.join(base_dir, "CE")
    pe_dir = os.path.join(base_dir, "PE")
    
    os.makedirs(ce_dir, exist_ok=True)
    os.makedirs(pe_dir, exist_ok=True)
    
    return base_dir, ce_dir, pe_dir


# ──────────────────────────────────────────────
# FETCH SINGLE SYMBOL
# ──────────────────────────────────────────────
def fetch_1min_data(fyers, symbol, date_str):
    with _sem:
        max_retries = 3
        for attempt in range(max_retries):
            time.sleep(RATE_DELAY * (attempt + 1))
            try:
                resp = fyers.history({
                    "symbol":      symbol,
                    "resolution":  RESOLUTION,
                    "date_format": "1",
                    "range_from":  date_str,
                    "range_to":    date_str,
                    "cont_flag":   "0"
                })
                
                # Handle rate limiting or temporary issues
                if resp.get("code") != 200:
                    if attempt < max_retries - 1:
                        wait_sec = (attempt + 1) * 2
                        logger.warning(f"  ⚠️  {symbol} failed ({resp.get('code')}). Retrying in {wait_sec}s... (Attempt {attempt+1})")
                        time.sleep(wait_sec)
                        continue
                    return symbol, None

                if "candles" not in resp or not resp["candles"]:
                    return symbol, None

                df = pd.DataFrame(
                    resp["candles"],
                    columns=["datetime", "open", "high", "low", "close", "volume"]
                )
                df["datetime"] = pd.to_datetime(df["datetime"], unit="s")
                df["datetime"] = (
                    df["datetime"]
                    .dt.tz_localize(pytz.UTC)
                    .dt.tz_convert(IST)
                    .dt.strftime("%Y-%m-%d %H:%M:%S")
                )
                df = df.sort_values("datetime").reset_index(drop=True)
                return symbol, df

            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                logger.warning(f"  ⚠️  Error fetching {symbol}: {e}")
                return symbol, None
        return symbol, None


# ──────────────────────────────────────────────
# SAFE FILENAME
# ──────────────────────────────────────────────
def safe_filename(symbol):
    return symbol.replace("NSE:", "").replace(":", "_").replace("/", "_") + ".csv"


# ──────────────────────────────────────────────
# MAIN DOWNLOAD FUNCTION
# ──────────────────────────────────────────────
def run_download(date_str=None):
    if date_str is None:
        date_str = datetime.now(IST).strftime("%Y-%m-%d")

    logger.info("=" * 65)
    logger.info(f"📥 OPTIONS EOD DOWNLOADER | Target Date: {date_str}")
    logger.info("=" * 65)

    fyers, _ = authenticate()
    if not fyers:
        return False

    # 1. Get spot price for ATM filtering
    spot_price = get_nifty_spot(fyers, date_str)
    atm_strike = round(spot_price / 50) * 50 if spot_price else None

    # 2. Get filtered symbols
    expiry_date, ce_symbols, pe_symbols = get_nearest_expiry_and_symbols(fyers, atm_strike)
    if not expiry_date or (not ce_symbols and not pe_symbols):
        logger.error("Failed to extract expiry and symbols. Aborting.")
        return False

    logger.info(f"📁 Building directory structure for Expiry={expiry_date}, Current Date={date_str}")
    base_dir, ce_dir, pe_dir = build_directories(date_str, expiry_date)

    all_symbols = [("CE", s) for s in ce_symbols] + [("PE", s) for s in pe_symbols]
    total = len(all_symbols)
    logger.info(f"🚀 Fetching {total} symbols with {MAX_WORKERS} parallel workers...")

    saved_ce, saved_pe, failed = 0, 0, 0
    completed_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="fetcher") as pool:
        futures = {
            pool.submit(fetch_1min_data, fyers, sym, date_str): (opt_type, sym)
            for opt_type, sym in all_symbols
        }

        for future in as_completed(futures):
            opt_type, orig_sym = futures[future]
            try:
                symbol, df = future.result()
                completed_count += 1

                if df is not None and not df.empty:
                    out_dir  = ce_dir if opt_type == "CE" else pe_dir
                    out_path = os.path.join(out_dir, safe_filename(symbol))
                    df.to_csv(out_path, index=False)
                    
                    if opt_type == "CE":
                        saved_ce += 1
                    else:
                        saved_pe += 1
                    logger.info(f"  ✅ [{completed_count}/{total}] {symbol} → {len(df)} rows")
                else:
                    failed += 1
                    logger.warning(f"  ⚠️  [{completed_count}/{total}] {orig_sym} → no data for today")

            except Exception as e:
                failed += 1
                completed_count += 1
                logger.error(f"  ❌ [{completed_count}/{total}] {orig_sym}: {e}")

    logger.info("")
    logger.info("=" * 65)
    logger.info(f"✅ DOWNLOAD COMPLETE for {date_str}")
    logger.info(f"   📁 Target Path : {base_dir}/")
    logger.info(f"   CE saved : {saved_ce} files  →  {ce_dir}/")
    logger.info(f"   PE saved : {saved_pe} files  →  {pe_dir}/")
    logger.info(f"   Failed / Empty : {failed}")
    logger.info("=" * 65)
    return True


if __name__ == "__main__":
    # Priority:
    # 1. Command line argument (e.g. python fetch_options_eod.py 2026-03-25)
    # 2. TARGET_DATE variable set in script
    # 3. Default to today's date
    cmd_date = sys.argv[1] if len(sys.argv) > 1 else None
    target = cmd_date or TARGET_DATE
    
    run_download(target)
