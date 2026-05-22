import argparse
import sys
import os
import asyncio

# Reconfigure stdout/stderr to support UTF-8 emojis on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add the parent directory of this file to the Python path to make absolute imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from scripts import run_amazon_tracker, run_myntra_tracker, run_flipkart_tracker

async def run_engine(args):
    specific_run = args.amazon or args.myntra or args.flipkart
    
    print("=" * 60)
    print("PRICE TRACKER ENGINE (ASYNC)")
    print("=" * 60)
    
    if not specific_run:
        print("[INFO] No specific platform flags provided. Running all scrapers sequentially.")
        print("[SEQUENCE] Amazon -> Myntra -> Flipkart\n")
        
        print("[1/3] Launching Amazon tracker...")
        await run_amazon_tracker()
        
        print("\n[2/3] Launching Myntra tracker...")
        await run_myntra_tracker()
        
        print("\n[3/3] Launching Flipkart tracker...")
        await run_flipkart_tracker()
    else:
        # Run specific crawlers in the sequential order requested: Amazon -> Myntra -> Flipkart
        if args.amazon:
            print("Launching Amazon tracker...")
            await run_amazon_tracker()
            
        if args.myntra:
            print("\nLaunching Myntra tracker...")
            await run_myntra_tracker()
            
        if args.flipkart:
            print("\nLaunching Flipkart tracker...")
            await run_flipkart_tracker()
            
    print("\n" + "=" * 60)
    print("All requested tracking jobs completed successfully!")
    print("=" * 60)

def main():
    parser = argparse.ArgumentParser(
        description="Multi-platform Price Scraper and Tracker CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python app/main.py -a          # Run Amazon scraper only
  python app/main.py -m          # Run Myntra scraper only
  python app/main.py -f          # Run Flipkart scraper only
  python app/main.py -a -m       # Run Amazon and Myntra scrapers
  python app/main.py             # Run all sequentially (Amazon -> Myntra -> Flipkart)
"""
    )
    
    parser.add_argument("-a", "--amazon", action="store_true", help="Track Amazon prices")
    parser.add_argument("-m", "--myntra", action="store_true", help="Track Myntra prices")
    parser.add_argument("-f", "--flipkart", action="store_true", help="Track Flipkart prices")
    
    args = parser.parse_args()
    
    # Initialize Proactor event loop policy on Windows to prevent Playwright loop crashes
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
    try:
        asyncio.run(run_engine(args))
    except KeyboardInterrupt:
        print("\n[INFO] Price tracker engine stopped by user.")
        sys.exit(0)

if __name__ == "__main__":
    main()
