# Price Tracker

A Playwright-based Python application to track product prices on various e-commerce platforms (starting with Myntra, with future support for Amazon, Flipkart, etc.).

## Project Structure
```
price-tracker-updated/
├── .gitignore
├── README.md
├── venv/                 # Python Virtual Environment
└── app/
    ├── .env              # Telegram and configuration credentials
    ├── main.py           # Core scraper and price comparator
    └── data/
        ├── product.json  # JSON storing target products, current prices, etc.
        └── cookies.json  # Authenticated cookies for seamless login
```

## Setup Instructions

1. **Virtual Environment**:
   Ensure `venv` is active and requirements are installed.
   ```bash
   # Activate venv on Windows:
   .\venv\Scripts\activate
   ```

2. **Configuration**:
   Add your credentials in `app/.env`:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   ```

3. **Product Configuration**:
   Define products to track in `app/data/product.json`.
