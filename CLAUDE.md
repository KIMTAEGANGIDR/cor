# ILIS (Indonesian Legal Information System) 프로젝트

> **KOICA 인도네시아 법률정보시스템 구축 프로젝트**

## 프로젝트 목적

인도네시아 법제처(peraturan.go.id)의 법률 문서를 수집, OCR 처리, 구조화하여 검색 가능한 법률정보시스템을 구축한다.

## 커뮤니케이션
- 사용자에게 항상 존댓말로 응답한다.

### 핵심 목표
1. **완전한 데이터 수집** - 61,000+ 인도네시아 법률 문서 메타데이터 크롤링
2. **PDF 다운로드** - 36,000+ PDF 파일 안정적 다운로드 (54GB)
3. **OCR 처리** - 스캔된 PDF를 검색 가능한 텍스트로 변환
4. **구조 파싱** - 법률 문서 계층 구조 추출 (Bab → Pasal → Ayat → Huruf)
5. **표준 포맷** - Akoma Ntoso XML (국제 법률문서 표준) 변환
6. **법률 관계 추출** - 폐지/개정/참조 관계 추출

### 품질 기준
- **KOICA "zero defect tolerance"** 품질 표준 준수
- OCR 정확도 95% 이상 목표
- 모든 문서 구조 파싱 가능해야 함

---

## DO (해야 할 것)

### 코드 작성
- ✅ Python 3.11+ 문법 사용
- ✅ Type hints 필수
- ✅ async/await로 HTTP 작업 처리
- ✅ Dataclass로 모델 정의
- ✅ 에러 발생 시 상세 로깅
- ✅ 재시도 로직 구현 (exponential backoff)
- ✅ 진행 상태 저장 (resume 가능하도록)

### 크롤링
- ✅ Rate limiting 준수 (3초 + jitter)
- ✅ 실패한 항목 별도 추적 및 재시도
- ✅ 이미 처리된 항목 스킵 (멱등성)
- ✅ 체크섬으로 파일 무결성 검증

### OCR
- ✅ PaddleOCR 3.x API 사용 (predict 메서드)
- ✅ GPU 사용 시 paddle.set_device('gpu:0')
- ✅ lang='en' 사용 (라틴 문자)
- ✅ 품질 점수 기록 및 검증

### 데이터
- ✅ SQLite WAL 모드로 동시성 처리
- ✅ 인덱스 적절히 생성 (jenis, tahun, status)
- ✅ 백업 정기적으로 수행

---

## DON'T (하지 말아야 할 것)

### 코드 작성
- ❌ PaddleOCR 2.x API 사용 금지 (ocr.ocr(), use_gpu 파라미터)
- ❌ 하드코딩된 경로 사용 금지 (config 사용)
- ❌ 동기 HTTP 요청 금지 (httpx async 사용)
- ❌ bare except 사용 금지 (구체적 예외 처리)

### 크롤링
- ❌ Rate limit 무시 금지 (서버 부하 방지)
- ❌ 동시 요청 과다 금지 (max 1-4개)
- ❌ 실패 시 무한 재시도 금지 (max 5회)

### OCR
- ❌ lang='id' 사용 금지 (인도네시아어 모델 없음, 'en' 사용)
- ❌ PaddleOCR-VL 사용 금지 (법령 문서에 부적합)
- ❌ OCR 없이 스캔 PDF 텍스트 추출 시도 금지

### 데이터
- ❌ 원본 PDF 수정 금지
- ❌ DB 스키마 임의 변경 금지 (마이그레이션 사용)
- ❌ 메타데이터 없이 PDF만 저장 금지

---

## 현재 작업 상태

👉 **WORKLOG.md** 참조

---

# Development Guidelines

Last updated: 2026-01-24

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

### OCR Pipeline V3 사용법 (레거시 - PaddleOCR 기반)
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

---

## OCR 파이프라인 (공식) - Surya + LLM

> **2026-01-24 공식 채택**

### 파이프라인 흐름

```
PDF → Surya OCR → 규칙 교정 → 품질 검증
                                 ↓
               ┌─────────────────┴─────────────────┐
               │                                   │
         ≥95% PASS                           <95%
         (자동 승인)                              ↓
                             ┌───────────────────┴───────────────┐
                             │                                   │
                      90-95% WARNING                       <90% FAIL
                             ↓                                   │
                      최대 5회 재처리 (300 DPI)                  │
                             ↓                                   │
                      개선 안됨 ────────────────────────────────→│
                                                                 ↓
                                                          LLM 교정
                                                    (Claude Sonnet via OpenRouter)
                                                                 ↓
                                                   ┌─────────────┴─────────────┐
                                                   │                           │
                                             LLM 확신 ≥90%              LLM 애매함
                                             + 품질 ≥95%                     ↓
                                             (자동 승인)                 인간 검토
```

### 구성 요소

| 파일 | 역할 |
|------|------|
| `surya_pipeline.py` | Surya OCR 래퍼, LAMPIRAN 자동 제외, CLI |
| `quality_validator.py` | 품질 검증 (KOICA 95% 기준), AutomatedPipeline |
| `llm_corrector.py` | LLM 교정 (OpenRouter → Claude Sonnet) |
| `ocr_corrector.py` | 규칙 기반 교정 (0↔O, 1↔I 등) |
| `indonesian_dict.py` | 인도네시아어 사전 + Sastrawi 스테머 |

### 품질 기준 (KOICA zero-defect)

| 등급 | 점수 | 조치 |
|------|------|------|
| **PASS** | ≥95% | 자동 승인, 인간 개입 불필요 |
| **WARNING** | 90-95% | 최대 5회 재처리 시도 |
| **FAIL** | <90% | LLM 교정 → 인간 검토 |

### 품질 점수 구성

```
종합 점수 = (OCR 신뢰도 × 0.45) + (사전 검증 × 0.35) + (법률 용어 × 0.15) + (구조 패턴 × 0.05)
```

### 사용법

```bash
# 환경 변수 설정 (.env 파일)
OPENROUTER_API_KEY=sk-or-v1-xxxxx

# 단일 PDF 처리
PYTHONPATH=. python -m peraturan.src.ocr.surya_pipeline process /path/to/file.pdf

# 배치 처리 (품질 검증만)
PYTHONPATH=. python -m peraturan.src.ocr.surya_pipeline batch --limit 100

# 자동화 파이프라인 (재처리 + LLM 교정 포함)
PYTHONPATH=. python -m peraturan.src.ocr.surya_pipeline auto --limit 100

# PDF 섹션 분석 (LAMPIRAN 감지)
PYTHONPATH=. python -m peraturan.src.ocr.surya_pipeline analyze /path/to/file.pdf

# LLM 연결 테스트
PYTHONPATH=. python -m peraturan.src.ocr.llm_corrector --test
```

### 출력 디렉토리

```
/tmp/ocr_auto_output/
├── approved/           # 자동 승인된 텍스트 (.txt + .json)
├── llm_queue/          # LLM 처리 대기
└── human_queue/        # 인간 검토 대기
```

### 주의사항

- Surya OCR 설치 필요: `pip install surya-ocr`
- OpenRouter API 키 필요 (LLM 교정용)
- GPU 권장 (Surya는 GPU에서 훨씬 빠름)
- LAMPIRAN(부록) 섹션은 자동으로 제외됨

<!-- MANUAL ADDITIONS END -->
