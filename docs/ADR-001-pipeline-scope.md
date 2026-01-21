# ADR-001: 파이프라인 범위 결정

> **상태**: 승인
> **날짜**: 2025-01-21
> **결정자**: 프로젝트 담당자

## 컨텍스트

OCR 파이프라인의 범위를 결정해야 함:
- 텍스트 추출만 할 것인가?
- 구조화 파싱 (BAB/Pasal/Ayat)까지 할 것인가?
- Akoma Ntoso XML 생성까지 할 것인가?
- Neo4j 그래프 DB까지 구축할 것인가?

현재 구현된 파이프라인:
- `pipeline.py`, `turbo_pipeline_v2.py`, `pipeline_v3.py`: OCR 텍스트 추출
- `unified_pipeline.py`: 텍스트 추출 + 구조화 + XML 생성
- `structure_parser.py`: BAB/Pasal/Ayat 파싱
- `akoma_ntoso.py`: XML 생성

## 결정

**서버에서는 OCR 텍스트 추출만 수행한다.**

구조화 파싱, XML 생성, Neo4j 구축은 로컬에서 수행한다.

```
[서버 - GPU]
PDF (55GB) → OCR → ocr_pipeline.db (텍스트)
                         ↓
                    다운로드 (수 GB)
                         ↓
[로컬 - CPU]
텍스트 → StructureParser → structured.db
           ↓
      AkomaNtoso XML
           ↓
      Neo4j 그래프 DB
```

## 근거

### 1. 작업 특성 분리

| 작업 | 리소스 | 위치 |
|------|--------|------|
| OCR | GPU 필수 | 서버 |
| 구조화 파싱 | CPU만 필요 | 로컬 |
| XML 생성 | CPU만 필요 | 로컬 |
| Neo4j | CPU + 메모리 | 로컬 |

OCR만 GPU가 필요하고, 나머지는 전부 CPU 작업이다.

### 2. 파싱 로직의 변동성

`StructureParser`의 정규식 패턴들은 인도네시아 법령 포맷의 비일관성 때문에 계속 수정될 것이다:

```python
BAB_PATTERN = r'BAB\s+([IVXLCDM]+)\.?\s*\n\s*(.+?)(?=\n|Pasal)'
PASAL_PATTERN = r'Pasal\s+(\d+)\s*\.?\s*\n(.*?)(?=Pasal\s+\d+|BAB|...)'
```

로컬에서 빠르게 수정 → 재실행하는 게 효율적이다.

### 3. 데이터 파이프라인 원칙

```
Raw Data (텍스트) → 영구 보관, 변경 없음
    ↓
Processed Data (구조화/XML) → 필요시 재생성 가능
    ↓
Graph DB (Neo4j) → 쿼리용, 언제든 재구축 가능
```

원본 텍스트만 잘 뽑아두면 나머지는 언제든 다시 만들 수 있다.

### 4. 실용적 이점

- 서버 작업 단순화 (OCR만 집중)
- 로컬에서 구조화 실험 자유로움
- Neo4j 스키마 변경 시 재구축 쉬움
- 55GB PDF → 텍스트 DB는 훨씬 작음 (다운로드 빠름)
- 서버 의존성 최소화

## 결과

### 서버 파이프라인 (유지)
- `pipeline_v3.py` 또는 `turbo_pipeline_v2.py` 사용
- 출력: `ocr_pipeline.db`의 `ocr_pages` 테이블 (raw_text)

### 로컬 파이프라인 (별도 구현 필요)
- `ocr_pipeline.db` 다운로드
- `StructureParser`로 구조화
- 필요시 `AkomaNtosoGenerator`로 XML 생성
- 필요시 Neo4j에 로드

### 사용하지 않는 기능
- `unified_pipeline.py`의 `enable_akn_xml` 옵션은 서버에서 사용 안 함
- `structured_documents`, `structured_sections` 테이블은 서버에서 채우지 않음

## 관련 파일

- `/peraturan/src/ocr/pipeline_v3.py`: 서버용 OCR 파이프라인
- `/peraturan/src/services/structure_parser.py`: 로컬용 구조화 파서
- `/peraturan/src/services/akoma_ntoso.py`: 로컬용 XML 생성기
- `/peraturan/src/services/unified_pipeline.py`: 통합 파이프라인 (로컬용)
