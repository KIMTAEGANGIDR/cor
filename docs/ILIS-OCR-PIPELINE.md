# ILIS 인도네시아 법령 OCR 파이프라인

## 프로젝트 개요

KOICA 인도네시아 법령정보시스템(ILIS) 구축사업의 법령 PDF → Akoma Ntoso XML 변환 파이프라인.

### 데이터 규모
- 총 문서: 32,000개 PDF
- 총 페이지: 약 150,000페이지
- 용량: 약 50GB
- 언어: 인도네시아어
- 원본 소스: 이미 크롤링 완료 (Google Drive에 저장됨)

### 핵심 요구사항
- 법령 원본 텍스트 보존 (오타도 그대로 유지)
- 법령 구조 파싱 (Pasal, Ayat, Huruf 등)
- Akoma Ntoso 국제 표준 XML 변환
- KOICA 품질 기준: "오류 허용 불가"

---

## 파이프라인 아키텍처

```
[STAGE 0] 헤더 추출 + 자동 클러스터링
    ↓
[STAGE 0.5] 패턴 분석기 (Human-in-the-loop)
    ↓
[STAGE 0.6] 패턴 검증 루프 (샘플 2개 테스트 → 승인/반려)
    ↓ (반려 시)
[STAGE 0.7] 심층 패턴 수정 (전체 페이지 분석 → 룰 수정 → 재검증)
    ↓
[STAGE 1] 확정된 양식별 처리 룰 적용
    ↓
[STAGE 2] PaddleOCR-VL 처리 (MCP 또는 배치)
    ↓
[STAGE 3] 양식별 구조 매핑 → Akoma Ntoso XML
    ↓
[STAGE 4] 검증
```

---

## STAGE 0: 헤더 기반 양식 분류

### 목적
- 32,000개 문서를 헤더 패턴 기준으로 분류
- 약 100개 패턴 예상 (연대별, 부처별, 법령 유형별)

### 작업 순서

1. **PDF 1페이지 헤더 영역 추출**
   ```python
   import fitz  # PyMuPDF
   
   def extract_header_image(pdf_path, output_path, header_ratio=0.3):
       """PDF 1페이지 상단 30%를 이미지로 추출"""
       doc = fitz.open(pdf_path)
       page = doc[0]
       
       # 상단 30% 영역만
       rect = page.rect
       clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y1 * header_ratio)
       
       # 200 DPI로 렌더링 (용량 최적화)
       mat = fitz.Matrix(200/72, 200/72)
       pix = page.get_pixmap(matrix=mat, clip=clip)
       pix.save(output_path)
       doc.close()
   ```

2. **헤더 OCR 처리**
   - PaddleOCR MCP 사용 (테스트/개발)
   - 또는 배치 처리 (대량)

3. **클러스터링으로 패턴 그룹화**
   ```python
   from sklearn.feature_extraction.text import TfidfVectorizer
   from sklearn.cluster import KMeans
   
   # 헤더 텍스트 → 클러스터링
   vectorizer = TfidfVectorizer()
   X = vectorizer.fit_transform(header_texts)
   kmeans = KMeans(n_clusters=100)  # 예상 패턴 수
   labels = kmeans.fit_predict(X)
   ```

4. **패턴 정의 (수동 검토)**
   - 각 클러스터에서 샘플 2-3개 확인
   - 정규식 또는 키워드 패턴 정의

---

## STAGE 0.5: 패턴 분석기 (Human-in-the-loop)

### 목적
- 자동 클러스터링 결과를 사람이 검토
- 노이즈 / 메타데이터 / 본문 영역 분류
- 패턴 룰 확정

### 분류 기준

| 분류 | 예시 | 처리 |
|------|------|------|
| **노이즈 (제거)** | 로고, 워터마크, 기관번호, URL, "Republik Indonesia" | 완전 제거 |
| **메타데이터** | 법령유형, 번호, 연도, 제정기관 | XML 메타데이터로 추출 |
| **본문** | TENTANG 이후, Menimbang, Pasal 등 | Akoma Ntoso 본문 |

### 패턴 분석기 UI (Gradio)

```
┌─────────────────────────────────────────────────────┐
│  클러스터 #23 (파일 847개)                           │
│  예상 패턴: PERATURAN PEMERINTAH                    │
├─────────────────────────────────────────────────────┤
│  [헤더 이미지 미리보기]                              │
│                                                     │
│  추출된 텍스트:                                      │
│  ─────────────────────────────────────              │
│  [0] 🏛️ [로고 영역]                                 │
│  [1] "PERATURAN PEMERINTAH REPUBLIK INDONESIA"      │
│  [2] "NOMOR 9 TAHUN 1946"                           │
│  [3] "TENTANG"                                      │
│  [4] "SUSUNAN PERATURAN AKAN MENJALANKAN..."        │
│  [5] "www.djpp.depkumham.go.id"                     │
├─────────────────────────────────────────────────────┤
│  각 라인 분류:                                       │
│                                                     │
│  [0] ● 노이즈  ○ 메타  ○ 본문  → 제거               │
│  [1] ○ 노이즈  ● 메타  ○ 본문  → 법령유형           │
│  [2] ○ 노이즈  ● 메타  ○ 본문  → 법령번호           │
│  [3] ○ 노이즈  ○ 메타  ● 본문  → 본문시작           │
│  [4] ○ 노이즈  ○ 메타  ● 본문                       │
│  [5] ● 노이즈  ○ 메타  ○ 본문  → 제거               │
│                                                     │
│  [이전] [저장 & 다음] [건너뛰기]                     │
└─────────────────────────────────────────────────────┘
```

### 작업 흐름

1. **클러스터 로드**: 자동 클러스터링 결과에서 샘플 2-3개 표시
2. **헤더 이미지 확인**: 원본 PDF 헤더 영역 시각적 확인
3. **라인별 분류**: 각 텍스트 라인을 노이즈/메타/본문으로 분류
4. **본문 시작점 지정**: 어디서부터 실제 법령 본문인지 표시
5. **저장 & 다음**: 패턴 룰 저장 후 다음 클러스터로 이동
6. **반복**: 약 100개 클러스터 전체 검토

### 저장되는 패턴 룰 형식

```json
{
  "cluster_023": {
    "name": "PP_1945_style",
    "file_count": 847,
    "noise_lines": [0, 5],
    "metadata_lines": [1, 2],
    "metadata_mapping": {
      "1": "law_type",
      "2": "law_number"
    },
    "body_start_line": 3,
    "notes": "1945-1950년대 초기 PP 양식",
    "reviewed_by": "태강",
    "reviewed_at": "2024-12-29"
  }
}
```

### 예상 작업 시간
- 클러스터당 평균 2-3분
- 100개 클러스터 × 3분 = **약 5시간**
- 하루 2시간씩 = **2-3일**

---

## STAGE 0.6: 패턴 검증 루프 (Human Approval)

### 목적
- 확정된 패턴 룰로 샘플 2개씩 테스트
- 사람이 결과 확인 후 승인/반려
- 반려 시 전체 페이지 분석으로 패턴 수정

### 검증 흐름

```
┌─────────────────────────────────────────────────────┐
│  [STAGE 0.5 완료] 패턴 룰 확정됨                     │
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│  각 패턴별 샘플 2개 자동 처리                        │
│  - 패턴 룰 적용                                     │
│  - PaddleOCR 실행                                   │
│  - 구조 파싱 결과 생성                               │
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│  [검증 UI] 사람이 결과 확인                          │
│                                                     │
│  패턴: PP_1945_style (847개 파일)                   │
│  ─────────────────────────────────────              │
│  샘플 1: pp-no-9-tahun-1946.pdf                     │
│  [원본 PDF 미리보기]  [추출 결과 미리보기]           │
│                                                     │
│  ✓ 노이즈 제거 OK?                                  │
│  ✓ 메타데이터 추출 OK?                              │
│  ✓ 본문 구조 파싱 OK?                               │
│  ─────────────────────────────────────              │
│  샘플 2: pp-no-10-tahun-1946.pdf                    │
│  [원본 PDF 미리보기]  [추출 결과 미리보기]           │
│                                                     │
│  [✅ 승인 - 패턴 확정] [❌ 반려 - 수정 필요]         │
└─────────────────────────────────────────────────────┘
                         ↓
              ┌─────────┴─────────┐
              ↓                   ↓
        [승인됨]              [반려됨]
              ↓                   ↓
     패턴 확정 완료         STAGE 0.7로 이동
     다음 패턴으로              (심층 분석)
```

### 승인 시
- 해당 패턴 `status: "approved"` 로 변경
- 다음 패턴 검증으로 이동

### 반려 시
- `status: "needs_revision"` 으로 변경
- STAGE 0.7 심층 분석으로 이동

---

## STAGE 0.7: 심층 패턴 수정 (반려된 패턴만)

### 목적
- 반려된 패턴의 샘플 PDF 전체 페이지 분석
- 문제점 파악 및 패턴 룰 수정
- 재검증

### 심층 분석 흐름

```
┌─────────────────────────────────────────────────────┐
│  [반려된 패턴]                                       │
│  문제 유형 선택:                                     │
│  ○ 노이즈가 본문에 포함됨                            │
│  ○ 메타데이터 추출 누락/오류                         │
│  ○ 본문 시작점 오류                                  │
│  ○ 구조 파싱 오류 (Pasal/Ayat/Huruf)                │
│  ○ 기타                                             │
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│  샘플 PDF 전체 페이지 이미지 변환                    │
│  - 모든 페이지 → 이미지 (200 DPI)                   │
│  - 페이지별 OCR 결과 표시                            │
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│  [심층 분석 UI]                                      │
│                                                     │
│  페이지 1/15  [◀ 이전] [다음 ▶]                     │
│  ─────────────────────────────────────              │
│  [페이지 이미지]     [OCR 추출 결과]                 │
│                                                     │
│  문제 영역 표시:                                     │
│  - 라인 5: "www.djpp..." → 노이즈 추가              │
│  - 라인 12: "Pasal 1" → 본문 시작점 수정            │
│                                                     │
│  [패턴 룰 수정]                                      │
│  ─────────────────────────────────────              │
│  추가 노이즈 패턴: _______________                  │
│  본문 시작 키워드: _______________                  │
│  구조 파싱 규칙 수정: _______________               │
│                                                     │
│  [수정 저장 & 재검증]                                │
└─────────────────────────────────────────────────────┘
                         ↓
              ┌─────────┴─────────┐
              ↓                   ↓
      [재검증 통과]         [재검증 실패]
              ↓                   ↓
     패턴 확정 완료         심층 분석 반복
                          (또는 수동 처리 플래그)
```

### 최대 반복 횟수
- 심층 분석 최대 3회 반복
- 3회 후에도 실패 시 → `status: "manual_review"` 플래그
- 해당 클러스터는 수동 처리 대상으로 분류

### 저장되는 패턴 룰 형식 (확장)

```json
{
  "cluster_023": {
    "name": "PP_1945_style",
    "file_count": 847,
    "noise_lines": [0, 5],
    "metadata_lines": [1, 2],
    "metadata_mapping": {
      "1": "law_type",
      "2": "law_number"
    },
    "body_start_line": 3,
    "notes": "1945-1950년대 초기 PP 양식",
    
    "status": "approved",
    "validation_history": [
      {
        "round": 1,
        "result": "rejected",
        "reason": "노이즈가 본문에 포함됨",
        "reviewed_at": "2024-12-29T10:00:00"
      },
      {
        "round": 2,
        "result": "approved",
        "reviewed_at": "2024-12-29T11:30:00"
      }
    ],
    "revision_count": 1,
    "final_reviewed_by": "태강",
    "final_reviewed_at": "2024-12-29T11:30:00"
  }
}
```

### 패턴 상태 종류

| 상태 | 의미 | 다음 단계 |
|------|------|----------|
| `draft` | 초기 분류 완료 | STAGE 0.6 검증 |
| `pending_validation` | 검증 대기 중 | 샘플 테스트 |
| `approved` | ✅ 승인됨 | 대량 처리 가능 |
| `needs_revision` | 수정 필요 | STAGE 0.7 심층 분석 |
| `manual_review` | ⚠️ 수동 검토 필요 | 별도 처리 |

---

### 알려진 노이즈 패턴 (공통)

아래는 거의 모든 문서에서 노이즈로 분류되는 항목:

| 패턴 | 설명 |
|------|------|
| `[로고]`, `[Garuda]` | 인도네시아 국가 문장 |
| `REPUBLIK INDONESIA` (단독) | 국가명만 있는 경우 |
| `www.djpp.depkumham.go.id` | 워터마크 URL |
| `ditjen Peraturan Perundang-undangan` | 기관명 |
| 페이지 번호 | 숫자만 있는 경우 |
| 기관 문서 번호 | 예: `No. 123/A/2024` |

---

### 알려진 헤더 패턴 예시

| 패턴 ID | 헤더 패턴 | 법령 유형 | 연대 |
|---------|----------|----------|------|
| PP_1945 | `PERATURAN PEMERINTAH NO. X TAHUN 194X` | PP (구형) | 1945-1950s |
| PP_modern | `PERATURAN PEMERINTAH REPUBLIK INDONESIA NOMOR X TAHUN XXXX` | PP | 1960s~ |
| UU | `UNDANG-UNDANG REPUBLIK INDONESIA` | UU | |
| Perpres | `PERATURAN PRESIDEN` | Perpres | |
| Permen | `PERATURAN MENTERI` | Permen | |

---

## STAGE 1: 확정된 양식별 처리 룰 적용

### 패턴 설정 파일 구조

```python
patterns = {
    "PP_1945": {
        "header_regex": r"PERATURAN PEMERINTAH NO\. \d+ TAHUN 194[5-9]",
        "structure": [
            "title",           # 법령 제목
            "considerans",     # Menimbang (고려사항)
            "legal_basis",     # Mengingat (법적 근거)
            "decides",         # Memutuskan
            "articles",        # Pasal (조항)
            "closing",         # Pasal Penutup
            "signature"        # 서명
        ],
        "article_pattern": r"Pasal\s+(\d+)\.",
        "paragraph_pattern": r"^\s*(\d+)\.\s+",  # Ayat
        "point_pattern": r"^\s*([a-z])\.\s+",     # Huruf
    },
    # ... 100개 패턴
}
```

---

## STAGE 2: PaddleOCR-VL 처리

### 방법 1: MCP (개발/테스트용)

Claude Desktop에서 PaddleOCR MCP 서버 사용.

**설정 (claude_desktop_config.json):**
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

**주의:** Apple Silicon (M4 Pro 등)에서는 PaddlePaddle 네이티브 미지원.
→ `aistudio` 모드로 클라우드 처리 권장.

### 방법 2: 배치 처리 (대량용)

**환경:** Google Colab Pro ($10/월) 또는 RunPod

```python
from paddleocr import PaddleOCR

ocr = PaddleOCR(
    use_angle_cls=True,
    lang='id',  # 인도네시아어
    use_gpu=True,
    show_log=False
)

def process_page(image_path):
    result = ocr.ocr(image_path, cls=True)
    return result
```

### 이미지 변환 최적화

| 설정 | 권장값 | 이유 |
|------|--------|------|
| DPI | 200-250 | 품질/용량 균형 |
| 포맷 | JPG 85% | 용량 절감 |
| 예상 용량 | ~400KB/페이지 | 15만 페이지 = ~60GB |

```python
def pdf_to_images(pdf_path, output_dir, dpi=200):
    """PDF → 이미지 변환"""
    doc = fitz.open(pdf_path)
    for i, page in enumerate(doc):
        mat = fitz.Matrix(dpi/72, dpi/72)
        pix = page.get_pixmap(matrix=mat)
        output_path = f"{output_dir}/page_{i+1:04d}.jpg"
        pix.pil_save(output_path, format="JPEG", quality=85)
    doc.close()
```

---

## STAGE 3: Akoma Ntoso 변환

### 인도네시아 법령 구조 → Akoma Ntoso 매핑

| 인도네시아 | Akoma Ntoso | 설명 |
|-----------|-------------|------|
| Judul | `<longTitle>` | 법령 제목 |
| Menimbang | `<preamble><recitals>` | 고려사항 |
| Mengingat | `<preamble><citations>` | 법적 근거 |
| Memutuskan | `<preamble><formula>` | 결정 |
| Pasal | `<article>` | 조 |
| Ayat | `<paragraph>` | 항 |
| Huruf | `<point>` | 호 |
| Angka | `<point>` | 목 |
| Pasal Penutup | `<wrapUp>` | 종결조항 |
| Tanda Tangan | `<conclusions>` | 서명 |

### 변환 예시

**입력 (텍스트):**
```
Pasal 1.
1. Untuk membentuk komisi...
2. Untuk keperluan pendaftaran...
   a. syarat pertama
   b. syarat kedua
```

**출력 (Akoma Ntoso):**
```xml
<article eId="art_1">
  <num>Pasal 1.</num>
  <paragraph eId="art_1__para_1">
    <num>1.</num>
    <content><p>Untuk membentuk komisi...</p></content>
  </paragraph>
  <paragraph eId="art_1__para_2">
    <num>2.</num>
    <content><p>Untuk keperluan pendaftaran...</p></content>
    <point eId="art_1__para_2__point_a">
      <num>a.</num>
      <content><p>syarat pertama</p></content>
    </point>
    <point eId="art_1__para_2__point_b">
      <num>b.</num>
      <content><p>syarat kedua</p></content>
    </point>
  </paragraph>
</article>
```

---

## STAGE 4: 검증

### 검증 항목

1. **텍스트 완전성**
   - 원본 PDF 텍스트 레이어와 OCR 결과 비교
   - 누락된 텍스트 없는지 확인

2. **구조 완전성**
   - 모든 Pasal이 파싱되었는지
   - 중첩 구조 (Ayat/Huruf) 올바른지

3. **XML 유효성**
   - Akoma Ntoso 스키마 검증

### 검증 결과 플래그

```python
validation_result = {
    "file": "pp-no-9-tahun-1946.pdf",
    "status": "success",  # success, warning, error
    "text_match_rate": 0.98,
    "structure_complete": True,
    "schema_valid": True,
    "manual_review_needed": False,
    "issues": []
}
```

---

## 제거 대상 (노이즈)

아래는 원본 법령 내용이 아니므로 제거:

- `www.djpp.depkumham.go.id` (워터마크)
- `ditjen Peraturan Perundang-undangan` (기관명)
- 페이지 번호

**주의:** 법령 본문의 오타는 수정하지 않음 (원본 보존)

---

## 작업 환경

### 개발/테스트
- **도구:** Claude Desktop + PaddleOCR MCP
- **모드:** AI Studio (클라우드)
- **용도:** 패턴 파악, 파이프라인 검증, 샘플 처리

### 대량 처리
- **환경:** Google Colab Pro ($10/월) 또는 RunPod
- **데이터 소스:** Google Drive (이미 크롤링 완료)
- **처리 방식:** 배치 실행

### Google Drive 연동

```python
# Colab에서
from google.colab import drive
drive.mount('/content/drive')

# 또는 rclone 사용
# rclone sync gdrive:"법령PDF폴더" /workspace/pdfs/
```

---

## 예상 비용

| 단계 | 방법 | 비용 |
|------|------|------|
| 개발/테스트 | MCP (AI Studio) | 무료~저가 |
| 대량 처리 | Colab Pro | $10/월 |
| 대량 처리 (대안) | RunPod | ~$30-50 |

---

## 다음 단계

1. [ ] PaddleOCR MCP 설정 (AI Studio 모드)
2. [ ] 샘플 PDF로 테스트 (이미 3개 있음)
3. [ ] 헤더 추출 스크립트 작성
4. [ ] 헤더 클러스터링 실행 (~100개 그룹)
5. [ ] **패턴 분석기 UI 개발 (Gradio) - STAGE 0.5**
6. [ ] **패턴 분석기로 100개 클러스터 검토**
7. [ ] **패턴 검증 UI 개발 (Gradio) - STAGE 0.6**
8. [ ] **각 패턴 샘플 2개 테스트 → 승인/반려**
9. [ ] **심층 분석 UI 개발 (Gradio) - STAGE 0.7** (반려된 패턴만)
10. [ ] 패턴별 처리 룰 최종 확정
11. [ ] Akoma Ntoso 변환 로직 구현
12. [ ] 검증 로직 구현
13. [ ] 대량 처리 실행

---

## 참고 파일

- 샘플 PDF 위치: `/mnt/user-data/uploads/`
  - `pp-no-2-tahun-1945.pdf`
  - `pp-no-3-tahun-1945.pdf`
  - `pp-no-9-tahun-1946.pdf`
