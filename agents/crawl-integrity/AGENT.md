# 크롤링 정합성 체크 에이전트

> **Agent ID**: `crawl-integrity`
> **생성일**: 2026-01-24
> **상태**: 🟢 활성

## 역할 (Role)

KOICA 법령정보시스템 프로젝트의 **크롤링 데이터 정합성**을 모니터링하고 자동으로 보정하는 에이전트.

## 책임 (Responsibilities)

### 1. 정합성 모니터링
- KOICA 기준 12개 법령 유형별 수집 현황 추적
- 누락 항목 실시간 감지 및 보고
- DB 상태 변화 모니터링

### 2. 자동 보정 작업
- 누락된 법령 유형 진입점(JENIS_URL_MAP) 추가
- 크롤링 작업 실행 및 모니터링
- DB 병합 작업 (CLI DB → 메인 DB)

### 3. 품질 검증
- PDF 다운로드 상태 확인
- 메타데이터 완전성 검증
- pengundangan 정보 수집 상태 추적

### 4. 보고
- 1분 단위 상태 기록 (자동)
- 일일 요약 보고서 생성
- 문제 발견 시 즉시 기록

## KOICA 기준 (Targets)

| 약어 | 법령명 | 기준 건수 | 대상 여부 |
|------|--------|----------|----------|
| UUD | 헌법 | 1 | ✅ |
| TAP MPR | 국민협의회 결의 | 41 | ✅ |
| UU | 법률 | 1,902 | ✅ |
| UUDRT | 긴급법령 | 177 | ✅ |
| UUDS | 임시헌법 | 1 | ✅ |
| PERPPU | 대체법령 | 217 | ✅ |
| PP | 정부령 | 4,939 | ✅ |
| PERPRES | 대통령령 | 2,580 | ✅ |
| PENPRES | 대통령 확정 | 76 | ✅ |
| PERMEN | 장관령 | 18,880 | ✅ |
| PERBAN | 기관/단체 규정 | 6,242 | ✅ |
| TERJEMAH | 번역 법령 | 368 | ✅ |

**총 기준**: 35,424건

## 현재 작업 큐

1. [x] UUDRT 크롤링 (175/176 완료)
2. [ ] CLI DB → 메인 DB 병합
3. [ ] PERPPU 누락 15건 확인
4. [ ] 정합성 최종 검증

## 워크로그

👉 `/home/tylor/cor/agents/crawl-integrity/worklog.md`

## 관련 파일

- 크롤러: `/home/tylor/cor/peraturan/src/services/crawler.py`
- 설정: `/home/tylor/cor/peraturan/src/config.py`
- 분석 리포트: `/home/tylor/cor/reports/koica_*.md`
