# 인도네시아 법령 PDF 파싱 분석 보고서

## 1. 연도별 분포

| 시대 | 건수 | 특징 |
|------|-----:|------|
| 1945-1969 | 725 | 독립 초기, 네덜란드어 혼용, 형식 불규칙 |
| 1970-1979 | 82 | 과도기 |
| 1980-1989 | 101 | 표준화 시작 |
| 1990-1999 | 185 | 현대화 진행 |
| 2000-2009 | 367 | 디지털화 시작 |
| 2010-2019 | 216 | 전자정부 도입 |
| 2020-2025 | 229 | 최신 형식 |

---

## 2. 연도별 헤더 패턴

### Era 1: 1945-1969 (독립 초기)

**특징:**
- 매우 불규칙한 형식
- 네덜란드어/구 철자법 혼용 (`padjak`, `ordonnantie`, `f 1.500`)
- "A.", "B." 등 알파벳 섹션
- 타자기 시대 문서

**헤더 예시:**
```
A. Mencabut Peraturan-Peraturan Yang Bertentangan Dengan Undang-Undang Ini
B. Menetapkan Peraturan Sebagai Berikut
UNDANG-UNDANG TENTANG SUSUNAN, KEKUASAAN DAN JALAN-PENGADILAN MAHKAMAH AGUNG INDONESIA".
```

**문제점:**
- 줄바꿈이 단어 중간에 발생
- 머리글/바닥글 혼입 (`www.djpp.depkumham.go.id`)
- 인용부호 불일치

---

### Era 2: 1970-1999 (과도기)

**특징:**
- 점진적 표준화
- "Menetapkan:" 패턴 등장
- 여전히 수동 타이핑 흔적

**헤더 예시:**
```
Menetapkan: UNDANG-UNDANG TENTANG ...
```

---

### Era 3: 2000-2009 (디지털 초기)

**특징:**
- 스캔+OCR 문서 다수
- "MEMUTUSKAN :" 패턴 일반화
- 페이지 헤더 혼입 ("PRESIDEN", "PRESIDEN REPUBLIK INDONESIA")
- 줄바꿈으로 단어 분리

**헤더 예시:**
```
…
MEMUTUSKAN :
Menetapkan :
UNDANG-UNDANG
TENTANG
ANGGARAN
PENDAPATAN
DAN BELANJA NEGARA TAHUN ANGGARAN 2000.
```

**문제점:**
- `…` (ellipsis) 가 페이지 구분으로 사용됨
- 단어가 여러 줄에 걸쳐 분리
- "PRESIDEN" 헤더가 본문에 혼입

---

### Era 4: 2010-2019 (전자정부)

**특징:**
- 더 나은 PDF 품질
- 구조화된 형식
- 일부 OCR 오류 존재

---

### Era 5: 2020-2025 (최신)

**특징:**
- "SK No" 문서번호 포함
- 더 일관된 구조
- 여전히 일부 OCR 문제

**헤더 예시:**
```
UNDANG-UNDANG TENTANG PERUBAHAN KEEMPAT ATAS
UNDANG.UNDANG NOMOR 4 TAHUN 2OO9 TENTANG
PERTAMBANGAN MINERAL DAN BATUBARA.
SK No250002A
```

**문제점:**
- "UNDANG.UNDANG" (마침표가 하이픈 대신)
- "2OO9" (숫자 0이 문자 O로 OCR됨)
- "SK No" 줄이 문서 중간에 혼입

---

## 3. 공통 문제점

### 3.1 줄바꿈 문제
```
문제: UNDANG-UNDANG
      TENTANG
      ANGGARAN
정상: UNDANG-UNDANG TENTANG ANGGARAN
```

### 3.2 페이지 헤더/푸터 혼입
```
혼입: PRESIDEN
      REPUBLIK INDONESIA
      Pasal 2 ...
정상: Pasal 2 ...
```

### 3.3 워터마크 혼입
```
혼입: www.djpp.depkumham.go.id
      ditjen Peraturan Perundang-undangan
      Pasal 1 ...
정상: Pasal 1 ...
```

### 3.4 OCR 오류
| 오류 | 정상 | 빈도 |
|------|------|------|
| `2OO9` | `2009` | 높음 |
| `UNDANG.UNDANG` | `UNDANG-UNDANG` | 중간 |
| `l` (소문자 L) | `1` | 중간 |
| `…` | (페이지 구분) | 높음 |

---

## 4. 제안 파싱 전략

### Phase 1: 전처리 (Pre-processing)

```python
def preprocess(text):
    # 1. 워터마크 제거
    text = remove_watermarks(text)

    # 2. 페이지 헤더/푸터 제거
    text = remove_headers_footers(text)

    # 3. OCR 오류 수정
    text = fix_ocr_errors(text)

    # 4. 줄바꿈 정규화
    text = normalize_line_breaks(text)

    return text
```

### Phase 2: 연도별 파서 선택

```python
def get_parser(year):
    if year < 1970:
        return LegacyParser()      # 1945-1969
    elif year < 2000:
        return TransitionParser()  # 1970-1999
    elif year < 2020:
        return ModernParser()      # 2000-2019
    else:
        return CurrentParser()     # 2020+
```

### Phase 3: 구조 추출

```
1. 법령 제목 추출
2. Menimbang/Mengingat 추출
3. BAB 분리
4. Pasal 분리
5. Ayat/Huruf/Angka 분리
6. Penjelasan 분리 (UU만)
```

---

## 5. 우선순위 권장

| 순위 | 작업 | 이유 |
|------|------|------|
| 1 | 워터마크/헤더 제거 | 가장 큰 노이즈 제거 |
| 2 | 줄바꿈 정규화 | 단어 분리 해결 |
| 3 | 2020+ 파서 완성 | 최신 법령 우선 |
| 4 | 2000-2019 파서 | 중요 법령 다수 |
| 5 | 1970-1999 파서 | 역사적 법령 |
| 6 | 1945-1969 파서 | 레거시 (복잡) |

---

## 6. 검증 계획

1. **자동 검증**
   - Pasal 번호 연속성 확인
   - BAB 구조 완전성 확인
   - 필수 요소 존재 확인 (제목, Pasal 1)

2. **수동 검증**
   - 연도별 10개 샘플 비교
   - PDF vs 파싱 결과 시각적 확인

---

**작성일:** 2025-12-28
**다음 단계:** 전처리 모듈 개발
