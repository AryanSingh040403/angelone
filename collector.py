import os
import sys
import time
import json
import csv
import logging
import threading
import pandas as pd
from datetime import datetime, time as dt_time, timezone, timedelta
from logging.handlers import RotatingFileHandler

# Define Indian Standard Time (IST)
IST = timezone(timedelta(hours=5, minutes=30))

# Ensure the directory containing this script is in the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from auth import get_auth_session

# Setup paths (logs and data reside directly inside the angelone-collector directory)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
DATA_DIR = os.path.join(SCRIPT_DIR, "data")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# Configure Logging
log_file = os.path.join(LOG_DIR, "collector.log")
logger = logging.getLogger("DataCollector")
logger.setLevel(logging.INFO)

# Rotating log handler (10MB per file, keeps 5 backups)
file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Console log handler for visibility
console_handler = logging.StreamHandler()
console_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# Global variables for thread safety
ticks_lock = threading.Lock()
in_memory_ticks = []

def load_tokens():
    """
    Loads tokens.json and builds a reverse lookup map.
    Normalizes symbol names for safe file paths (replaces spaces with underscores).
    """
    tokens_path = os.path.join(SCRIPT_DIR, "tokens.json")
    if not os.path.exists(tokens_path):
        err_msg = f"tokens.json not found at: {tokens_path}. Run get_tokens.py first."
        logger.error(err_msg)
        raise FileNotFoundError(err_msg)
        
    with open(tokens_path, "r") as f:
        token_map = json.load(f)
        
    # Build reverse lookup (token_id -> normalized_symbol_name)
    token_to_symbol = {}
    for symbol, token in token_map.items():
        clean_sym = symbol.replace(" ", "_")
        if token not in token_to_symbol:
            token_to_symbol[token] = clean_sym
        else:
            current = token_to_symbol[token]
            # Preference matching logic for cleaner filenames
            if current == "NIFTY" and clean_sym == "Nifty_50":
                token_to_symbol[token] = clean_sym
            elif current == "BANKNIFTY" and clean_sym == "Bank_Nifty":
                token_to_symbol[token] = clean_sym
            elif current in ["LTIM", "TATAMOTORS"] and clean_sym in ["LTM", "TMPV"]:
                token_to_symbol[token] = clean_sym
                
    return token_map, token_to_symbol

def parse_snap_quote(message, ticker):
    """
    Parses a single Snap Quote message into the target dictionary layout.
    """
    token = message.get("token")
    exchange_ts = message.get("exchange_timestamp")
    
    # Resolve timestamp: prefer exchange timestamp in epoch ms (UTC), fallback to system time
    if exchange_ts:
        timestamp = datetime.fromtimestamp(exchange_ts / 1000.0, tz=IST)
    else:
        timestamp = datetime.now(IST)
        
    # Filter out ticks before 9:15 AM IST (pre-open session)
    if timestamp.time() < dt_time(9, 15, 0):
        return None
        
    # Standard prices are returned in paise; divide by 100.0 to convert to Rupees
    ltp = message.get("last_traded_price", 0) / 100.0
    open_price = message.get("open_price_of_the_day", 0) / 100.0
    high_price = message.get("high_price_of_the_day", 0) / 100.0
    low_price = message.get("low_price_of_the_day", 0) / 100.0
    close_price = message.get("closed_price", 0) / 100.0
    volume = message.get("volume_trade_for_the_day", 0)
    
    # Extract Best Bid and Ask from buy/sell lists
    best_5_buy = message.get("best_5_buy_data", [])
    best_5_sell = message.get("best_5_sell_data", [])
    
    bid_price = 0.0
    bid_qty = 0
    if best_5_buy:
        # First buy packet is the highest bid
        bid_price = best_5_buy[0].get("price", 0) / 100.0
        bid_qty = best_5_buy[0].get("quantity", 0)
        
    ask_price = 0.0
    ask_qty = 0
    if best_5_sell:
        # First sell packet is the lowest ask
        ask_price = best_5_sell[0].get("price", 0) / 100.0
        ask_qty = best_5_sell[0].get("quantity", 0)
        
    return {
        "timestamp": timestamp,
        "symbol": ticker,
        "ltp": ltp,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
        "bid_price": bid_price,
        "bid_qty": bid_qty,
        "ask_price": ask_price,
        "ask_qty": ask_qty
    }

def flush_ticks(ticks_list):
    """
    Groups the ticks by date and symbol, loading any existing Parquet file,
    appending the new ticks, and writing back to partition path:
    data/YYYY-MM-DD/SYMBOL.parquet
    """
    if not ticks_list:
        return
        
    df_new = pd.DataFrame(ticks_list)
    # Extract date string for YYYY-MM-DD directories
    df_new["date_str"] = df_new["timestamp"].dt.strftime("%Y-%m-%d")
    
    grouped = df_new.groupby(["date_str", "symbol"])
    
    for (date_str, symbol), group in grouped:
        dir_path = os.path.join(DATA_DIR, date_str)
        os.makedirs(dir_path, exist_ok=True)
        
        file_path = os.path.join(dir_path, f"{symbol}.parquet")
        
        # Drop temporary date_str column
        df_to_save = group.drop(columns=["date_str"])
        
        # Read existing parquet if it exists to append
        tmp_file_path = file_path + ".tmp"
        if os.path.exists(file_path):
            try:
                df_existing = pd.read_parquet(file_path)
                # Concatenate and maintain chronological order
                df_combined = pd.concat([df_existing, df_to_save], ignore_index=True)
                df_combined.to_parquet(tmp_file_path, index=False)
                os.replace(tmp_file_path, file_path)
            except Exception as e:
                logger.error(f"Failed to append to existing Parquet file {file_path}: {e}")
                if os.path.exists(tmp_file_path):
                    try:
                        os.remove(tmp_file_path)
                    except Exception:
                        pass
        else:
            try:
                df_to_save.to_parquet(tmp_file_path, index=False)
                os.replace(tmp_file_path, file_path)
                logger.info(f"Created new Parquet file for {symbol} at {file_path}")
            except Exception as e:
                logger.error(f"Failed to create Parquet file {file_path}: {e}")
                if os.path.exists(tmp_file_path):
                    try:
                        os.remove(tmp_file_path)
                    except Exception:
                        pass

def flusher_loop():
    """
    Runs continuously in a daemon thread, flushing in-memory ticks to Parquet files every 5 minutes.
    """
    logger.info("Tick flusher thread started.")
    while True:
        # Sleep for 5 minutes (300 seconds)
        time.sleep(300)
        
        try:
            logger.info("Flushing ticks from memory to Parquet...")
            # Thread-safe retrieval and clear
            with ticks_lock:
                ticks_to_flush = list(in_memory_ticks)
                in_memory_ticks.clear()
                
            if ticks_to_flush:
                flush_ticks(ticks_to_flush)
                logger.info(f"Flushed {len(ticks_to_flush)} ticks to Parquet.")
            else:
                logger.info("No new ticks collected in the last 5 minutes.")
        except Exception as e:
            logger.error(f"Error in flusher loop: {e}")

def main():
    logger.info("Initializing Data Collector...")
    try:
        token_map, token_to_symbol = load_tokens()
    except Exception as e:
        logger.error(f"Failed to load token list: {e}")
        return

    # Extract all unique tokens to subscribe
    unique_tokens = list(set(token_map.values()))
    token_list = [
        {
            "exchangeType": 1, # NSE Cash Market segment
            "tokens": unique_tokens
        }
    ]

    # Start the background flusher daemon thread
    flusher_thread = threading.Thread(target=flusher_loop, daemon=True)
    flusher_thread.start()

    # Authenticate ONCE before the loop to prevent rate-limit bans on rapid reconnects
    try:
        logger.info("Authenticating and fetching tokens...")
        _, auth_token, feed_token = get_auth_session()
        api_key = os.getenv("API_KEY")
        client_id = os.getenv("CLIENT_ID")
    except Exception as e:
        logger.error(f"Initial authentication failed: {e}")
        return

    # Reconnect loop
    while True:
        try:
            # SmartWebSocketV2 handles simple reconnect logic internally, 
            # but we run it inside this try-except loop for complete crash resilience
            sws = SmartWebSocketV2(auth_token, api_key, client_id, feed_token)

            def on_data(wsapp, message):
                token = message.get("token")
                if not token:
                    return
                    
                ticker = token_to_symbol.get(token, "UNKNOWN")
                
                try:
                    tick = parse_snap_quote(message, ticker)
                    if tick is None:
                        return # Skip pre-open tick
                    # Thread-safe append
                    with ticks_lock:
                        in_memory_ticks.append(tick)
                    # Micro-log to file for debugging
                    logger.debug(f"Tick received: {ticker} -> LTP: {tick['ltp']:.2f}")
                except Exception as e:
                    logger.error(f"Failed to parse or store tick for {ticker} ({token}): {e}")

            def on_open(wsapp):
                logger.info("WebSocket connection established.")
                logger.info(f"Subscribing to {len(unique_tokens)} symbols in Snap Quote Mode (Mode 3)...")
                try:
                    # Mode 3 = SNAP_QUOTE
                    sws.subscribe(correlation_id="data_collection", mode=3, token_list=token_list)
                    logger.info("Subscription request transmitted successfully.")
                except Exception as e:
                    logger.error(f"Subscription failed: {e}")

            def on_error(wsapp, error):
                logger.error(f"WebSocket Client Error: {error}")

            def on_close(wsapp, code, reason):
                logger.warning(f"WebSocket Connection closed. Code: {code}, Reason: {reason}")

            # Assign callbacks
            sws.on_open = on_open
            sws.on_data = on_data
            sws.on_error = on_error
            sws.on_close = on_close

            # This blocks the thread while the socket connection is open
            sws.connect()
            
        except Exception as e:
            logger.error(f"WebSocket loop encountered error: {e}")
            
        # Reconnection delay
        logger.warning("Disconnected. Retrying connection in 5 seconds...")
        time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Collector process terminated manually by user.")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Unhandled collector crash: {e}")
        sys.exit(1)
