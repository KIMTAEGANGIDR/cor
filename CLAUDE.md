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

## PaddleOCR 설정 가이드

### 환경
- PaddlePaddle GPU 3.0.0 (CUDA 11.8)
- PaddleOCR 2.9.1
- numpy 1.26.4
- pymupdf (PDF 처리용)

### 올바른 문법 (PaddleOCR 2.9.x)
```python
from paddleocr import PaddleOCR

# GPU 사용
ocr = PaddleOCR(lang='en', use_gpu=True)

# PDF OCR
result = ocr.ocr('파일경로.pdf')

# 결과 파싱
for line in result[0]:
    text, confidence = line[1]
    print(f"[{confidence:.3f}] {text}")
```

### 주의사항
- PaddleOCR 3.x 문법 사용 금지 (use_angle_cls 등 deprecated)
- numpy 2.x 사용 금지 (호환성 문제)
- lang='id' 대신 lang='en' 사용 (라틴 문자 인식)

<!-- MANUAL ADDITIONS END -->
