# KOICA 대상 법령 기준 대비 크롤링 분석

- DB 경로: /home/tylor/cor/peraturan/data/db/peraturan.db
- 크롤링 총 건수(현 DB): 41,052
- 대상 범주 합계(현 DB): 35,435
- 비대상 범주 합계(현 DB): 5,617

## 기준 매핑 결과
| 약어 | 법령명(인도네시아) | 법령명(한글) | 대상여부 | 기준 건수 | 크롤링 건수 | 차이(크롤링-기준) |
|---|---|---|---|---:|---:|---:|
| UUD | Undang-Undang Dasar | 헌법 | O | 1 | 0 | -1 |
| TAP MPR | Ketetapan Majelis Permusyawaratan Rakyat | 국민협의회 결의 | O | 41 | 41 | 0 |
| UU | Undang-Undang | 법률 | O | 1,902 | 1,907 | 5 |
| UUDRT | Undang-Undang Darurat | 긴급법령 | O | 177 | 0 | -177 |
| UUDS | Undang-Undang Dasar Sementara | 임시헌법 | O | 1 | 0 | -1 |
| PERPPU | Peraturan Pemerintah Pengganti Undang-undang | 대체법령 | O | 217 | 202 | -15 |
| PP | Peraturan Pemerintah | 정부령(정부규정) | O | 4,939 | 4,969 | 30 |
| PERPRES | Peraturan Presiden | 대통령 령 | O | 2,580 | 2,613 | 33 |
| PENPRES | Penetapan Presiden | 대통령 확정 | O | 76 | 76 | 0 |
| KEPPRES | Keputusan Presiden | 대통령 결정 | X | 5,339 | 5,220 | -119 |
| INPRES | Instruksi Presiden | 대통령 지시 | X | 390 | 385 | -5 |
| PERMEN | Peraturan Menteri | 장관령 | O | 18,880 | 19,216 | 336 |
| PERBAN | Peraturan Badan/Lembaga | 기관/단체 규정 | O | 6,242 | 6,411 | 169 |
| PERDA | Peraturan Daerah | 지방 규정 | X | 19,603 | 12 | -19,591 |
| TERJEMAH | Terjemah Resmi Peraturan | 번역 법령 | O | 368 | 0 | -368 |

## DB jenis -> 약어 매핑
- INSTRUKSI PRESIDEN -> INPRES
- KEPUTUSAN PRESIDEN -> KEPPRES
- KETETAPAN MAJELIS PERMUSYAWARATAN RAKYAT -> TAP MPR
- PENETAPAN PRESIDEN -> PENPRES
- PERATURAN BADAN/LEMBAGA -> PERBAN
- PERATURAN DAERAH -> PERDA
- PERATURAN MENTERI -> PERMEN
- PERATURAN PEMERINTAH -> PP
- PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG -> PERPPU
- PERATURAN PRESIDEN -> PERPRES
- UNDANG-UNDANG -> UU