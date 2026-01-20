# ILIS OCR Pipeline - GPU Server 전체 가이드

## 프로젝트 개요

**KOICA 인도네시아 법령정보시스템(ILIS)** 구축사업
- 인도네시아 법령 PDF 32,000개 → Akoma Ntoso XML 변환
- 국제 법률 문서 표준 (Akoma Ntoso 3.0) 준수

### 데이터 규모
- 총 문서: 32,517개 PDF
- 총 페이지: 약 150,000페이지
- 용량: 약 54GB (PDF), 3.9GB (헤더 이미지)
- 언어: 인도네시아어

---

## 전체 파이프라인

```
[STAGE 0] 헤더 추출 .......................... ✅ 완료
[STAGE 0] 헤더 OCR ........................... ✅ 완료 (32,514개)
     ↓
[STAGE 0.5] 클러스터링 ....................... 🔄 현재 단계
     ↓
[STAGE 0.5] 패턴 분석 (웹 UI) ................ ⏳ 대기
     ↓
[STAGE 0.6] 패턴 검증 ........................ ⏳ 대기
     ↓
[STAGE 1] 패턴 룰 적용 ....................... ⏳ 대기
     ↓
[STAGE 2] 전체 페이지 OCR (GPU) .............. ⏳ 대기
     ↓
[STAGE 3] Akoma Ntoso XML 변환 ............... ⏳ 대기
     ↓
[STAGE 4] 검증 ............................... ⏳ 대기
```

---

## 디렉토리 구조

```
/mnt/workspace/images/
├── CLAUDE.md              # 이 파일
├── ocr_pipeline.db        # SQLite DB (모든 데이터)
├── headers/               # 헤더 이미지 (32,517개)
│   ├── uu/                # 법률 (UU)
│   ├── pp/                # 정부령 (PP)
│   ├── perpres/           # 대통령령
│   ├── permen/            # 장관령
│   └── other/             # 기타
├── ocr_worker/            # Python 스크립트
│   ├── clusterer.py       # 클러스터링
│   ├── worker.py          # OCR 워커
│   ├── pattern_ui.py      # 패턴 분석 웹 UI (만들어야 함)
│   ├── akn_converter.py   # Akoma Ntoso 변환 (만들어야 함)
│   └── requirements.txt
├── pdfs/                  # PDF 파일들 (아직 없음, 54GB)
├── xml/                   # 출력 XML (생성 예정)
└── exports/               # 최종 내보내기
```

---

## 현재 상태

### DB 테이블 상태

| 테이블 | 레코드 수 | 상태 |
|--------|-----------|------|
| documents | 32,517 | 문서 메타데이터 |
| headers | 32,517 | OCR 완료 (raw_text 있음) |
| clusters | 0 | **클러스터링 필요** |
| pattern_rules | 0 | 패턴 분석 후 생성 |
| ocr_pages | 0 | 전체 OCR 후 생성 |
| akn_outputs | 0 | XML 변환 후 생성 |

---

## 단계별 작업 가이드

### STAGE 0.5-1: 클러스터링

```bash
cd /mnt/workspace/images

# 의존성 설치
pip3 install scikit-learn numpy rich click

# 클러스터링 실행
python3 ocr_worker/clusterer.py run

# 결과 확인
python3 ocr_worker/clusterer.py status
```

**목표**: 32,514개 헤더를 ~100개 클러스터로 그룹화

---

### STAGE 0.5-2: 패턴 분석 웹 UI

클러스터별로 사람이 검토하며 패턴 룰 정의:
- 노이즈 라인 지정 (로고, 워터마크, URL 등)
- 메타데이터 라인 지정 (법령 유형, 번호, 연도)
- 본문 시작점 지정

```bash
# 웹 UI 실행
python3 ocr_worker/pattern_ui.py

# 브라우저에서 접속
# http://192.168.0.113:7860
```

**UI 기능**:
1. 클러스터 목록 표시
2. 클러스터 선택 → 샘플 헤더 이미지 + OCR 텍스트 표시
3. 각 라인을 노이즈/메타데이터/본문으로 분류
4. 저장 → DB에 pattern_rules 생성
5. 다음 클러스터로 이동

---

### STAGE 0.6: 패턴 검증

각 패턴으로 샘플 2개 처리 → 사람이 검토 → 승인/반려

---

### STAGE 2: 전체 페이지 OCR

PDF 전체 페이지를 OCR 처리 (GPU 사용)

```bash
# PDF 파일 필요 (54GB)
# 로컬에서 전송: rsync -avz pdfs/ kim@192.168.0.113:/mnt/workspace/images/pdfs/

# OCR 실행
python3 ocr_worker/full_ocr.py run --batch-size 100
```

---

### STAGE 3: Akoma Ntoso XML 변환

OCR 텍스트 → 구조 파싱 → Akoma Ntoso 3.0 XML

**Akoma Ntoso 매핑**:

| 인도네시아 | Akoma Ntoso | 설명 |
|-----------|-------------|------|
| Judul | `<longTitle>` | 법령 제목 |
| Menimbang | `<preamble><recitals>` | 고려사항 |
| Mengingat | `<preamble><citations>` | 법적 근거 |
| Pasal | `<article>` | 조 |
| Ayat | `<paragraph>` | 항 |
| Huruf | `<point>` | 호 |
| Angka | `<point>` | 목 |

**XML 출력 예시**:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act name="uu-no-1-tahun-2024">
    <meta>
      <identification source="#source">
        <FRBRWork>
          <FRBRthis value="/id/uu/2024/1"/>
          <FRBRuri value="/id/uu/2024/1"/>
          <FRBRdate date="2024-01-15" name="enacted"/>
          <FRBRauthor href="#dpr"/>
          <FRBRcountry value="id"/>
        </FRBRWork>
      </identification>
    </meta>
    <preamble>
      <recitals>
        <recital>Menimbang: bahwa...</recital>
      </recitals>
    </preamble>
    <body>
      <article eId="art_1">
        <num>Pasal 1</num>
        <paragraph eId="art_1__para_1">
          <num>(1)</num>
          <content><p>Dalam undang-undang ini...</p></content>
        </paragraph>
      </article>
    </body>
  </act>
</akomaNtoso>
```

---

### STAGE 4: 검증

- 텍스트 완전성 확인
- 구조 파싱 정확도 확인
- XML 스키마 검증

---

## 명령어 요약

```bash
cd /mnt/workspace/images

# 1. 클러스터링
python3 ocr_worker/clusterer.py run
python3 ocr_worker/clusterer.py status

# 2. 패턴 분석 웹 UI
python3 ocr_worker/pattern_ui.py

# 3. 전체 OCR (PDF 필요)
python3 ocr_worker/full_ocr.py run

# 4. Akoma Ntoso 변환
python3 ocr_worker/akn_converter.py run

# 5. 검증
python3 ocr_worker/validator.py run
```

---

## 알려진 노이즈 패턴

| 패턴 | 설명 | 처리 |
|------|------|------|
| www.djpp.depkumham.go.id | 워터마크 URL | 제거 |
| www.bphn.go.id | 워터마크 URL | 제거 |
| REPUBLIK INDONESIA (단독) | 국가명 | 제거 |
| [로고/가루다] | 국가 문장 | 제거 |
| 페이지 번호 | 숫자만 | 제거 |

---

## 법령 유형 (Jenis)

| 코드 | 인도네시아어 | 한국어 | 우선순위 |
|------|-------------|--------|----------|
| UU | Undang-Undang | 법률 | 100 |
| PERPPU | Peraturan Pemerintah Pengganti UU | 긴급법률대체정부령 | 90 |
| PP | Peraturan Pemerintah | 정부령 | 80 |
| PERPRES | Peraturan Presiden | 대통령령 | 70 |
| PERMEN | Peraturan Menteri | 장관령 | 50 |

---

## 다음 할 일 (순서대로)

1. [ ] **클러스터링 실행** - `python3 ocr_worker/clusterer.py run`
2. [ ] **패턴 분석 웹 UI 개발** - Gradio 또는 Flask
3. [ ] 패턴 분석 (Human-in-the-loop) - ~100개 클러스터 검토
4. [ ] 패턴 검증
5. [ ] PDF 파일 전송 (54GB)
6. [ ] 전체 페이지 OCR
7. [ ] Akoma Ntoso 변환기 개발
8. [ ] XML 변환 실행
9. [ ] 검증 및 수정

---

## 연락처

- 로컬 작업자: kim (macOS)
- 메인 프로젝트: `/Users/kim/GIT/crawling/`
- 문서: `ILIS-OCR-PIPELINE.md`
