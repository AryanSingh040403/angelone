import os
import sys
import time
import json
import logging
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from auth import get_auth_session
from SmartApi import SmartConnect

# Apply global monkeypatch for request timeouts to prevent API hanging
original_request = requests.Session.request
def patched_request(self, method, url, *args, **kwargs):
    if 'timeout' not in kwargs or kwargs['timeout'] is None:
        kwargs['timeout'] = 10.0
    return original_request(self, method, url, *args, **kwargs)
requests.Session.request = patched_request

# Path setup
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
HISTORICAL_DIR = os.path.join(DATA_DIR, "historical")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(HISTORICAL_DIR, exist_ok=True)

# Logger setup
log_file = os.path.join(LOG_DIR, "backfill.log")
logger = logging.getLogger("Backfiller")
logger.setLevel(logging.INFO)

if not logger.handlers:
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
    file_formatter = logging.Formatter('%(asctime)s [%(levelname)s]: %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

# Timezone setup
IST = timezone(timedelta(hours=5, minutes=30))

def get_backfill_todate():
    """
    Computes a robust todate in IST.
    If the current IST time is past 15:30 (3:30 PM), today's candle is closed, so use today.
    Otherwise, use yesterday to avoid fetching partial/incomplete data for today.
    """
    ist_now = datetime.now(IST)
    market_close_today = ist_now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if ist_now >= market_close_today:
        return ist_now.strftime("%Y-%m-%d 15:30")
    else:
        yesterday = ist_now - timedelta(days=1)
        return yesterday.strftime("%Y-%m-%d 15:30")

def load_tokens():
    """
    Loads tokens.json from the current directory.
    """
    tokens_path = os.path.join(SCRIPT_DIR, "tokens.json")
    if not os.path.exists(tokens_path):
        err = f"tokens.json not found at: {tokens_path}."
        logger.error(err)
        raise FileNotFoundError(err)
        
    with open(tokens_path, "r") as f:
        return json.load(f)

def fetch_symbol_data(smart_connect, symbol, token, from_date, to_date):
    """
    Fetches historical daily data for a single symbol and writes to Parquet.
    """
    params = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": "ONE_DAY",
        "fromdate": from_date,
        "todate": to_date
    }
    
    try:
        response = smart_connect.getCandleData(params)
    except Exception as e:
        logger.warning(f"Exception during historical request for {symbol} ({token}): {e}")
        return False

    if not response or not response.get("status"):
        msg = response.get("message", "Unknown error") if response else "No response"
        code = response.get("errorcode", "No code") if response else "No code"
        logger.warning(f"Failed to fetch {symbol} ({token}): {msg} (Error Code: {code})")
        return False
        
    candle_list = response.get("data")
    if not candle_list:
        logger.warning(f"No daily candle data returned for {symbol} ({token}). Skipping Parquet write.")
        return False
        
    # Standard normalization of symbol name (replace spaces with underscores)
    clean_sym = symbol.replace(" ", "_")
    file_name = f"{clean_sym}_daily.parquet"
    file_path = os.path.join(HISTORICAL_DIR, file_name)
    tmp_file_path = file_path + ".tmp"
    
    try:
        # Construct DataFrame
        df = pd.DataFrame(candle_list, columns=["timestamp", "open", "high", "low", "close", "volume"])
        
        # Enforce proper datatypes for robust downstream queries
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["open"] = pd.to_numeric(df["open"])
        df["high"] = pd.to_numeric(df["high"])
        df["low"] = pd.to_numeric(df["low"])
        df["close"] = pd.to_numeric(df["close"])
        df["volume"] = pd.to_numeric(df["volume"]).astype(int)
        
        # Write atomically
        df.to_parquet(tmp_file_path, index=False)
        os.replace(tmp_file_path, file_path)
        
        print(f"Fetching {symbol}... done ({len(candle_list)} candles)")
        logger.info(f"Saved {len(candle_list)} daily candles for {symbol} to {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to process or save Parquet for {symbol}: {e}")
        if os.path.exists(tmp_file_path):
            try:
                os.remove(tmp_file_path)
            except Exception:
                pass
        return False

def main():
    logger.info("Historical backfill process started.")
    
    try:
        tokens_dict = load_tokens()
    except Exception as e:
        print(f"Error loading tokens: {e}")
        return
        
    # Login and initialize SmartConnect
    try:
        logger.info("Authenticating via auth.py...")
        smart_connect, auth_token, _ = get_auth_session()
        logger.info("Authentication successful.")
    except Exception as e:
        print(f"Authentication failed: {e}")
        logger.error(f"Authentication failed: {e}")
        return
        
    from_date = "2020-08-01 09:15"
    to_date = get_backfill_todate()
    
    print("\n" + "="*60)
    print("HISTORICAL BACKFILL RUNNING")
    print(f"From Date: {from_date}")
    print(f"To Date:   {to_date}")
    print(f"Saving to: {HISTORICAL_DIR}")
    print(f"Symbols:   {len(tokens_dict)}")
    print("="*60 + "\n")
    
    success_count = 0
    for idx, (symbol, token) in enumerate(tokens_dict.items(), 1):
        # 0.5s sleep to avoid rate limiting (REST limit is 3 requests/sec)
        time.sleep(0.5)
        
        success = fetch_symbol_data(smart_connect, symbol, token, from_date, to_date)
        if success:
            success_count += 1
            
    print("\n" + "="*60)
    print("BACKFILL COMPLETED")
    print(f"Successfully backfilled: {success_count}/{len(tokens_dict)} symbols")
    print("="*60 + "\n")
    
    logger.info(f"Backfill finished. Successfully collected {success_count}/{len(tokens_dict)} symbols.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Backfill terminated manually by user.")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Unhandled backfill exception: {e}")
        sys.exit(1)
