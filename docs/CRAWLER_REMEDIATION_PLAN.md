# 크롤러 보완 계획 (비판적 검토 버전)

## 원안 검토 결과

### 검증된 항목

| 제안 | 검증 결과 | 비고 |
|------|----------|------|
| TAP MPR 진입점 필요 | **확인됨** | `/tapmpr` 엔드포인트 존재, 41건 |
| PENPRES 진입점 필요 | **확인됨** | `/penpres` 엔드포인트 존재, 76건 |
| UUD 특수 처리 필요 | **확인됨** | `/uud` 단일 페이지, meta 태그에 복수 PDF |
| 번역본 별도 처리 | **확인됨** | 단, `/terjemahresmi`가 아닌 별도 도메인 |
| `/id/` 링크 필터 확장 | **불필요** | TAP MPR/PENPRES 모두 `/id/` 패턴 사용 |

### 수정이 필요한 항목

| 원안 | 실제 상황 | 수정 내용 |
|------|----------|----------|
| `/files2/*.pdf` 처리 | `/files/*.pdf` 사용 중 | `/files2/` 패턴 확인 불가, 기존 파서 호환 |
| `/terjemahresmi` 크롤러 | 별도 도메인 `e-penerjemahan.peraturan.go.id` | 외부 도메인으로 별도 크롤러 필요 |
| 공포정보(w3) 테이블 | 기존 파서가 이미 `<table>` 파싱 | 추가 파서 불필요, 필드 매핑만 확장 |

---

## 수정된 보완 계획

### Phase 1: 수집 경로 확장 (즉시 적용 가능)

#### 1.1 JENIS_URL_MAP 확장

**파일**: `peraturan/src/services/crawler.py`

```python
# 현재
JENIS_URL_MAP = {
    "UNDANG-UNDANG": "/uu",
    "PERPPU": "/perppu",
    "PERATURAN PEMERINTAH": "/pp",
    "PERATURAN PRESIDEN": "/perpres",
    "PERATURAN MENTERI": "/permen",
    "PERATURAN BADAN/LEMBAGA": "/perban",
}

# 확장안
JENIS_URL_MAP = {
    # 기존
    "UNDANG-UNDANG": "/uu",
    "PERPPU": "/perppu",
    "PERATURAN PEMERINTAH": "/pp",
    "PERATURAN PRESIDEN": "/perpres",
    "PERATURAN MENTERI": "/permen",
    "PERATURAN BADAN/LEMBAGA": "/perban",
    # 추가 (RFP 대상)
    "TAP MPR": "/tapmpr",           # 41건
    "PENPRES": "/penpres",           # 76건
    "UUD RT": "/uudrt",              # 확인 필요
    "INSTRUKSI PRESIDEN": "/inpres", # 존재 여부 확인 필요
}
```

**예상 효과**: +117건 (TAP MPR 41 + PENPRES 76)

#### 1.2 UUD 특수 처리

**파일**: `peraturan/src/services/crawler.py`

UUD는 리스트 페이지가 없고 단일 상세 페이지이므로 별도 seed 처리:

```python
# UUD 전용 크롤링 메서드
async def crawl_uud(self) -> list[Peraturan]:
    """Crawl UUD 1945 and amendments (special case: single page, multiple PDFs)."""
    url = f"{self.config.base_url}/uud"
    response = await self.http.get(url)

    # Extract PDFs from meta tags
    pdf_urls = self.parser.extract_uud_pdfs(response.text)

    # Create one entry per amendment version
    results = []
    for pdf_url in pdf_urls:
        # Parse amendment info from PDF filename
        ...
```

**예상 효과**: +4건 (UUD 1945 원본 + 개정 1-4차)

#### 1.3 링크 필터 수정 (선택적)

**파일**: `peraturan/src/services/parser.py:284-286`

```python
# 현재
if "/id/" not in href:
    return False, None

# 변경 불필요 - TAP MPR/PENPRES 모두 /id/ 패턴 사용
# 예: /id/tap-mpr-no-iv-mpr-1999-tahun-2004
# 예: /id/penpres-no-60-tahun-2020
```

**결론**: 링크 필터 변경 불필요. 기존 `/id/` 패턴이 모든 유형에 적용됨.

---

### Phase 2: PDF 추출 개선

#### 2.1 Meta 태그 PDF 추출 추가

**파일**: `peraturan/src/services/parser.py:99-130`

```python
def _extract_pdf_url(self, soup: BeautifulSoup) -> Optional[str]:
    # 기존 방식 1: <a href="/files/*.pdf">
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/files/" in href and href.endswith(".pdf"):
            ...

    # 추가 방식 2: <meta name="article:tag" content="*.pdf">
    for meta in soup.find_all("meta", {"name": "article:tag"}):
        content = meta.get("content", "")
        if content.endswith(".pdf"):
            return content  # 이미 전체 URL

    return None
```

**영향받는 문서**: UUD, TAP MPR (일부), 기타

#### 2.2 UUD 복수 PDF 처리

**현재 스키마 제약**: `pdf_url TEXT` 단일 값만 저장 가능

**옵션 A**: 별도 attachments 테이블 추가
```sql
CREATE TABLE attachments (
    slug TEXT NOT NULL,
    pdf_url TEXT NOT NULL,
    version TEXT,  -- 'original', 'amendment_1', etc.
    PRIMARY KEY (slug, pdf_url),
    FOREIGN KEY (slug) REFERENCES peraturan(slug)
);
```

**옵션 B**: JSON 컬럼 사용 (SQLite 3.38+)
```python
pdf_urls: Optional[str] = None  # JSON array: ["url1", "url2", ...]
```

**권장**: 옵션 B (스키마 변경 최소화, RFP 범위 내)

---

### Phase 3: 다운로드/폴더 관리

#### 3.1 유형별 폴더 매핑 확장

**파일**: `peraturan/src/services/downloader.py:19-41`

```python
def get_pdf_folder(jenis: str) -> str:
    type_map = {
        # 기존
        "UNDANG-UNDANG": "uu",
        ...
        # 추가
        "TAP MPR": "tapmpr",
        "KETETAPAN MPR": "tapmpr",
        "PENETAPAN PRESIDEN": "penpres",
        "PENPRES": "penpres",
        "UUD": "uud",
        "UUD 1945": "uud",
        "UNDANG-UNDANG DASAR": "uud",
    }
    return type_map.get(jenis.upper(), "other")
```

#### 3.2 PDF 유효성 검사 강화

**파일**: `peraturan/src/services/downloader.py:271-327`

```python
async def _download_single(self, peraturan: Peraturan) -> bool:
    ...
    # 다운로드 후 검증 추가
    if local_path.exists():
        content = local_path.read_bytes()

        # 1. 크기 검사
        if len(content) == 0:
            raise DownloaderError("Empty file")

        # 2. PDF 시그니처 검사 (%PDF-)
        if not content.startswith(b'%PDF-'):
            # HTML이 PDF로 저장된 경우
            local_path.unlink()
            self.db.add_failed_item(FailedItem(
                url=peraturan.pdf_url,
                item_type="pdf",
                error_message="Invalid PDF: HTML content received",
            ))
            return False
```

---

### Phase 4: 번역본 처리 (별도 프로젝트)

#### 4.1 실제 구조

- `/terjemahresmi`: 정보 페이지 (실제 문서 없음)
- `e-penerjemahan.peraturan.go.id`: 실제 번역본 시스템 (별도 도메인)

#### 4.2 권장사항

번역본은 **RFP 핵심 범위 외**로 판단:
- 별도 도메인으로 인증/세션 문제 발생 가능
- 원문 법령 35,424건 확보가 우선
- 향후 확장으로 분리

---

## 구현 우선순위

| 우선순위 | 작업 | 예상 효과 | 난이도 |
|---------|------|----------|--------|
| **P0** | JENIS_URL_MAP 확장 (TAP MPR, PENPRES) | +117건 | 낮음 |
| **P0** | 폴더 매핑 확장 | other 폴더 방지 | 낮음 |
| **P1** | Meta 태그 PDF 추출 | PDF 수집률 향상 | 중간 |
| **P1** | PDF 유효성 검사 | 품질 향상 | 낮음 |
| **P2** | UUD 특수 처리 | +4건, 복수 PDF | 중간 |
| **P3** | 번역본 크롤러 | 별도 프로젝트 | 높음 |

---

## 제안서 반영 문구 (수정본)

> "현행 peraturan.go.id 구조 분석 결과, TAP MPR(41건), PENPRES(76건), UUD(4건) 등 일부 법령 유형이 기본 크롤러 진입점에서 누락됨을 확인하였다. 이에 **유형별 진입점 확장**(JENIS_URL_MAP), **UUD 단일 페이지 특수 처리**, **meta 태그 기반 PDF 링크 추출**, **PDF 유효성 검증 로직**을 추가하여 RFP 대상군 100% 커버를 달성한다.
>
> 번역본(e-penerjemahan.peraturan.go.id)은 별도 도메인으로 제공되어 1차 범위에서 제외하고, 원문 법령 구축 완료 후 2차 확장으로 진행한다."

---

## 검증 항목 (구현 후)

1. **커버리지 테스트**
   - [ ] TAP MPR 41건 전체 수집 확인
   - [ ] PENPRES 76건 전체 수집 확인
   - [ ] UUD 4건 + 복수 PDF 확인

2. **품질 테스트**
   - [ ] PDF 시그니처 검증 동작 확인
   - [ ] `other/` 폴더 신규 파일 없음 확인
   - [ ] Meta 태그 PDF 추출 정상 동작

3. **회귀 테스트**
   - [ ] 기존 35,316건 재수집 없음 확인
   - [ ] 기존 PDF 32,520개 무결성 유지

---

*작성일: 2026-01-20*
*검토자: Claude*
