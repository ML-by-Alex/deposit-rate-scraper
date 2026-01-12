# deposit-rate-scraper

Scrapes USD deposit rates from Uzbek bank websites and exports clean Excel/CSV reports.

## What it does

- Reads bank URLs from `banks_urls.txt` (one per line)
- Scrapes each site for USD deposit information
- Exports structured data:
  - `result.xlsx` - formatted Excel workbook with color coding
  - `result.csv` - plain deposit table
  - `sites_status.csv` - scraping diagnostics and failures

## Quick start

```bash
# 1. Create virtual environment
python -m venv venv

# 2. Activate it
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create banks_urls.txt with your URLs
echo "https://xb.uz/page/physical-deposit?currency=USD" > banks_urls.txt
echo "https://mkbank.uz/oz/private/deposit/?currency=USD" >> banks_urls.txt

# 5. Run
python -m deposits