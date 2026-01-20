# GPU Server Setup Guide

## Overview

이 문서는 GPU 서버 (`kim@192.168.0.113`)에서 ILIS OCR 파이프라인의 헤더 OCR 작업을 수행하는 방법을 설명합니다.

### Project: ILIS Indonesian Legal Document Pipeline

- **목적**: 인도네시아 법령 PDF 32,000개를 Akoma Ntoso XML로 변환
- **현재 단계**: STAGE 0 - 헤더 이미지 OCR 처리
- **데이터 위치**: `/mnt/workspace/images/`

---

## Server Info

- **Host**: `192.168.0.113`
- **User**: `kim`
- **Work Directory**: `/mnt/workspace/images/`
- **GPU**: EasyOCR용 GPU 환경 (Docker)

---

## Current Status

| 항목 | 값 |
|------|-----|
| 총 헤더 이미지 | 32,517개 |
| 이미지 크기 | 약 3.8GB |
| 이미지 포맷 | JPG (PDF 1페이지 상단 30%) |
| OCR 대상 언어 | 인도네시아어 (id), 영어 (en) |

### Directory Structure (Server)

```
/mnt/workspace/images/
├── headers/                    # 헤더 이미지들
│   ├── uu/                     # UU (법률) - 1,930개
│   ├── pp/                     # PP (정부령) - 4,351개
│   ├── perpres/                # Perpres (대통령령) - 2,511개
│   ├── permen/                 # Permen (장관령) - 17,978개
│   └── other/                  # 기타 - 5,757개
├── ocr_pipeline.db             # SQLite DB (OCR 상태 관리)
└── ocr_worker/                 # OCR 워커 스크립트
    ├── requirements.txt
    ├── worker.py               # 메인 워커
    └── config.py               # 설정
```

---

## Task: Header OCR Processing

### What the GPU Server Does

1. **헤더 이미지 읽기**: `headers/` 폴더의 JPG 파일들
2. **EasyOCR 실행**: 인도네시아어 + 영어 텍스트 추출
3. **결과 저장**: SQLite DB에 OCR 텍스트 저장
4. **상태 업데이트**: `pending` → `extracted`

### OCR Output Format

```json
{
  "document_id": "uu-no-1-tahun-1945",
  "raw_text": "UNDANG-UNDANG REPUBLIK INDONESIA\nNOMOR 1 TAHUN 1945\nTENTANG...",
  "lines": ["UNDANG-UNDANG REPUBLIK INDONESIA", "NOMOR 1 TAHUN 1945", "TENTANG..."],
  "confidence": 0.95
}
```

---

## Setup Instructions

### 1. Install Dependencies

```bash
# Python 환경 (Docker 내부 또는 시스템)
pip install easyocr torch torchvision

# 또는 requirements.txt 사용
pip install -r /mnt/workspace/images/ocr_worker/requirements.txt
```

### 2. Verify EasyOCR

```bash
python3 -c "import easyocr; reader = easyocr.Reader(['id', 'en'], gpu=True); print('EasyOCR ready!')"
```

### 3. Run OCR Worker

```bash
cd /mnt/workspace/images/ocr_worker

# 테스트 (10개만)
python worker.py --limit 10 --dry-run

# 실제 실행 (배치 처리)
python worker.py --batch-size 100

# 백그라운드 실행
nohup python worker.py --batch-size 100 > ocr.log 2>&1 &
```

---

## Worker Commands

```bash
# 상태 확인
python worker.py status

# 특정 카테고리만 처리
python worker.py --category uu --batch-size 50

# 처리 재개 (중단된 경우)
python worker.py --resume

# 에러난 것만 재처리
python worker.py --retry-errors
```

---

## Database Schema (ocr_pipeline.db)

### headers 테이블

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| document_id | TEXT | 문서 ID (slug) |
| image_path | TEXT | 헤더 이미지 경로 |
| raw_text | TEXT | OCR 추출 텍스트 |
| lines | TEXT | JSON: 라인별 텍스트 |
| ocr_confidence | REAL | OCR 신뢰도 (0-1) |
| status | TEXT | pending/extracted/error |
| error_message | TEXT | 에러 메시지 |
| created_at | TEXT | 생성 시각 |

### Status Values

- `pending`: OCR 대기
- `extracted`: OCR 완료
- `error`: OCR 실패

---

## Expected Processing Time

| Batch Size | Speed (est.) | Total Time |
|------------|--------------|------------|
| 1 | ~2 sec/image | ~18 hours |
| 10 | ~15 sec/batch | ~14 hours |
| 100 | ~90 sec/batch | ~8 hours |

**Note**: GPU 메모리와 모델 로딩 상태에 따라 다름

---

## After Completion

OCR 완료 후:

1. **DB 파일 동기화**: `ocr_pipeline.db`를 로컬로 복사
2. **STAGE 0.5 진행**: 패턴 분석기로 클러스터링 검토 (로컬 작업)

```bash
# 서버에서 로컬로 DB 복사
scp kim@192.168.0.113:/mnt/workspace/images/ocr_pipeline.db ./peraturan/data/
```

---

## Troubleshooting

### GPU Memory Error

```bash
# 배치 사이즈 줄이기
python worker.py --batch-size 10

# 또는 이미지 크기 축소 옵션
python worker.py --resize 0.5
```

### Slow Processing

```bash
# GPU 사용 확인
nvidia-smi

# CUDA 버전 확인
python3 -c "import torch; print(torch.cuda.is_available())"
```

### Database Lock

```bash
# 기존 프로세스 확인
ps aux | grep worker.py

# 강제 종료
pkill -f worker.py
```

---

## Contact

문제 발생 시 로컬에서 Claude Code로 문의:

```
cd /path/to/crawling
claude "GPU 서버 OCR 작업 중 [에러 내용] 발생"
```
