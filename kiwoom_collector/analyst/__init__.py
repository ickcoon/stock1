"""Independent, offline ARVIS analysis. No collectors, auth, DB or order calls."""
from .chart import analyze as analyze_stock
from .daily_adapter import from_daily_pages

__all__ = ['analyze_stock', 'from_daily_pages']
