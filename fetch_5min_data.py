"""
Fetch 5-Min Historical Data: Nifty 50 Spot + ~₹70 OTM Option (CE & PE)
=======================================================================
Smart approach to find the ₹70 option historically:
  1. Fetch Nifty 50 spot 5-min data for each date
  2. Get the 9:20 candle close (spot price at 9:20)
  3. Based on spot price, construct OTM option symbols in steps of 50 points
     - OTM CE: strikes ABOVE spot (e.g. spot+100, spot+150, ..., spot+600)
     - OTM PE: strikes BELOW spot (e.g. spot-100, ..., spot-600)
  4. Fetch 9:20 candle for each OTM strike candidate
  5. Pick CE and PE whose 9:20 close is closest to ₹70
  6. Fetch full day 5-min data for the chosen CE and PE

Dates: March 4, 5, 6, 9 (2026)
Saves to: data_5min/

Usage: python fetch_5min_data.py
"""

import os
import json
import time
import logging
import pandas as pd
import pytz
import dotenv
from datetime import datetime, timedelta
from fyers_apiv3 import fyersModel

dotenv.load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

IST          = pytz.timezone("Asia/Kolkata")
API_DELAY    = 0.6          # seconds between calls
DATA_DIR     = "data_5min"
RESOLUTION   = "5"
SPOT_SYMBOL  = "NSE:NIFTY50-INDEX"
TARGET_PRICE = 70.0         # desired option price at 9:20
STRIKE_STEP  = 50           # NIFTY strikes in multiples of 50
# OTM range to scan: 100 .. 800 points from spot in steps of 50 → 15 strikes per side
OTM_MIN_PTS  = 100
OTM_MAX_PTS  = 800

# Fixed dates to fetch
TARGET_DATES = ["2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09"]


# ============================================================
# AUTH
# ============================================================

def authenticate():
    from main import FyersAuthenticator
    cid  = os.getenv("CLIENT_ID")
    sk   = os.getenv("SECRET_KEY")
    user = os.getenv("USERNAME")
    pin  = os.getenv("PIN")
    totp = os.getenv("TOTP_KEY")
    if not all([cid, sk, user, pin, totp]):
        logger.error("Missing .env variables!")
        return None, None
    auth = FyersAuthenticator(cid, sk, "https://www.google.com", user, pin, totp)
    token, err = auth.get_access_token()
    if not token:
        logger.error(f"Auth failed: {err}")
        return None, None
    fyers = fyersModel.FyersModel(client_id=cid, token=token, log_path="")
    logger.info("✅ Authentication successful!")
    return fyers, cid


# ============================================================
# CANDLE FETCH HELPERS
# ============================================================

def fetch_candles(fyers, symbol, resolution, date_str):
    """Fetch all candles for a symbol on a given date. Returns DataFrame."""
    time.sleep(API_DELAY)
    resp = fyers.history({
        "symbol":     symbol,
        "resolution": resolution,
        "date_format":"1",
        "range_from": date_str,
        "range_to":   date_str,
        "cont_flag":  "0"
    })
    if resp.get("code") != 200 or "candles" not in resp:
        return pd.DataFrame()
    df = pd.DataFrame(resp["candles"], columns=["datetime","open","high","low","close","volume"])
    df["datetime"] = pd.to_datetime(df["datetime"], unit="s")
    df["datetime"] = df["datetime"].dt.tz_localize(pytz.UTC).dt.tz_convert(IST)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def get_920_candle(df):
    """
    Return the 5-min candle that opens at 09:15 (closes at 09:20).
    This is the first 5-min candle of the trading day.
    """
    if df.empty:
        return None
    # Match 09:15 candle timestamp
    mask = (df["datetime"].dt.hour == 9) & (df["datetime"].dt.minute == 15)
    row = df[mask]
    if row.empty:
        # Fallback: just take the first candle
        row = df.iloc[[0]]
    return row.iloc[0]


# ============================================================
# OPTION SYMBOL BUILDER (NIFTY weekly format)
# ============================================================

def get_nearest_expiry_str(fyers):
    """Return nearest expiry as string (unix timestamp string from API)."""
    time.sleep(API_DELAY)
    resp = fyers.optionchain({"symbol": SPOT_SYMBOL, "strikecount": 1, "timestamp": ""})
    if resp.get("code") != 200:
        logger.error(f"Failed to get expiry: {resp}")
        return None
    return str(resp["data"]["expiryData"][0]["expiry"])


def get_otm_strikes(spot_price, step=STRIKE_STEP, min_pts=OTM_MIN_PTS, max_pts=OTM_MAX_PTS):
    """
    Generate OTM CE strikes (above spot) and OTM PE strikes (below spot).
    Rounds spot to nearest step first so strikes align to valid multiples.
    """
    rounded_spot = round(spot_price / step) * step

    ce_strikes, pe_strikes = [], []
    for pts in range(min_pts, max_pts + step, step):
        ce_strikes.append(rounded_spot + pts)    # OTM for CE
        pe_strikes.append(rounded_spot - pts)    # OTM for PE

    return ce_strikes, pe_strikes


def get_option_symbols_from_chain(fyers, expiry_ts, spot_price):
    """
    Fetch option chain and return a dict of strike → {CE: symbol, PE: symbol}
    for strikes in the OTM range around spot_price.
    """
    time.sleep(API_DELAY)
    resp = fyers.optionchain({
        "symbol":      SPOT_SYMBOL,
        "strikecount": 20,          # wide enough to cover ±1000 pts
        "timestamp":   expiry_ts
    })
    if resp.get("code") != 200:
        logger.error(f"Option chain error: {resp}")
        return {}

    chain = resp["data"]["optionsChain"]
    strike_map = {}
    for opt in chain:
        sp       = opt.get("strike_price", 0)
        opt_type = opt.get("option_type", "")
        sym      = opt.get("symbol", "")
        if not sym or sp == 0:
            continue
        if sp not in strike_map:
            strike_map[sp] = {}
        strike_map[sp][opt_type] = sym

    return strike_map


# ============================================================
# FIND ₹70 OPTION FOR ONE DAY
# ============================================================

def find_70_option_for_day(fyers, date_str, spot_920_close, expiry_ts):
    """
    1. Generate OTM strike candidates based on spot price
    2. Get their symbols from the option chain  
    3. Fetch each candidate's 9:20 candle
    4. Return (ce_symbol, ce_920_close, pe_symbol, pe_920_close)
    """
    ce_strikes, pe_strikes = get_otm_strikes(spot_920_close)
    logger.info(f"  Spot at 9:20: ₹{spot_920_close:.2f}")
    logger.info(f"  Scanning {len(ce_strikes)} OTM CE strikes + {len(pe_strikes)} OTM PE strikes...")

    # Get option chain symbol map
    strike_map = get_option_symbols_from_chain(fyers, expiry_ts, spot_920_close)
    if not strike_map:
        logger.error("  Empty strike map!")
        return None, None, None, None

    # Build candidate lists from chain
    ce_candidates = {}   # {symbol: 9:20_close}
    pe_candidates = {}

    for strike in ce_strikes:
        if strike in strike_map and "CE" in strike_map[strike]:
            sym = strike_map[strike]["CE"]
            df  = fetch_candles(fyers, sym, RESOLUTION, date_str)
            candle = get_920_candle(df)
            if candle is not None and candle["close"] > 0:
                ce_candidates[sym] = float(candle["close"])
                logger.info(f"    CE {strike}: 9:20 close = ₹{candle['close']:.2f}  [{sym}]")

    for strike in pe_strikes:
        if strike in strike_map and "PE" in strike_map[strike]:
            sym = strike_map[strike]["PE"]
            df  = fetch_candles(fyers, sym, RESOLUTION, date_str)
            candle = get_920_candle(df)
            if candle is not None and candle["close"] > 0:
                pe_candidates[sym] = float(candle["close"])
                logger.info(f"    PE {strike}: 9:20 close = ₹{candle['close']:.2f}  [{sym}]")

    if not ce_candidates or not pe_candidates:
        logger.error("  No valid candidates found!")
        return None, None, None, None

    best_ce  = min(ce_candidates, key=lambda s: abs(ce_candidates[s] - TARGET_PRICE))
    best_pe  = min(pe_candidates, key=lambda s: abs(pe_candidates[s] - TARGET_PRICE))
    ce_price = ce_candidates[best_ce]
    pe_price = pe_candidates[best_pe]

    logger.info(f"  ✅ Best CE: {best_ce}  9:20 close = ₹{ce_price:.2f} (Δ={ce_price-TARGET_PRICE:+.2f})")
    logger.info(f"  ✅ Best PE: {best_pe}  9:20 close = ₹{pe_price:.2f} (Δ={pe_price-TARGET_PRICE:+.2f})")
    return best_ce, ce_price, best_pe, pe_price


# ============================================================
# SAVE HELPER
# ============================================================

def save_csv(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    logger.info(f"    Saved {path}  ({len(df)} rows)")


# ============================================================
# MAIN
# ============================================================

def main():
    logger.info("=" * 65)
    logger.info("5-MIN FETCHER — Spot-First ₹70 OTM Option Strategy")
    logger.info(f"Dates: {TARGET_DATES}")
    logger.info("=" * 65)

    # Step 1: Auth
    fyers, _ = authenticate()
    if not fyers:
        return

    # Step 2: Nearest expiry (shared for all dates — same weekly expiry usually)
    logger.info("\n🔍 Getting nearest expiry...")
    expiry_ts = get_nearest_expiry_str(fyers)
    if not expiry_ts:
        logger.error("Cannot get expiry. Exiting.")
        return
    logger.info(f"  Expiry timestamp: {expiry_ts}")

    metadata = {
        "fetch_time":    datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "resolution":    "5min",
        "target_price":  TARGET_PRICE,
        "expiry_ts":     expiry_ts,
        "dates":         TARGET_DATES,
        "days":          {}
    }

    # Step 3: Process each date
    for date_str in TARGET_DATES:
        logger.info(f"\n{'='*55}")
        logger.info(f"📅 Processing: {date_str}")
        logger.info(f"{'='*55}")

        # Fetch spot data first
        logger.info("  [5min] Fetching Nifty 50 spot...")
        spot_df = fetch_candles(fyers, SPOT_SYMBOL, RESOLUTION, date_str)
        if spot_df.empty:
            logger.warning(f"  No spot data for {date_str}. Skipping.")
            continue

        save_csv(spot_df, f"{DATA_DIR}/nifty_spot_5min_{date_str}.csv")

        # Get 9:20 spot close
        spot_920 = get_920_candle(spot_df)
        if spot_920 is None:
            logger.warning(f"  No 9:20 candle for {date_str}. Skipping.")
            continue
        spot_920_close = float(spot_920["close"])
        logger.info(f"  Nifty 9:20 candle → O:{spot_920['open']:.2f}  H:{spot_920['high']:.2f}  "
                    f"L:{spot_920['low']:.2f}  C:{spot_920_close:.2f}")

        # Find ₹70 OTM option using spot as anchor
        ce_sym, ce_920, pe_sym, pe_920 = find_70_option_for_day(
            fyers, date_str, spot_920_close, expiry_ts
        )
        if not ce_sym or not pe_sym:
            logger.warning(f"  Skipping {date_str} — could not find ₹70 OTM option")
            continue

        # Fetch full-day 5-min data for the chosen options
        logger.info(f"  [5min] Fetching full-day CE ({ce_sym})...")
        ce_df = fetch_candles(fyers, ce_sym, RESOLUTION, date_str)
        if not ce_df.empty:
            save_csv(ce_df, f"{DATA_DIR}/nifty_ce_5min_{date_str}.csv")

        logger.info(f"  [5min] Fetching full-day PE ({pe_sym})...")
        pe_df = fetch_candles(fyers, pe_sym, RESOLUTION, date_str)
        if not pe_df.empty:
            save_csv(pe_df, f"{DATA_DIR}/nifty_pe_5min_{date_str}.csv")

        metadata["days"][date_str] = {
            "spot_920_close": spot_920_close,
            "ce_symbol":      ce_sym,
            "ce_price_at_920":ce_920,
            "pe_symbol":      pe_sym,
            "pe_price_at_920":pe_920,
        }

    # Step 4: Save metadata
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(f"{DATA_DIR}/metadata.json", "w") as f:
        json.dump(metadata, f, indent=4)

    logger.info("\n" + "=" * 65)
    logger.info("✅ FETCH COMPLETE")
    logger.info("=" * 65)
    for dt, info in metadata["days"].items():
        logger.info(f"  {dt}: Spot 9:20=₹{info['spot_920_close']:.2f}")
        logger.info(f"         CE: {info['ce_symbol']} @ ₹{info['ce_price_at_920']:.2f}")
        logger.info(f"         PE: {info['pe_symbol']} @ ₹{info['pe_price_at_920']:.2f}")
    logger.info(f"\nFiles in: {DATA_DIR}/")
    logger.info(f"Next step: python3 analyze_divergence_5min.py")


if __name__ == "__main__":
    main()
