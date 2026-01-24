# ILIS 프로젝트 작업 기록

## 현재 상태 (2026-01-24)

### 환경
- **서버**: GCP VM (Ubuntu 22.04)
- **GPU**: NVIDIA L4 (드라이버 활성화됨 ✅)
- **Python**: 3.11 + venv (.venv)

### 진행 중인 작업
- [ ] rclone으로 Google Drive에서 PDF 다운로드 중 (54GB, 36,038개 파일)
  - 경로: `/home/tylor/peraturan_pdfs/`
  - 예상 시간: 몇 시간 (밤새 진행)

### 완료된 작업
- [x] cor 레포 클론 (`/home/tylor/cor`)
- [x] Python 3.11 설치
- [x] 가상환경 생성 및 의존성 설치
- [x] PaddleOCR 설치 (CPU 버전)
- [x] NVIDIA 드라이버 설치 (580)
- [x] rclone 설정 및 다운로드 시작
- [x] VM 재부팅 및 GPU 드라이버 활성화
- [x] PyTorch GPU 버전 설치 (torch 2.10.0 + CUDA)
- [x] Surya OCR 설치 (GPU 가속)

---

## 실시간 상태 (자동 갱신: 2026-01-24 15:57:01)

### PDF 다운로드
- 상태: ⏸️ 중지됨
- 파일: **36,037** / 36,038 (100.0%)
- 용량: **54.3GB** / 54GB (100.5%)

### GPU
- NVIDIA L4
- 메모리: 1683 MiB / 23034 MiB
- 사용률: 0%

### 디스크
- 사용: 88G / 97G (91%)
- 남은 공간: 9.1G

### 실행 중인 작업
- Python: 91769 /bin/bash -c -l source /home/tylor/.claude/shell-snapshots/snapshot-bash-1...
- Python: 128841 /bin/bash -lc /home/tylor/cor/.venv/bin/python - <<'PY' import asyncio im...
## TODO (다음 작업)

### 우선순위 높음
1. [x] ~~VM 재부팅하여 GPU 드라이버 활성화~~ ✅
2. [x] ~~GPU 인식 확인~~ ✅ (torch.cuda.is_available() = True)
3. [x] ~~Surya OCR 설치~~ ✅ (GPU 가속)
4. [ ] PDF 다운로드 완료 후 파일 확인 (진행 중)

### 우선순위 중간
5. [ ] PaddleOCR GPU 버전으로 재설치
6. [ ] OCR 파이프라인 테스트
7. [ ] peraturan.go.id 크롤링으로 메타데이터 업데이트
8. [ ] 데이터베이스 동기화 (PDF ↔ 메타데이터)

### 우선순위 낮음
9. [ ] Neo4j 설정 (법률 관계 그래프)
10. [ ] 웹 대시보드 설정

---

## 작업 로그

### 2026-01-24
- 14:00 - cor 레포 클론
- 14:05 - Python 3.11 설치, venv 생성
- 14:10 - 기본 의존성 + PaddleOCR 설치
- 14:20 - NVIDIA L4 GPU 발견, 드라이버 설치 시작
- 14:35 - 드라이버 설치 완료 (재부팅 필요)
- rclone 백그라운드에서 PDF 다운로드 진행 중

---

## 참고 정보

### 주요 경로
```
/home/tylor/cor/                 # 프로젝트 코드
/home/tylor/cor/.venv/           # Python 가상환경
/home/tylor/peraturan_pdfs/      # 다운로드된 PDF (진행 중)
/home/tylor/cor/peraturan/data/  # 데이터베이스 위치 (예정)
```

### 재부팅 후 실행할 것
```bash
# 1. GPU 확인
nvidia-smi

# 2. rclone 다운로드 재개
rclone copy "gdrive:/" /home/tylor/peraturan_pdfs --drive-root-folder-id "1-8F2cAWZ8hKX9CU4IDof3MufMkfPKRxT" -P --transfers 4 &

# 3. Surya OCR 설치 (옵션)
source .venv/bin/activate && pip install surya-ocr
```

### 주요 명령어
```bash
# 가상환경 활성화
cd /home/tylor/cor && source .venv/bin/activate

# CLI 사용
peraturan status      # 상태 확인
peraturan crawl       # 크롤링
peraturan download    # PDF 다운로드
peraturan dashboard   # 대시보드

# GPU 확인
nvidia-smi
python -c "import torch; print(torch.cuda.is_available())"

# 다운로드 진행 확인
tail -10 /tmp/claude/-home-tylor/tasks/b6b482c.output
```

### 데이터 규모
- 메타데이터: 61,000+ 문서
- PDF 파일: 36,038개, 54GB
- 예상 페이지: 150,000+
# 후속 다운로드 필요
- [ ] 데이터베이스 파일 다운로드: https://drive.google.com/drive/folders/1bQp7giqJmYWq_UuebpVnuwP6TJIRSbkY
  - Folder ID: 1bQp7giqJmYWq_UuebpVnuwP6TJIRSbkY
