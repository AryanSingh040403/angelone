# AngelOne 

This repository contains a robust, crash-resilient tick data collector and historical backfill script for the AngelOne SmartAPI.

## Setup Instructions

1. Install Python 3.9+
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your AngelOne API credentials.
4. Add your list of stock symbols to `tokens.json`.

## Usage

* **Historical Data**: Run `python backfill.py` to download years of daily candle data.
* **Real-time Collector**: Run `python scheduler.py` to continuously collect real-time data automatically during market hours. The data is saved directly as Parquet files to save space.
* **Troubleshooting**: Run `python test_totp.py` if you have login/authentication errors related to the TOTP key.
