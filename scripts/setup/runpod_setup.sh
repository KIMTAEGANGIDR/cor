#!/bin/bash
# =============================================================================
# ILIS OCR Pipeline - RunPod 설치 스크립트
# RTX 5090 + CUDA 12.x + PaddleOCR 3.1.0
# =============================================================================

set -e  # 에러 시 중단

echo "=============================================="
echo "ILIS OCR Pipeline - RunPod Setup"
echo "=============================================="

# -----------------------------------------------------------------------------
# 1. 시스템 패키지
# -----------------------------------------------------------------------------
echo "[1/8] 시스템 패키지 설치..."
apt-get update -qq
apt-get install -y -qq tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng git curl

# -----------------------------------------------------------------------------
# 2. Node.js + Claude Code
# -----------------------------------------------------------------------------
echo "[2/8] Node.js + Claude Code 설치..."
if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
fi
cd /root && npm install -g @anthropic-ai/claude-code 2>/dev/null || true

# -----------------------------------------------------------------------------
# 3. 코드 클론
# -----------------------------------------------------------------------------
echo "[3/8] GitHub 클론..."
if [ ! -d "/workspace/cor" ]; then
    git clone https://github.com/KIMTAEGANGIDR/cor.git /workspace/cor
    cd /workspace/cor
    git checkout 001-peraturan-crawler
else
    cd /workspace/cor
    git pull origin 001-peraturan-crawler
fi

# -----------------------------------------------------------------------------
# 4. Python 가상환경
# -----------------------------------------------------------------------------
echo "[4/8] Python 가상환경 설정..."
cd /workspace/cor
rm -rf .venv 2>/dev/null || true
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q

# -----------------------------------------------------------------------------
# 5. Python 패키지 (기본)
# -----------------------------------------------------------------------------
echo "[5/8] Python 기본 패키지 설치..."
pip install -q \
    httpx \
    beautifulsoup4 \
    lxml \
    click \
    rich \
    PyMuPDF \
    pillow \
    pytesseract \
    easyocr

# -----------------------------------------------------------------------------
# 6. PaddlePaddle GPU + PaddleOCR 3.1.0
# -----------------------------------------------------------------------------
echo "[6/8] PaddleOCR 설치 (시간 소요)..."
# CUDA 12.x용 PaddlePaddle
pip install paddlepaddle-gpu -i https://www.paddlepaddle.org.cn/packages/stable/cu129/ --timeout 600 -q || \
pip install paddlepaddle-gpu -i https://mirror.baidu.com/pypi/simple --timeout 600 -q || \
pip install paddlepaddle-gpu --timeout 600 -q

# PaddleOCR
pip install "paddleocr>=3.1.0" -q

# -----------------------------------------------------------------------------
# 7. rclone 설치
# -----------------------------------------------------------------------------
echo "[7/8] rclone 설치..."
if ! command -v rclone &> /dev/null; then
    curl https://rclone.org/install.sh | bash
fi

# -----------------------------------------------------------------------------
# 8. 디렉토리 생성
# -----------------------------------------------------------------------------
echo "[8/8] 디렉토리 생성..."
mkdir -p /workspace/cor/peraturan/data/pdfs
mkdir -p /workspace/cor/bpk/data

echo ""
echo "=============================================="
echo "설치 완료!"
echo "=============================================="
echo ""
echo "다음 단계:"
echo "1. rclone config (토큰 설정)"
echo "2. rclone copy gdrive:ilis/db/ /workspace/cor/peraturan/data/ -P"
echo "3. rclone copy gdrive:ILIS/peraturan_pdfs/ /workspace/cor/peraturan/data/pdfs/ -P --transfers 16"
echo ""
echo "테스트:"
echo "  cd /workspace/cor && source .venv/bin/activate"
echo "  python -c \"import paddle; print('CUDA:', paddle.device.is_compiled_with_cuda())\""
echo "  python -c \"from paddleocr import PaddleOCR; print('PaddleOCR OK')\""
echo ""
