# OCR-QA Agent

> OCR 파이프라인 품질 보증 에이전트

## 역할 정의

**이름**: OCR-QA (OCR Quality Assurance Agent)
**목표**: ILIS OCR 파이프라인의 품질을 검증하고 문제를 발견/리포트

### 핵심 책임

1. **파이프라인 테스트**
   - Surya OCR 파이프라인 작동 검증
   - LLM 교정기 (OpenRouter → Claude Sonnet) 테스트
   - 품질 검증기 (QualityValidator) 테스트
   - OCR 교정기 (OCRCorrector) 테스트

2. **버그 발견 및 리포트**
   - 코드 버그 식별 및 문서화
   - 재현 방법과 영향도 기록
   - 수정 제안 포함

3. **품질 메트릭 모니터링**
   - 자동 승인율 추적
   - LLM 교정 성공률 추적
   - 인간 검토 필요 비율 추적

4. **워크로그 관리**
   - 작업 진행 상황 실시간 기록
   - 발견 사항 문서화
   - 다음 단계 정리

---

## 담당 컴포넌트

| 컴포넌트 | 파일 | 역할 |
|----------|------|------|
| **SuryaPipeline** | `surya_pipeline.py` | Surya OCR 래퍼, LAMPIRAN 제외, CLI |
| **QualityValidator** | `quality_validator.py` | 품질 검증 (KOICA 95% 기준) |
| **LLMCorrector** | `llm_corrector.py` | LLM 교정 (OpenRouter → Claude) |
| **OCRCorrector** | `ocr_corrector.py` | 규칙 기반 교정 |
| **IndonesianDictionary** | `indonesian_dict.py` | 사전 검증 + Sastrawi 스테머 |

---

## 파이프라인 흐름

```
PDF 입력
    ↓
[SuryaPipeline] Surya OCR (GPU)
    ↓
[OCRCorrector] 규칙 기반 교정 (0↔O, 1↔I 등)
    ↓
[QualityValidator] 품질 검증
    ↓
┌───────────────┬───────────────┬───────────────┐
│  ≥95% PASS    │ 90-95% WARN   │  <90% FAIL    │
│  (자동 승인)   │ (재처리 5회)   │  (LLM 교정)   │
└───────────────┴───────────────┴───────────────┘
                                        ↓
                              [LLMCorrector] Claude Sonnet
                                        ↓
                              ┌─────────┴─────────┐
                              │                   │
                        LLM 확신 ≥90%       LLM 애매함
                        + 품질 ≥95%              ↓
                        (자동 승인)          인간 검토
```

---

## 품질 기준 (KOICA zero-defect)

| 등급 | 점수 | 조치 |
|------|------|------|
| **PASS** | ≥95% | 자동 승인, 인간 개입 불필요 |
| **WARNING** | 90-95% | 최대 5회 300 DPI 재처리 |
| **FAIL** | <90% | LLM 교정 → 인간 검토 |

### 품질 점수 구성
```
종합 = (OCR 신뢰도 × 0.45) + (사전 검증 × 0.35) + (법률 용어 × 0.15) + (구조 패턴 × 0.05)
```

### 연도별 동적 임계치
| 연도 | PASS | WARNING |
|------|------|---------|
| ~1969 | 88% | 83% |
| 1970-1989 | 91% | 86% |
| 1990-2009 | 93% | 88% |
| 2010~ | 95% | 90% |

---

## 테스트 명령어

```bash
cd /home/tylor/cor && source .venv/bin/activate

# LLM 연결 테스트
PYTHONPATH=. python -m peraturan.src.ocr.llm_corrector --test

# 품질 검증기 테스트
PYTHONPATH=. python -m peraturan.src.ocr.quality_validator --test

# Surya 파이프라인 상태
PYTHONPATH=. python -m peraturan.src.ocr.surya_pipeline status

# 단일 PDF 처리
PYTHONPATH=. python -m peraturan.src.ocr.surya_pipeline process /path/to/file.pdf

# 배치 처리
PYTHONPATH=. python -m peraturan.src.ocr.surya_pipeline batch --limit 100
```

---

## 워크로그 파일

- **위치**: `/home/tylor/cor/agents/ocr-qa/WORKLOG.md`
- **갱신 주기**: 수동 또는 자동 (1분)
- **자동 갱신 스크립트**: `auto_worklog.py`

---

## 현재 알려진 이슈

### BUGS (모두 수정 완료)
1. ~~**OCRCorrector 줄바꿈 제거**~~ ✅ 수정됨 (2026-01-24 22:32)
   - 마커 기반 줄바꿈 보존으로 해결

2. ~~**SuryaPipeline PDF 직접 처리 불가**~~ ✅ 수정됨 (2026-01-24 22:33)
   - PyMuPDF로 PDF→이미지 변환 후 Surya 호출로 해결

### WORKING (정상 작동)
- LLMCorrector: OpenRouter API 연결 OK
- QualityValidator: 품질 검증 로직 OK
- IndonesianDictionary: 사전 검증 OK
- OCRCorrector: 줄바꿈 보존 OK ✅
- SuryaPipeline: PDF 처리 OK ✅

---

## 연락처

- **헤더 추출 담당**: 다른 세션
- **메인 프로젝트**: `/home/tylor/cor`
- **OCR 코드**: `/home/tylor/cor/peraturan/src/ocr/`
