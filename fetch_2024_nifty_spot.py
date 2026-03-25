#!/usr/bin/env python3
"""
Fetch Nifty 50 Spot data for 2024 based on dates found in 2024_daywise.
"""
import os
import time
import pandas as pd
import pytz
from datetime import datetime
import dotenv

from fyers_apiv3 import fyersModel

dotenv.load_dotenv()

IST = pytz.timezone('Asia/Kolkata')
API_DELAY = 0.5

def authenticate():
    from main import FyersAuthenticator
    client_id = os.getenv("CLIENT_ID")
    secret_key = os.getenv("SECRET_KEY")
    username = os.getenv("USERNAME")
    pin = os.getenv("PIN")
    totp_key = os.getenv("TOTP_KEY")
    
    auth = FyersAuthenticator(client_id, secret_key, "https://www.google.com", username, pin, totp_key)
    token, err = auth.get_access_token()
    if not token:
        print(f"Auth failed: {err}")
        return None
    return fyersModel.FyersModel(client_id=client_id, token=token, log_path="")

def get_dates_from_folder(folder_path):
    dates = []
    for item in os.listdir(folder_path):
        if os.path.isdir(os.path.join(folder_path, item)):
            try:
                # Format: DDMMMYY e.g. 01APR24
                date_obj = datetime.strptime(item, "%d%b%y")
                dates.append(date_obj.strftime("%Y-%m-%d"))
            except ValueError:
                pass
    return sorted(list(set(dates)))

def fetch_and_save_spot(fyers, dates, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    symbol = "NSE:NIFTY50-INDEX"
    
    for date_str in dates:
        out_file = os.path.join(out_dir, f"nifty_spot_5min_{date_str}.csv")
        if os.path.exists(out_file):
            print(f"Skipping {date_str}, already exists.")
            continue
            
        print(f"Fetching {date_str}...")
        time.sleep(API_DELAY)
        
        data = {
            "symbol": symbol,
            "resolution": "5",
            "date_format": "1",
            "range_from": date_str,
            "range_to": date_str,
            "cont_flag": "0"
        }
        
        res = fyers.history(data)
        if res.get("code") == 200 and "candles" in res and res["candles"]:
            df = pd.DataFrame(res['candles'], columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = pd.to_datetime(df['datetime'], unit='s')
            df['datetime'] = df['datetime'].dt.tz_localize(pytz.UTC).dt.tz_convert(IST)
            df['datetime'] = df['datetime'].dt.strftime('%H:%M') # Match options format
            
            df.to_csv(out_file, index=False)
            print(f"  Saved {len(df)} candles to {out_file}")
        else:
            print(f"  Failed for {date_str}: {res}")

def main():
    fyers = authenticate()
    if not fyers: return
    
    base_folder = "/Users/pavanreddy/EMA/2024_daywise"
    spot_folder = "/Users/pavanreddy/EMA/2024_daywise_spot"
    
    dates = get_dates_from_folder(base_folder)
    print(f"Found {len(dates)} days in 2024_daywise.")
    
    fetch_and_save_spot(fyers, dates, spot_folder)

if __name__ == "__main__":
    main()
