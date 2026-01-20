# Peraturan Crawler

인도네시아 법령 크롤러 (peraturan.go.id)

## 설치

```bash
pip install -e .
```

## 사용법

```bash
# 메타데이터 크롤링
peraturan crawl --limit 100

# PDF 다운로드
peraturan download --limit 50

# 상태 확인
peraturan status

# 검색
peraturan search "pendapatan" --year 2024
```

## 개발

```bash
# 테스트 실행
pytest

# 린터 실행
ruff check .
```
