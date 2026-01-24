# KOICA 대상 법령 누락 요약

- DB 경로: /home/tylor/cor/peraturan/data/db/peraturan.db

## 누락(기준 대비 부족) 범주
| 약어 | 법령명(인도네시아) | 법령명(한글) | 기준 건수 | 크롤링 건수 | 누락 건수 |
|---|---|---|---:|---:|---:|
| UUD | Undang-Undang Dasar | 헌법 | 1 | 0 | 1 |
| UUDRT | Undang-Undang Darurat | 긴급법령 | 177 | 0 | 177 |
| UUDS | Undang-Undang Dasar Sementara | 임시헌법 | 1 | 0 | 1 |
| PERPPU | Peraturan Pemerintah Pengganti Undang-undang | 대체법령 | 217 | 202 | 15 |
| TERJEMAH | Terjemah Resmi Peraturan | 번역 법령 | 368 | 0 | 368 |

## 참고
- UUD/UUDRT/UUDS/TERJEMAH는 DB에 해당 유형이 0건이라 개별 누락 목록을 산출할 수 없음
- PERPPU는 202건이 있으나 기준(217건)보다 15건 부족: 아래 CSV로 현재 보유 목록을 제공