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

### 환경 (2026-01-21 업데이트)
- PaddlePaddle GPU 3.2.0 (CUDA 12.6)
- PaddleOCR 3.3.3 (PP-OCRv5)
- numpy 1.26.4
- pymupdf (PDF 처리용)

### 올바른 문법 (PaddleOCR 3.x / PP-OCRv5)
```python
import os
os.environ['DISABLE_MODEL_SOURCE_CHECK'] = 'True'

import paddle
paddle.set_device('gpu:0')

from paddleocr import PaddleOCR

# PP-OCRv5 초기화
ocr = PaddleOCR(lang='en')

# 이미지 OCR (predict 메서드 사용)
result = ocr.predict('image.png')

# 결과 파싱 (3.x 형식)
res = result[0]
texts = res['rec_texts']
scores = res['rec_scores']

for text, score in zip(texts, scores):
    print(f"[{score:.3f}] {text}")
```

### 주의사항
- PaddleOCR 2.x API (ocr.ocr(), use_gpu 파라미터) 사용 금지
- paddle.set_device('gpu:0')으로 GPU 설정
- lang='id' 대신 lang='en' 사용 (라틴 문자 인식)
- PaddleOCR-VL은 인도네시아 법령 문서에 적합하지 않음 (PP-OCRv5 사용)

### OCR Pipeline V3 사용법
```bash
# DB 스키마 초기화
PYTHONPATH=. python -m peraturan.src.ocr.pipeline_v3 init

# 파이프라인 실행
PYTHONPATH=. python -m peraturan.src.ocr.pipeline_v3 run --limit 100

# 특정 단계만 실행
PYTHONPATH=. python -m peraturan.src.ocr.pipeline_v3 run --stage page_gen --limit 100
PYTHONPATH=. python -m peraturan.src.ocr.pipeline_v3 run --stage ocr --limit 50

# 상태 확인
PYTHONPATH=. python -m peraturan.src.ocr.pipeline_v3 status
```

<!-- MANUAL ADDITIONS END -->
