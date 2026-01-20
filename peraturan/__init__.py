"""Peraturan Crawler - Indonesian legal document crawler for peraturan.go.id."""

import sys
from pathlib import Path

# Make 'src' importable when running as installed package
# This allows 'from src.xxx import yyy' to work both:
# 1. When running from peraturan/ with PYTHONPATH=.
# 2. When running as installed package via entry point
_src_path = Path(__file__).parent / "src"
if _src_path.exists() and str(_src_path.parent) not in sys.path:
    sys.path.insert(0, str(_src_path.parent))
