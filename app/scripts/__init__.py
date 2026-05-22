from .myntra.scraper import track_prices as run_myntra_tracker
from .amazon.scraper import track_prices as run_amazon_tracker
from .flipkart.scraper import track_prices as run_flipkart_tracker

__all__ = [
    "run_myntra_tracker",
    "run_amazon_tracker",
    "run_flipkart_tracker"
]
