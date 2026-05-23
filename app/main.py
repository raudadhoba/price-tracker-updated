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
    loop_count = 1
    while True:
        print("=" * 60)
        if args.loop:
            print(f"PRICE TRACKER ENGINE (ASYNC) - LOOP CYCLE #{loop_count}")
        else:
            print("PRICE TRACKER ENGINE (ASYNC)")
        print("=" * 60)
        
        # Determine active platforms based on CLI arguments
        active_platforms = []
        if args.amazon or (not args.amazon and not args.myntra and not args.flipkart):
            active_platforms.append(("Amazon", run_amazon_tracker))
        if args.myntra or (not args.amazon and not args.myntra and not args.flipkart):
            active_platforms.append(("Myntra", run_myntra_tracker))
        if args.flipkart or (not args.amazon and not args.myntra and not args.flipkart):
            active_platforms.append(("Flipkart", run_flipkart_tracker))
            
        for idx, (name, func) in enumerate(active_platforms):
            if idx > 0:
                print(f"\n[Engine] Waiting 15 seconds before launching next platform ({name})...")
                await asyncio.sleep(15)
                
            print(f"\n[{idx+1}/{len(active_platforms)}] Launching {name} tracker...")
            try:
                await func()
            except Exception as e:
                print(f"[Engine] [ERROR] {name} scraper failed: {e}")
                
        print("\n" + "=" * 60)
        print(f"Cycle #{loop_count} completed successfully!")
        print("=" * 60)
        
        if not args.loop:
            break
            
        print("\n[Engine] Waiting 30 seconds before starting the next loop cycle... (Press Ctrl+C to stop)")
        await asyncio.sleep(30)
        loop_count += 1

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
  python app/main.py --loop      # Run in an infinite loop with 30s platform breaks & 2m cycle breaks
"""
    )
    
    parser.add_argument("-a", "--amazon", action="store_true", help="Track Amazon prices")
    parser.add_argument("-m", "--myntra", action="store_true", help="Track Myntra prices")
    parser.add_argument("-f", "--flipkart", action="store_true", help="Track Flipkart prices")
    parser.add_argument("-l", "--loop", action="store_true", help="Keep the engine running in an infinite loop")
    
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
