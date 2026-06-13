import os
import sys
import tarfile
import shutil
import logging
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

# Path setup
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
DATA_DIR = os.path.join(SCRIPT_DIR, "data")

os.makedirs(LOG_DIR, exist_ok=True)

# Logger setup
log_file = os.path.join(LOG_DIR, "uploader.log")
logger = logging.getLogger("Uploader")
logger.setLevel(logging.INFO)

# Setup handlers only if not already initialized
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

def get_ist_now_naive():
    """
    Returns current time in IST as a timezone-naive object
    to safely compare with folder parsed dates.
    """
    return datetime.now(IST).replace(tzinfo=None)

def compress_folder(source_dir, output_filename):
    """
    Compresses the directory as a .tar.gz file.
    Uses atomic write-then-replace logic.
    """
    logger.info(f"Compressing {source_dir} into {output_filename}...")
    tmp_output = output_filename + ".tmp"
    try:
        with tarfile.open(tmp_output, "w:gz") as tar:
            tar.add(source_dir, arcname=os.path.basename(source_dir))
        os.replace(tmp_output, output_filename)
        logger.info(f"Compression completed successfully: {output_filename}")
        return True
    except Exception as e:
        logger.error(f"Failed to compress folder: {e}")
        if os.path.exists(tmp_output):
            try:
                os.remove(tmp_output)
            except Exception:
                pass
        return False

def clean_old_local_data():
    """
    Removes local directories older than 7 days based on their folder date names (YYYY-MM-DD).
    Uses naive date arithmetic to prevent offset-naive/aware comparison TypeError.
    Specifically skips the 'historical' directory to preserve the backfill dataset.
    """
    logger.info("Checking for local data folders older than 7 days...")
    ist_now = get_ist_now_naive()
    cutoff_date = ist_now - timedelta(days=7)
    
    cleaned_count = 0
    if not os.path.exists(DATA_DIR):
        return
        
    for entry in os.listdir(DATA_DIR):
        entry_path = os.path.join(DATA_DIR, entry)
        if os.path.isdir(entry_path) and entry != "historical":
            try:
                # Parse directory name as YYYY-MM-DD date (naive)
                dir_date = datetime.strptime(entry, "%Y-%m-%d")
                if dir_date < cutoff_date:
                    logger.info(f"Removing expired local directory: {entry_path}")
                    shutil.rmtree(entry_path)
                    cleaned_count += 1
            except ValueError:
                # Skip folders that do not follow the YYYY-MM-DD naming convention (like "historical")
                continue
                
    logger.info(f"Cleaned up {cleaned_count} expired local folders.")

def main():
    logger.info("Uploader process started.")
    
    # Find today's folder name in IST
    ist_now = get_ist_now_naive()
    date_str = ist_now.strftime("%Y-%m-%d")
    todays_dir = os.path.join(DATA_DIR, date_str)
    
    if not os.path.exists(todays_dir):
        logger.warning(f"Today's data directory {todays_dir} does not exist. Nothing to compress.")
        clean_old_local_data()
        return
        
    # Check if folder is empty
    if not os.listdir(todays_dir):
        logger.warning(f"Today's data directory {todays_dir} is empty. Skipping compression.")
        clean_old_local_data()
        return

    # Output compressed filename
    archive_name = f"data_{date_str}.tar.gz"
    archive_path = os.path.join(DATA_DIR, archive_name)
    
    # Compress today's folder
    if compress_folder(todays_dir, archive_path):
        logger.info("Daily compression task completed successfully.")
        clean_old_local_data()
    else:
        logger.error("Daily compression task failed.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Uploader process terminated manually by user.")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Unhandled uploader error: {e}")
        sys.exit(1)
