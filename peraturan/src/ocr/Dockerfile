# =============================================================================
# OCR Pipeline Dockerfile
# 인도네시아 법령 PDF → Akoma Ntoso XML 변환 파이프라인
# =============================================================================
#
# 사용법:
#   로컬 빌드: docker build -t ilis-ocr .
#   로컬 실행: docker run -it --rm -v $(pwd)/data:/workspace/data ilis-ocr
#   GPU 실행:  docker run --gpus all -it --rm -v $(pwd)/data:/workspace/data ilis-ocr
#
# RunPod 사용:
#   이미지 푸시 후 RunPod에서 동일 이미지 사용
# =============================================================================

# Base: PyTorch with CUDA (RunPod 호환)
FROM pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime

# 환경 변수
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TZ=Asia/Jakarta

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 기본 도구
    build-essential \
    curl \
    wget \
    git \
    vim \
    # Tesseract OCR + 인도네시아어
    tesseract-ocr \
    tesseract-ocr-ind \
    tesseract-ocr-eng \
    # PDF 처리
    poppler-utils \
    libpoppler-cpp-dev \
    # 이미지 처리
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    # rclone (Google Drive 동기화)
    && curl https://rclone.org/install.sh | bash \
    # 정리
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 작업 디렉토리
WORKDIR /workspace

# Python 패키지 설치 (requirements.txt)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# PaddleOCR 설치 (GPU 버전)
RUN pip install --no-cache-dir \
    paddlepaddle-gpu==2.5.2.post118 -f https://www.paddlepaddle.org.cn/whl/linux/mkl/avx/stable.html \
    paddleocr>=2.7.0

# 인도네시아어 형태소 분석기 (Sastrawi)
RUN pip install --no-cache-dir Sastrawi

# 소스 코드 복사
COPY . /workspace/ocr

# 데이터 디렉토리 생성
RUN mkdir -p /workspace/data/input \
    /workspace/data/output \
    /workspace/data/checkpoints \
    /workspace/data/logs \
    /workspace/data/manual_review

# 환경 변수 (기본값)
ENV OCR_INPUT_DIR=/workspace/data/input
ENV OCR_OUTPUT_DIR=/workspace/data/output
ENV OCR_CHECKPOINT_DIR=/workspace/data/checkpoints
ENV OCR_LOG_DIR=/workspace/data/logs
ENV OCR_MANUAL_REVIEW_DIR=/workspace/data/manual_review

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import paddle; import paddleocr; print('OK')" || exit 1

# 기본 명령
CMD ["python", "-m", "ocr.pipeline", "--help"]
