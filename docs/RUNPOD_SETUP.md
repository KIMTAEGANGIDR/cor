# RunPod OCR Pipeline 환경 설정 가이드

## 권장 환경

### GPU 및 CUDA
| 항목 | 권장 | 비고 |
|------|------|------|
| **CUDA** | **12.9**, 12.6, 11.8 | PaddleOCR 3.0+ 기준 |
| **Python** | 3.10 ~ 3.11 | 3.12도 가능 |
| **GPU VRAM** | 8GB+ | RTX 5090, A40, A100 등 |
| **Driver** | ≥560 | CUDA 12.6+ 기준 |

### RunPod 템플릿 선택

**RTX 5090 사용시 (최신):**
```
runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404
```
- PyTorch 2.8.0 + CUDA 12.8
- PaddleOCR 3.0.3이 RTX 50 시리즈 지원

**A40/A100 사용시 (안정):**
```
runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04
```

### 스토리지 권장
| 용도 | 최소 | 권장 |
|------|------|------|
| 배치 처리 | 80GB | 150GB |
| 전체 PDF | 150GB+ | 200GB |

---

## 설치 순서

### 1. 기본 확인
```bash
# GPU 확인
nvidia-smi

# CUDA 버전 확인
nvcc --version

# Python 버전 확인
python3 --version
```

### 2. 코드 클론
```bash
cd /workspace
git clone https://github.com/KIMTAEGANGIDR/cor.git
cd cor
git checkout 001-peraturan-crawler
```

### 3. Python 환경
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

### 4. 핵심 의존성 설치
```bash
# 기본 패키지
pip install httpx beautifulsoup4 lxml click rich PyMuPDF pillow

# Tesseract (시스템)
apt-get update && apt-get install -y tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng
pip install pytesseract

# EasyOCR
pip install easyocr

# PaddleOCR (CUDA 11.8용)
pip install paddlepaddle-gpu==2.6.0.post118 -f https://www.paddlepaddle.org.cn/whl/linux/mkl/avx/stable.html
pip install paddleocr==2.7.0.3
```

### 5. GPU 확인
```bash
python -c "import paddle; print('CUDA:', paddle.device.is_compiled_with_cuda())"
python -c "import torch; print('PyTorch CUDA:', torch.cuda.is_available())"
```

---

## rclone 설정 (Google Drive)

### 설치
```bash
curl https://rclone.org/install.sh | bash
```

### 설정 (headless)
로컬 PC에서 토큰 생성:
```bash
rclone authorize "drive"
```

RunPod에서 설정:
```bash
rclone config
# n → gdrive → drive → 엔터들 → scope:1 → n → n → 토큰붙여넣기 → n → y
```

### 데이터 다운로드
```bash
# DB (필수, 빠름)
mkdir -p /workspace/cor/peraturan/data /workspace/cor/bpk/data
rclone copy gdrive:ilis/db/ocr_pipeline.db /workspace/cor/peraturan/data/ -P
rclone copy gdrive:ilis/db/peraturan.db /workspace/cor/peraturan/data/ -P
rclone copy gdrive:ilis/db/peraturan_bpk.db /workspace/cor/bpk/data/ -P

# PDF (96GB, 시간 소요)
rclone copy gdrive:ILIS/peraturan_pdfs /workspace/cor/peraturan/data/pdfs -P --transfers 16
```

---

## 파이프라인 실행

### DB 상태 확인
```bash
cd /workspace/cor
source .venv/bin/activate
export PYTHONPATH=/workspace/cor

python -m peraturan.src.ocr.database stats
```

### OCR 처리
```bash
# 테스트 (10개만)
python -m peraturan.src.ocr.pipeline process --limit 10

# 본격 처리
python -m peraturan.src.ocr.pipeline process --jenis "UNDANG-UNDANG" --workers 4
```

---

## 문제 해결

### PaddleOCR CUDA 에러
```bash
# 버전 확인
python -c "import paddle; paddle.utils.run_check()"

# 재설치
pip uninstall paddlepaddle-gpu paddleocr -y
pip install paddlepaddle-gpu==2.6.0.post118 -f https://www.paddlepaddle.org.cn/whl/linux/mkl/avx/stable.html
pip install paddleocr==2.7.0.3
```

### EasyOCR GPU 안 잡힐 때
```bash
pip uninstall torch torchvision -y
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
pip install easyocr
```

---

## 참고 링크
- [PaddlePaddle 설치 가이드](https://www.paddlepaddle.org.cn/documentation/docs/en/install/index_en.html)
- [PaddleOCR GitHub](https://github.com/PaddlePaddle/PaddleOCR)
- [RunPod 문서](https://docs.runpod.io/)
