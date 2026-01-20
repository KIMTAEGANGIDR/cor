"""
ILIS OCR Worker Configuration
"""
from pathlib import Path

# Paths (GPU Server)
BASE_DIR = Path("/mnt/workspace/images")
HEADERS_DIR = BASE_DIR / "headers"
DB_PATH = BASE_DIR / "ocr_pipeline.db"

# OCR Settings
OCR_LANGUAGES = ['id', 'en']  # Indonesian + English
GPU_ENABLED = True
BATCH_SIZE = 100

# Categories (법령 유형)
CATEGORIES = ['uu', 'pp', 'perpres', 'permen', 'other']

# Processing
MAX_WORKERS = 1  # EasyOCR는 단일 프로세스 권장
RETRY_LIMIT = 3
