import os
import sys
import time as time_lib
import subprocess
import logging
from datetime import datetime, time, timezone, timedelta
from logging.handlers import RotatingFileHandler

# Path setup
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")

os.makedirs(LOG_DIR, exist_ok=True)

# Logger setup
log_file = os.path.join(LOG_DIR, "scheduler.log")
logger = logging.getLogger("Scheduler")
logger.setLevel(logging.INFO)

if not logger.handlers:
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
    file_formatter = logging.Formatter('%(asctime)s [%(levelname)s]: %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

# Timezone setup
IST = timezone(timedelta(hours=5, minutes=30))

# Combined 2025 and 2026 NSE Holiday List (YYYY-MM-DD format)
# Handled edge case of 2026-01-15 Maharashtra Municipal Elections holiday
NSE_HOLIDAYS = {
    # 2025 Trading Holidays
    "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10", "2025-04-14",
    "2025-04-18", "2025-05-01", "2025-08-15", "2025-08-27", "2025-10-02",
    "2025-10-21", "2025-10-22", "2025-11-05", "2025-12-25",
    
    # 2026 Trading Holidays
    "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
    "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24",
    "2026-12-25"
}

def get_ist_now():
    """
    Returns the current time in IST (UTC+5:30) as a timezone-aware datetime.
    """
    return datetime.now(IST)

def main():
    logger.info("NSE Trading Scheduler initialized.")
    logger.info(f"Target collector path: {os.path.join(SCRIPT_DIR, 'collector.py')}")
    logger.info(f"Target uploader path: {os.path.join(SCRIPT_DIR, 'uploader.py')}")
    
    collector_process = None
    last_uploader_run_date = None
    
    start_time = time(9, 10, 0)
    stop_time = time(15, 35, 0)

    # Main scheduler loop
    while True:
        try:
            ist_now = get_ist_now()
            current_date_str = ist_now.strftime("%Y-%m-%d")
            current_time = ist_now.time()
            
            # Weekday check (0=Monday, 6=Sunday)
            is_weekday = ist_now.weekday() < 5
            is_holiday = current_date_str in NSE_HOLIDAYS
            is_trading_day = is_weekday and not is_holiday
            
            # Check if we are inside market hours (9:10 AM - 3:35 PM IST)
            is_market_hours = start_time <= current_time < stop_time
            
            # 1. Collector control
            if is_trading_day and is_market_hours:
                # We should be running the collector. Handles startup catch-up automatically.
                if collector_process is None:
                    logger.info(f"Starting collector.py subprocess for trading day: {current_date_str}")
                    try:
                        collector_script = os.path.join(SCRIPT_DIR, "collector.py")
                        collector_process = subprocess.Popen(
                            [sys.executable, collector_script],
                            cwd=SCRIPT_DIR
                        )
                        logger.info(f"collector.py started with Process ID: {collector_process.pid}")
                    except Exception as e:
                        logger.error(f"Failed to start collector.py subprocess: {e}")
                else:
                    # Monitor if it exited unexpectedly
                    poll = collector_process.poll()
                    if poll is not None:
                        logger.warning(f"collector.py process exited unexpectedly with code {poll}. Restarting in 30s...")
                        collector_process = None
                        time_lib.sleep(30)  # Crash backoff to prevent rapid login/restart loops
            else:
                # We should NOT be running the collector
                if collector_process is not None:
                    logger.info("Stopping collector.py: Outside trading hours or holiday.")
                    try:
                        # Attempt graceful termination (SIGTERM)
                        collector_process.terminate()
                        logger.info("Sent termination signal (SIGTERM) to collector.py")
                        # Wait up to 10 seconds for it to exit
                        try:
                            collector_process.wait(timeout=10)
                            logger.info("collector.py process stopped gracefully.")
                        except subprocess.TimeoutExpired:
                            logger.warning("collector.py did not terminate in 10s. Force killing (SIGKILL)...")
                            collector_process.kill()
                            collector_process.wait()
                            logger.info("collector.py process force killed.")
                    except Exception as e:
                        logger.error(f"Error terminating collector.py process: {e}")
                    finally:
                        collector_process = None
            
            # 2. Uploader control (Runs at or after 4:00 PM IST on trading days)
            if is_trading_day:
                if current_time >= time(16, 0, 0) and last_uploader_run_date != current_date_str:
                    logger.info(f"Triggering uploader.py for trading day {current_date_str}...")
                    try:
                        uploader_script = os.path.join(SCRIPT_DIR, "uploader.py")
                        subprocess.Popen(
                            [sys.executable, uploader_script],
                            cwd=SCRIPT_DIR
                        )
                        last_uploader_run_date = current_date_str
                        logger.info("uploader.py process triggered successfully.")
                    except Exception as e:
                        logger.error(f"Failed to start uploader.py: {e}")

            # 3. Log status heartbeat (every 10 minutes on the 10-minute mark)
            if ist_now.minute % 10 == 0 and ist_now.second < 5:
                status = "RUNNING" if collector_process else "STOPPED"
                logger.info(f"Heartbeat check: trading_day={is_trading_day}, market_hours={is_market_hours}, collector={status}, last_uploader_run={last_uploader_run_date}")
                time_lib.sleep(5) # Prevent duplicate logs in the same minute

        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}")

        # Sleep for 5 seconds between checks
        time_lib.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Scheduler terminated manually by user.")
        sys.exit(0)
