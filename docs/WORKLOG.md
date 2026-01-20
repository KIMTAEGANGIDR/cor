# Work Log

## 2025-12-31 (화)

### 작업 요약
ILIS OCR Pipeline - 헤더 OCR 완료 및 OCR 도구 비교 분석

---

### 1. 환경 복구

**문제**: Google Drive NFS 동기화로 `.venv` 심볼릭 링크 손상
```
OSError: Stale NFS file handle
```

**해결**:
```bash
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pip install gradio easyocr scikit-learn gradio_image_annotation
```

---

### 2. 헤더 OCR 완료 (EasyOCR)

**실행**:
```bash
PYTHONPATH=. nohup .venv/bin/python -m peraturan.src.ocr.header_clusterer ocr --workers 4 -q &
```

**결과**:
```
완료: 32,514 / 32,517 (99.99%)
실패: 3개 (이미지 손상 추정)
소요 시간: ~수 시간 (백그라운드)
```

---

### 3. 패턴 규칙 정의 (20개 클러스터)

기존 516개 클러스터링 데이터 분석으로 3가지 헤더 유형 발견:

| 유형 | 클러스터 | 노이즈 라인 | 특징 |
|------|----------|-------------|------|
| Type A: SALINAN | 1,2,3,5,14,16,17,18 | 0-3 | "SALINAN PRESIDEN REPUBLIK INDONESIA" |
| Type B: LEMBARAN_NEGARA | 4,6,8,9,10,11,12,13 | 1 | "BERITA/TAMBAHAN NEGARA" |
| Type C: PRESIDEN | 0,7,19 | 0-1 | "PRESIDEN REPUBLIK INDONESIA" |

**저장**: `pattern_rules` 테이블에 20개 클러스터 규칙 저장 완료

---

### 4. Header Annotator V2 개발

**경로**: `peraturan/src/ocr/header_annotator_v2.py`

**기능**: HuggingFace 스타일 바운딩 박스 주석 도구
- 이미지에서 드래그로 영역 지정
- 레이블: 🔴 noise, 🔵 metadata, 🟢 body_start
- 클러스터별 규칙 저장

**실행**:
```bash
PYTHONPATH=. python -m peraturan.src.ocr.header_annotator_v2 --port 7867
```

---

### 5. OCR 도구 비교 분석

| 도구 | 정확도 | 속도 | GPU | 비용 |
|------|--------|------|-----|------|
| **EasyOCR** (현재) | ⭐⭐⭐ (90%+) | 느림 | 권장 | 무료 |
| **Claude Vision** | ⭐⭐⭐⭐⭐ (99%+) | 빠름 | 불필요 | API 비용 |
| **Google Vision** | ⭐⭐⭐⭐ (98.7%) | 빠름 | 불필요 | $1.50/1000장 |
| **PaddleOCR** | 설정 따라 다름 | 빠름 | 권장 | 무료 |
| **Surya** | ⭐⭐⭐⭐ | 빠름 | 필수 | 무료 |
| **DeepSeek-OCR** | ⭐⭐⭐⭐⭐ | 매우 빠름 | 필수 | 무료 |

**Claude Vision 테스트 결과**:
- 3개 샘플 비교: EasyOCR 대비 완전성 우수 (긴 제목 잘림 없음)
- 핵심 정보(법령유형, 번호, 연도)는 EasyOCR도 충분

---

### 6. PaddleOCR MCP 서버 설치

```bash
pip install paddleocr-mcp
```

**모드**:
- `local`: PaddlePaddle + PaddleOCR 설치 필요
- `aistudio`: 무료 API (토큰 필요)
- `self_hosted`: 자체 서버

**상태**: 설치 완료, AI Studio 토큰 대기 중

---

### 현재 상태

| 단계 | 상태 | 비고 |
|------|------|------|
| STAGE 0.1 헤더 추출 | ✅ 완료 | 32,517개 |
| STAGE 0.2 헤더 OCR | ✅ 완료 | 32,514개 (EasyOCR) |
| STAGE 0.3 클러스터링 | ⏳ 대기 | Ubuntu + 980Ti에서 실행 예정 |
| STAGE 0.5 패턴 분석 | ✅ 규칙 정의 완료 | 20개 클러스터 |

---

### 다음 작업 (Ubuntu 980Ti)

1. `ocr_pipeline.db` 파일 복사
2. 클러스터링 실행:
   ```bash
   PYTHONPATH=. python -m peraturan.src.ocr.header_clusterer cluster --n-clusters 30
   ```
3. GPU 가속 OCR (Surya/PaddleOCR) 테스트
4. 패턴 규칙 적용해서 노이즈 제거

---

### 참고

**OCR 진행 모니터링**:
```bash
# 진행률 확인
source .venv/bin/activate && python -c "
import sqlite3
conn = sqlite3.connect('peraturan/data/ocr_pipeline.db')
total = conn.execute('SELECT COUNT(*) FROM headers').fetchone()[0]
done = conn.execute('SELECT COUNT(*) FROM headers WHERE raw_text IS NOT NULL').fetchone()[0]
print(f'{done:,}/{total:,} ({done/total*100:.1f}%)')
"
```

---

## 2025-12-29 (일) - 오후

### 작업 요약
STAGE 0.5 패턴 분석 UI 개발 (Gradio 기반)

---

### 1. Triple Viewer 생성

**경로**: `peraturan/src/ocr/triple_viewer.py`

**목적**: PDF vs PyMuPDF vs OCR 3열 비교

**기능**:
| 열 | 내용 |
|---|------|
| 왼쪽 | PDF 원본 이미지 |
| 가운데 | PyMuPDF 텍스트 블록 (폰트, 크기, 볼드/이탤릭, 들여쓰기 정보) |
| 오른쪽 | PaddleOCR 결과 (신뢰도, 텍스트 분류) |

**실행**:
```bash
PYTHONPATH=. python -m peraturan.src.ocr.triple_viewer --port 7864
```

---

### 2. Pattern Analyzer 생성 (핵심)

**경로**: `peraturan/src/ocr/pattern_analyzer.py`

**목적**: 클러스터별 헤더 패턴 분석 + 노이즈 패턴 발견

**기능**:
- 클러스터 선택 (드롭다운, 문서 수 표시)
- 헤더 썸네일 갤러리 (클러스터 내 헤더 비교)
- 헤더 상세 분석 (OCR 라인별 자동 분류)
  - 🗑️ 노이즈 (SALINAN, PRESIDEN, URL 등)
  - 📋 메타데이터 (법령유형, 번호, 연도)
  - ▶️ 본문 시작
- 공통 패턴 분석 (클러스터 내 반복되는 노이즈/메타 패턴 자동 감지)
- 노이즈 패턴 저장 (DB에 규칙 저장)

**실행**:
```bash
PYTHONPATH=. python -m peraturan.src.ocr.pattern_analyzer --port 7865
```

**UI 구조**:
```
┌─────────────────────────────────────────────────────────────┐
│ 클러스터 선택: [#4 - UNDANG-UNDANG (110개) ▼]  [규칙 저장]   │
├─────────────────────────────────────────────────────────────┤
│                   헤더 썸네일 갤러리                          │
├───────────────────────────┬─────────────────────────────────┤
│     헤더 이미지 (선택)      │        OCR 라인 분석             │
│                           │ #1 SALINAN        🗑️ 노이즈      │
│                           │ #2 PRESIDEN       🗑️ 노이즈      │
│                           │ #3 UNDANG-UNDANG  📋 법령유형    │
├───────────────────────────┴─────────────────────────────────┤
│ 📊 클러스터 공통 패턴                                         │
│ 🗑️ 노이즈 후보          │ 📋 메타데이터 패턴                  │
└─────────────────────────────────────────────────────────────┘
```

---

### 3. 기존 뷰어 정리

| 파일 | 포트 | 용도 |
|------|------|------|
| `side_by_side_viewer.py` | 7861 | DB 기반 클러스터/문서 선택 → PDF vs OCR |
| `pdf_parser_viewer.py` | 7862 | PDF 업로드 → 페이지별 OCR |
| `pdf_to_xml_viewer.py` | 7863 | PDF → OCR → Akoma Ntoso XML |
| `triple_viewer.py` | 7864 | PDF vs PyMuPDF vs OCR 3열 비교 |
| `pattern_analyzer.py` | 7865 | **클러스터별 헤더 패턴 분석** (핵심) |

---

### 현재 상태

| 단계 | 상태 | 비고 |
|------|------|------|
| STAGE 0.1 헤더 추출 | ✅ 완료 | 32,517개 |
| STAGE 0.2 헤더 OCR | ✅ 완료 | PaddleOCR |
| STAGE 0.3 클러스터링 | ✅ 완료 | 20개 클러스터 |
| STAGE 0.5 패턴 분석 UI | 🔄 진행 중 | pattern_analyzer.py |

---

### 이슈

**Google Drive NFS 오류**:
- `.venv`가 Google Drive에 동기화되어 NFS 파일 핸들 오류 발생
- 새 터미널에서 실행 시 `ModuleNotFoundError` 또는 `OSError: Stale NFS file handle`
- **해결책**: 로컬에 `.venv_local` 생성 또는 Google Drive 동기화 일시 중지

---

### 다음 작업 (TODO)

- [ ] Pattern Analyzer 갤러리 표시 문제 해결
- [ ] 노이즈 패턴 정의 완료 (클러스터별)
- [ ] 규칙 저장 후 자동 필터링 테스트
- [ ] STAGE 1 (패턴 룰 적용) 구현

---

## 2025-12-29 (일) - 오전

### 작업 요약
ILIS OCR 파이프라인 구축 - STAGE 0 (헤더 클러스터링) 진행

---

### 1. OCR 파이프라인 DB 스키마 설계

**경로**: `peraturan/src/ocr/`

**생성 파일**:
| 파일 | 내용 |
|------|------|
| `schema.py` | 13개 테이블 DDL 정의 |
| `models.py` | 데이터 클래스 및 Enum (DocumentStage, ClusterStatus 등) |
| `database.py` | OCRPipelineDB 클래스, DocumentMigrator |
| `header_extractor.py` | PDF 헤더 이미지 추출기 |
| `header_clusterer.py` | 헤더 OCR + TF-IDF 클러스터링 |

**테이블 구조**:
```
documents (문서 메타)
  └─ headers (헤더 이미지/텍스트)
       └─ clusters (패턴 그룹)
            └─ pattern_rules (처리 룰)
                 └─ validation_history (검증 이력)

ocr_pages → ocr_results → parsed_structure → akn_outputs
```

---

### 2. 폴더 구조 변경

**Before**:
```
data/pdfs/uu/
data/pdfs/pp/
src/
```

**After**:
```
peraturan/data/pdfs/uu/    # 법제처 (peraturan.go.id)
peraturan/data/pdfs/pp/
peraturan/src/
bpk/data/                   # BPK (peraturan.bpk.go.id) - 별도 분리
bpk/src/
```

**변경 파일**:
- `pyproject.toml` - 패키지 경로 업데이트
- `CLAUDE.md` - 프로젝트 구조 문서화

---

### 3. 문서 마이그레이션

**소스**: `peraturan/data/peraturan.db` (35,316개)
**대상**: `peraturan/data/ocr_pipeline.db`

**결과**:
```
마이그레이션: 32,519개 (PDF 있는 문서만)
제외: 2,797개 (PDF 없음)
```

**명령어**:
```bash
PYTHONPATH=. python -m peraturan.src.ocr.database migrate
```

---

### 4. 헤더 이미지 추출 (STAGE 0.1)

**방법**: PyMuPDF로 PDF 1페이지 상단 35%를 JPEG로 추출 (200 DPI)

**결과**:
```
성공: 32,517개
실패: 2개 (손상된 PDF)
출력: peraturan/data/headers/{jenis}/
```

**명령어**:
```bash
PYTHONPATH=. python -m peraturan.src.ocr.header_extractor --workers 8
```

---

### 5. 헤더 OCR 시도 (STAGE 0.2)

**시도 1: EasyOCR (CPU 모드)**
- 설치: `pip install easyocr scikit-learn`
- 테스트: 50개 성공 (속도: ~18개/분)
- 문제: 32,517개 전체 처리에 **~29시간** 소요 예상

**테스트 결과 (OCR 품질)**:
```
uu-no-2-tahun-2025:
  SALINAN
  PRESIDEN
  REPUBLIK INDONESIA
  UNDANG-UNDANG REPUBLIK INDONESIA
  NOMOR 2 TAHUN 2025
  신뢰도: 0.91
```

**클러스터링 테스트** (516개 샘플):
```
20개 클러스터 생성
- Cluster 4 (110개): TAMBAHAN LEMBARAN NEGARA
- Cluster 0,7,19 (40개씩): SALINAN PRESIDEN
- Cluster 8,11 (25개): LEMBARAN NEGARA
```

---

### 6. PDF 텍스트 레이어 분석

**발견**: 100% PDF에 텍스트 레이어 존재

**문제**: 텍스트 레이어 품질이 낮음 (기존 OCR 오류)
```
예시 오류:
- "2OO9" (숫자 0이 알파벳 O)
- "kamnia ftrhan" (karunia Tuhan 오타)
- "dilnrasai" (dikuasai 오타)
```

**결론**: 텍스트 레이어 사용 불가 → OCR 필요

---

### 7. PaddleOCR MCP 설정

**이유**: EasyOCR CPU 모드가 너무 느림 (29시간)

**설치**:
```bash
pipx install paddleocr-mcp
```

**설정** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "paddleocr": {
      "command": "paddleocr_mcp",
      "args": [],
      "env": {
        "PADDLEOCR_MCP_PIPELINE": "PaddleOCR-VL",
        "PADDLEOCR_MCP_PPOCR_SOURCE": "aistudio"
      }
    }
  }
}
```

**상태**: 설정 완료, Claude Desktop 재시작 필요

---

### 현재 상태

| 단계 | 상태 | 비고 |
|------|------|------|
| STAGE 0.1 헤더 추출 | ✅ 완료 | 32,517개 |
| STAGE 0.2 헤더 OCR | 🔄 진행 중 | PaddleOCR MCP 대기 |
| STAGE 0.3 클러스터링 | ⏳ 대기 | 테스트 완료 (20개 클러스터) |
| STAGE 0.5 패턴 분석 UI | ⏳ 대기 | Gradio 개발 필요 |

---

### 다음 작업 (TODO)

- [ ] Claude Desktop 재시작 → PaddleOCR MCP 활성화
- [ ] PaddleOCR로 32,517개 헤더 OCR 처리
- [ ] 전체 클러스터링 실행 (~100개 패턴)
- [ ] Gradio 패턴 분석 UI 개발 (STAGE 0.5)
- [ ] 패턴별 룰 정의 (노이즈/메타/본문 분류)

---

### 참고 파일

- 파이프라인 설계: `ILIS-OCR-PIPELINE.md`
- OCR DB: `peraturan/data/ocr_pipeline.db`
- 헤더 이미지: `peraturan/data/headers/{jenis}/`

---

## 2025-12-26 (목)

### 작업 요약
그래프 시각화 실험 및 기능 분석

---

### 1. 법률 관계 그래프 시각화 (NetworkX + PyVis)

SQLite DB에서 직접 폐지/개정 관계 추출하여 인터랙티브 그래프 생성

**생성 파일**:
| 파일 | 내용 | 노드 수 |
|------|------|--------|
| `data/graph_full.html` | 모든 UU 간 폐지/개정 관계 | 71개 |
| `data/graph_hub_laws.html` | 핵심 허브 법률 네트워크 | 36개 |
| `data/graph_law_evolution.html` | 5대 법률 진화 체인 | 23개 |
| `data/graph_omnibus_impact.html` | UU 11/2020 옴니버스 영향도 | 18개 |

**5대 법률 진화 체인**:
- 세금법(KUP): 1983 → 1994 → 2000 → 2007 → 2008 → 2021
- 광업법: 1960 → 1967 → 2009 → 2020 → 2025
- 건강법: 1960 → 1992 → 2009 → 2023
- 노동법: 1969 → 1997 → 2003 → 2020 → 2023
- 회사법: 1995 → 2007 → 2020

---

### 2. 그래프 DB 실효성 분석

**발견**:
```
폐지 관계: 240개 / 2,107개 UU (11%)
개정 관계: 16개 / 2,107개 UU (0.7%)
```

**결론**:
- 대부분의 법률은 **독립적** (연결 없음)
- 연결이 많은 건 **옴니버스법** 같은 특수 케이스뿐
- 그래프 DB는 **제한적 활용도** → 전문 검색이 더 실용적

---

### 3. 현재 검색 기능 분석 (`src/cli.py:249-361`)

**지원 기능**:
- 제목(tentang) 검색 ✅
- 유형/연도/상태 필터 ✅
- JSON/CSV/테이블 출력 ✅

**미지원**:
- 전문(본문) 검색 ❌
- 조문 검색 ❌

**참고**: `extracted_text`, `parsed_json` 컬럼에 데이터는 있으나 검색에 미활용

---

### 4. Penjelasan (해설) 설명

인도네시아 법률 구조:
```
1. Batang Tubuh (본문) - 실제 조문
2. Penjelasan (해설) - 입법 취지, 조문별 설명
   - Umum: 일반 설명
   - Pasal demi Pasal: 조문별 해석
```

---

### 현재 상태

- 그래프 시각화: 완료 (4개 HTML 파일)
- 그래프 DB 도입: 보류 (실효성 낮음)
- 전문 검색: 미구현 (SQLite FTS5로 가능하나 일반적 기술)

---

## 2025-12-22 (일)

### 작업 요약
대시보드 개선 및 PDF 없는 문서 분석

---

### 1. 대시보드에 CRAWLED 진행률 추가

**문제**: PDF URL 재크롤링 중인데, 대시보드에는 DOWNLOADED: 0으로만 표시됨

**해결**:
- `database.py`: `with_pdf_url` 통계 추가
- `index.html`: CRAWLED 카드 추가 (TOTAL → CRAWLED → DOWNLOADED → REMAINING)
- `base.html`: 진행률 바가 `download_percentage` 사용하도록 수정
- `/api/download-status`: `crawled`, `download_percentage` 반환 추가
- 폴링 간격 5초로 조정 (서버 부하 감소)

**변경 파일**:
- `src/services/database.py:448-455`
- `src/web.py:240-303`
- `templates/index.html:354-420`
- `templates/base.html:86-116`

---

### 2. DB Lock 에러 수정

**문제**: 크롤러와 웹서버 동시 접근 시 "database is locked" 에러

**해결**: `sqlite3.connect()`에 `timeout=30.0` 추가

**변경 위치**: `src/services/database.py:70`

---

### 3. 에러 페이지 RFP 비교 테이블 추가

**문제**: 제안요청서(RFP) 기준과 실제 수집 현황 비교 필요

**해결**: 15개 법령 유형별 RFP 비교 테이블 추가
- 약어, 법령명(한글), 대상 여부, RFP 건수, DB수집, 차이, PDF URL, 다운로드, 진행률
- 미수집 항목 빨간색 강조
- 비대상 항목 (KEPPRES, INPRES, PERDA) 회색 처리

**변경 파일**:
- `src/web.py:348-389` - RFP 카테고리 매핑
- `templates/errors.html:232-346` - 비교 테이블

---

### 4. PDF 없는 문서 분석

**결과**: 2,773건 (7.9%)이 원본 사이트에 PDF 없음

| 패턴 | 건수 | 비율 |
|------|------|------|
| 2015-2019 장관령/기관규정 | 1,107 | 39.9% |
| 2010-2014 동일 | 685 | 24.7% |
| 1945-1979 역사적 문서 | 773 | 27.9% |
| 2020-현재 | 50 | 1.8% |

**핵심 발견**:
- 88.1%가 아직 유효한 법령 (Berlaku)
- 내부 행정 규정 (조직, 인사, 급여)이 대다수
- PERPPU (긴급법령) 70.5%가 PDF 없음 (1959-1960년대)
- ARSIP NASIONAL (국가기록원) 31.8% PDF 없음 (아이러니)

**분석 문서**: `docs/NO_PDF_ANALYSIS.md`

---

### 현재 상태

```
전체 문서: 35,316
PDF URL 수집: 32,543 (92.1%)
PDF URL 없음: 2,773 (7.9%) - 원본 사이트에 없음
다운로드 완료: 2,133 (6.0%)
다운로드 진행 중...
```

---

## 2025-12-20 (금)

### 작업 요약
인도네시아 법령 크롤러(peraturan.go.id) 안정화 및 모니터링 시스템 구축

---

### 1. 에러 페이지 개선 (`src/web.py`, `templates/errors.html`)

**문제**: 에러 목록에서 실패한 PDF URL만 표시되고, 원본 문서 페이지 링크가 없어 디버깅이 어려움

**해결**:
- `/errors` 엔드포인트에서 `failed_items`와 `peraturan` 테이블 JOIN
- 원본 문서 페이지(`source_url`), slug, 제목(`tentang`) 표시
- 클릭하면 peraturan.go.id 원본 페이지로 이동 가능

**변경 파일**:
- `src/web.py:303-326` - SQL JOIN 쿼리 추가
- `templates/errors.html:172-183` - 원본 문서 컬럼 추가

---

### 2. Watchdog 모니터링 시스템 구축 (`src/watchdog.py`)

**문제**: 다운로드 프로세스가 예고 없이 중단되어 수동으로 재시작 필요

**해결**: 자동 모니터링 및 재시작 시스템 구현

**기능**:
- 프로세스 사망 시 자동 재시작
- Stall 감지 (180초 동안 진행 없으면 재시작)
- 최대 100회 재시작 지원
- 30초 간격 상태 체크
- 세션 통계 (다운로드 수, 속도, ETA)
- Direct download / Scheduler 모드 선택 가능

**사용법**:
```bash
# 백그라운드 실행
nohup python -u -m src.watchdog --direct -b 500 > watchdog.log 2>&1 &

# 로그 확인
tail -f watchdog.log

# 종료
pkill -f "src.watchdog"
```

---

### 3. 다운로드 에러 핸들링 강화 (`src/services/downloader.py`)

**문제**: 연속 에러 발생 시 프로세스가 크래시되거나 무한 루프

**해결**:
- 연속 10회 에러 시 30초 대기 후 재시도
- DB 에러 발생 시에도 프로세스 유지
- 에러 메시지 길이 제한 (500자)
- KeyboardInterrupt, Exception 분리 처리
- 상태 업데이트 실패해도 다운로드 계속 진행

**변경 위치**: `src/services/downloader.py:196-269`

---

### 퇴근 시 최종 상태 (16:32)

```
다운로드: 3,393 / 35,316 (9.6%)
세션 다운로드: +1,915 PDFs
속도: 12-16/min
Watchdog: 실행 중 (restart #4, 자동 복구 정상 작동)
ETA: ~44시간
```

**Watchdog 자동 재시작 4회 성공** - 안정적으로 동작 중

---

### 다음 작업 (TODO)

- [ ] 실패한 PDF 재시도 로직 개선
- [ ] 대시보드에 실시간 watchdog 상태 표시
- [ ] 다운로드 완료 후 알림 (이메일/슬랙)
- [ ] 중복 다운로드 방지 최적화
