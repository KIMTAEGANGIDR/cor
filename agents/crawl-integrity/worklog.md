# 크롤링 정합성 에이전트 워크로그

> **마지막 업데이트**: 2026-01-24T23:51:12.007920
> **상태**: 🔴 386건 누락

## 현재 작업

**대기 중**


## KOICA 정합성 현황

| 약어 | 법령명 | 기준 | 현재 | 차이 | 상태 | 비고 |
|------|--------|------|------|------|------|------|
| UUD | 헌법 | 1 | 1 | +0 | ✅ | 메인 DB |
| TAP MPR | 국민협의회 결의 | 41 | 41 | +0 | ✅ | 메인 DB |
| UU | 법률 | 1902 | 1907 | +5 | ✅ | 메인 DB |
| UUDRT | 긴급법령 | 177 | 175 | -2 | ⚠️ | 메인 DB |
| UUDS | 임시헌법 | 1 | 0 | -1 | ⚠️ | 메인 DB |
| PERPPU | 대체법령 | 217 | 202 | -15 | ❌ | 메인 DB |
| PP | 정부령 | 4939 | 4969 | +30 | ✅ | 메인 DB |
| PERPRES | 대통령령 | 2580 | 2613 | +33 | ✅ | 메인 DB |
| PENPRES | 대통령 확정 | 76 | 76 | +0 | ✅ | 메인 DB |
| PERMEN | 장관령 | 18880 | 19218 | +338 | ✅ | 메인 DB |
| PERBAN | 기관/단체 규정 | 6242 | 6414 | +172 | ✅ | 메인 DB |
| TERJEMAH | 번역 법령 | 368 | 0 | -368 | ❌ | 메인 DB |

**총 누락: 386건**

## 실행 중인 작업

| PID | CPU | Command |
|-----|-----|---------|
| 243632 | 0.0% | /bin/bash -c -l source /home/tylor/.claude/shell-snapshots/s... |
| 243634 | 3.1% | python3 -u scripts/update_pengundangan.py... |
| 315805 | 0.0% | /bin/bash -c -l source /home/tylor/.claude/shell-snapshots/s... |
| 315828 | 0.1% | python -m peraturan.src.ocr.header_cluster_agent run --clust... |
| 316875 | 0.0% | /bin/bash -c -l source /home/tylor/.claude/shell-snapshots/s... |

## 최근 변경 이력

- `2026-01-24T23:51:12` ✅ UUDRT 크롤링 완료 (175/177)
- `2026-01-24T23:50:01` ✅ UUDRT 크롤링 완료 (175/177)
- `2026-01-24T23:48:51` ✅ UUDRT 크롤링 완료 (175/177)
- `2026-01-24T23:47:41` ✅ UUDRT 크롤링 완료 (175/177)
- `2026-01-24T23:46:31` ✅ UUDRT 크롤링 완료 (175/177)
- `2026-01-24T23:45:21` ✅ UUDRT 크롤링 완료 (175/177)
- `2026-01-24T23:44:11` ✅ UUDRT 크롤링 완료 (175/177)
- `2026-01-24T23:43:02` ✅ UUDRT 크롤링 완료 (175/177)
- `2026-01-24T23:41:52` ✅ UUDRT 크롤링 완료 (175/177)
- `2026-01-24T23:40:41` ✅ UUDRT 크롤링 완료 (175/177)

## 대기 중인 작업

- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)
- [ ] CLI DB → 메인 DB 병합 (UUDRT)

## 완료된 작업

- [x] UUDRT 크롤링 완료 (175/177)
- [x] UUDRT 크롤링 완료 (175/177)
- [x] UUDRT 크롤링 완료 (175/177)
- [x] UUDRT 크롤링 완료 (175/177)
- [x] UUDRT 크롤링 완료 (175/177)

---

*자동 생성: 2026-01-24T23:51:12.007920*
