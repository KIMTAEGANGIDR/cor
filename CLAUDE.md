# Crawling Development Guidelines

Auto-generated from all feature plans. Last updated: 2025-12-29

## Active Technologies
- SQLite (sqlite3 표준 라이브러리)
- Python 3.11+ + httpx, beautifulsoup4, lxml, click, rich
- PyMuPDF (fitz) for PDF processing

## Project Structure

```text
peraturan/              # 법제처 (peraturan.go.id) - 메인
├── src/                # 소스 코드
│   ├── ocr/            # OCR 파이프라인
│   ├── models/
│   ├── services/
│   └── utils/
├── data/               # 데이터
│   ├── pdfs/           # PDF 파일 (54GB)
│   ├── headers/        # 헤더 이미지
│   ├── peraturan.db    # 크롤링 DB
│   └── ocr_pipeline.db # OCR 파이프라인 DB
├── tests/
└── templates/

bpk/                    # BPK (peraturan.bpk.go.id) - 분리됨
├── src/
├── data/
├── tests/
└── templates/

specs/                  # Feature specifications
```

## Features

### 001-peraturan-crawler
- Target: peraturan.go.id
- CLI command: `peraturan`
- Source: `peraturan/src/`

### 002-bpk-crawler
- Target: peraturan.bpk.go.id
- CLI command: `bpk`
- Source: `bpk/src/`
- Excludes local government regulations (Perda, Pergub, Perbup, Perwali)

## Commands

```bash
# Activate virtual environment
source .venv/bin/activate

# Run peraturan.go.id crawler
peraturan crawl --help
peraturan download --help

# Run BPK crawler
bpk crawl --jenis uu       # Crawl UU (laws)
bpk download               # Download PDFs
bpk search <keyword>       # Search database
bpk status                 # Show statistics

# OCR Pipeline
PYTHONPATH=. python -m peraturan.src.ocr.database init
PYTHONPATH=. python -m peraturan.src.ocr.database migrate
PYTHONPATH=. python -m peraturan.src.ocr.database stats
PYTHONPATH=. python -m peraturan.src.ocr.header_extractor status
PYTHONPATH=. python -m peraturan.src.ocr.header_extractor extract --limit 100

# Testing
pytest                           # Run all tests
pytest peraturan/tests/          # Run peraturan tests only
pytest bpk/tests/                # Run BPK tests only

# Linting
ruff check .
```

## Code Style

Python 3.11+: Follow standard conventions
- Type hints required
- Async/await for HTTP operations
- Dataclasses for models

## Recent Changes
- 2025-12-29: Folder restructure (peraturan/, bpk/ separation)
- 2025-12-29: OCR Pipeline DB schema and header extractor
- 002-bpk-crawler: Added BPK JDIH crawler (peraturan.bpk.go.id)
- 001-peraturan-crawler: Initial implementation (peraturan.go.id)

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
