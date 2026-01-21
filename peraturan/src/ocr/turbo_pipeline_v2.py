"""
Turbo OCR Pipeline v3.1 - 실제로 작동하는 버전

핵심 전략 (v3.0 실패 교훈):
1. GPU/CUDA는 메인 프로세스에서만 (multiprocessing CUDA 문제 회피)
2. CPU 작업(PDF→이미지)만 멀티프로세싱
3. 이미지를 메모리에 미리 로드 (Pre-fetching)
4. 거대 배치로 GPU에 한번에 던지기

아키텍처:
[Phase 1] PDF → Images (multiprocessing, CPU)
    ↓ (메모리에 모든 이미지 로드)
[Phase 2] Images → OCR (단일 프로세스, GPU)
    ↓
[Phase 3] Save results (threading, I/O)
"""

import os
import sys
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from multiprocessing import Pool, cpu_count
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from queue import Queue
import gc

import numpy as np
from PIL import Image
import fitz  # PyMuPDF

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class TurboConfig:
    """설정"""
    dpi: int = 150
    max_image_size: int = 1800
    loader_workers: int = 8
    batch_size: int = 32  # 큰 배치
    use_gpu: bool = True


def load_pdf_images(args) -> List[Tuple[str, int, np.ndarray]]:
    """PDF에서 모든 페이지 이미지 추출 (워커 함수)"""
    pdf_path, doc_id, dpi, max_size = args
    results = []

    try:
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)

            img = np.frombuffer(pix.samples, dtype=np.uint8)
            img = img.reshape(pix.height, pix.width, pix.n)

            if pix.n == 4:
                img = img[:, :, :3]

            # 리사이즈
            h, w = img.shape[:2]
            if max(h, w) > max_size:
                scale = max_size / max(h, w)
                new_h, new_w = int(h * scale), int(w * scale)
                pil_img = Image.fromarray(img)
                pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)
                img = np.array(pil_img)

            results.append((doc_id, page_num, img))

        doc.close()
    except Exception as e:
        logger.error(f"PDF 로드 실패 {pdf_path}: {e}")

    return results


class TurboPipelineV2:
    """
    Turbo OCR Pipeline v3.1

    2단계 파이프라인:
    1. 모든 이미지 미리 로드 (multiprocessing)
    2. GPU로 배치 OCR 처리 (single process)
    """

    def __init__(self, config: TurboConfig = None):
        self.config = config or TurboConfig()
        self.ocr = None

    def _init_ocr(self):
        """OCR 초기화 (메인 프로세스에서만)"""
        if self.ocr is not None:
            return

        from paddleocr import PaddleOCR

        logger.info("PaddleOCR 초기화 중...")
        self.ocr = PaddleOCR(
            use_angle_cls=True,
            lang='en',
            use_gpu=self.config.use_gpu,
            show_log=False,
            enable_mkldnn=True,  # CPU 가속
            use_tensorrt=False,  # TensorRT (있으면 활성화)
        )
        logger.info("PaddleOCR 준비 완료")

    def run(self, pdf_dir: Path, output_dir: Path, limit: int = None) -> dict:
        """파이프라인 실행"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # PDF 목록
        pdf_files = list(Path(pdf_dir).rglob("*.pdf"))
        if limit:
            pdf_files = pdf_files[:limit]

        total_pdfs = len(pdf_files)
        logger.info(f"처리할 PDF: {total_pdfs}개")

        start_time = time.time()

        # ===== Phase 1: 모든 이미지 로드 (CPU 병렬) =====
        logger.info("=" * 50)
        logger.info("Phase 1: PDF → 이미지 로드 (CPU 병렬화)")
        logger.info("=" * 50)

        phase1_start = time.time()

        # 작업 준비
        tasks = [
            (str(pdf), pdf.stem, self.config.dpi, self.config.max_image_size)
            for pdf in pdf_files
        ]

        all_images = []  # (doc_id, page_num, image)

        # 멀티프로세싱으로 로드
        with Pool(self.config.loader_workers) as pool:
            for i, results in enumerate(pool.imap_unordered(load_pdf_images, tasks)):
                all_images.extend(results)
                if (i + 1) % 20 == 0:
                    logger.info(f"로드 진행: {i+1}/{total_pdfs} PDFs, {len(all_images)} pages")

        phase1_time = time.time() - phase1_start
        total_pages = len(all_images)

        logger.info(f"Phase 1 완료: {total_pages} pages in {phase1_time:.1f}s "
                    f"({total_pages/phase1_time:.1f} pages/sec)")

        # ===== Phase 2: OCR 처리 (GPU) =====
        logger.info("=" * 50)
        logger.info("Phase 2: OCR 처리 (GPU)")
        logger.info("=" * 50)

        phase2_start = time.time()
        self._init_ocr()

        results = []
        batch_size = self.config.batch_size
        total_batches = (total_pages + batch_size - 1) // batch_size

        for batch_idx in range(total_batches):
            batch_start = batch_idx * batch_size
            batch_end = min(batch_start + batch_size, total_pages)
            batch = all_images[batch_start:batch_end]

            batch_results = []
            batch_time_start = time.time()

            for doc_id, page_num, img in batch:
                try:
                    result = self.ocr.ocr(img, cls=True)

                    if result and result[0]:
                        texts = []
                        confs = []
                        for line in result[0]:
                            if line and len(line) >= 2:
                                _, (text, conf) = line[0], line[1]
                                texts.append(text)
                                confs.append(conf)

                        batch_results.append({
                            'doc_id': doc_id,
                            'page_num': page_num,
                            'text': "\n".join(texts),
                            'confidence': sum(confs) / len(confs) if confs else 0
                        })
                    else:
                        batch_results.append({
                            'doc_id': doc_id,
                            'page_num': page_num,
                            'text': '',
                            'confidence': 0
                        })

                except Exception as e:
                    logger.error(f"OCR 실패 {doc_id} p{page_num}: {e}")
                    batch_results.append({
                        'doc_id': doc_id,
                        'page_num': page_num,
                        'text': '',
                        'confidence': 0
                    })

            results.extend(batch_results)

            batch_time = time.time() - batch_time_start
            pages_done = batch_end
            elapsed = time.time() - phase2_start
            rate = pages_done / elapsed if elapsed > 0 else 0

            if (batch_idx + 1) % 5 == 0 or batch_idx == total_batches - 1:
                logger.info(
                    f"OCR 진행: {pages_done}/{total_pages} pages "
                    f"({rate:.1f} pages/sec), 배치 {len(batch)}장={batch_time:.2f}s"
                )

        phase2_time = time.time() - phase2_start
        logger.info(f"Phase 2 완료: {total_pages} pages in {phase2_time:.1f}s "
                    f"({total_pages/phase2_time:.1f} pages/sec)")

        # 메모리 정리
        del all_images
        gc.collect()

        # ===== Phase 3: 결과 저장 =====
        logger.info("=" * 50)
        logger.info("Phase 3: 결과 저장")
        logger.info("=" * 50)

        phase3_start = time.time()

        def save_result(r):
            doc_dir = output_dir / r['doc_id']
            doc_dir.mkdir(exist_ok=True)
            text_file = doc_dir / f"page_{r['page_num']:03d}.txt"
            with open(text_file, 'w', encoding='utf-8') as f:
                f.write(r['text'])

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(save_result, results))

        phase3_time = time.time() - phase3_start
        logger.info(f"Phase 3 완료: {len(results)} pages in {phase3_time:.1f}s")

        # ===== 최종 통계 =====
        total_time = time.time() - start_time

        logger.info("=" * 50)
        logger.info("완료!")
        logger.info(f"총 문서: {total_pdfs}")
        logger.info(f"총 페이지: {total_pages}")
        logger.info(f"총 시간: {total_time:.1f}s")
        logger.info(f"전체 속도: {total_pages/total_time:.1f} pages/sec")
        logger.info("-" * 50)
        logger.info(f"Phase 1 (로드): {phase1_time:.1f}s ({total_pages/phase1_time:.1f} p/s)")
        logger.info(f"Phase 2 (OCR):  {phase2_time:.1f}s ({total_pages/phase2_time:.1f} p/s)")
        logger.info(f"Phase 3 (저장): {phase3_time:.1f}s")
        logger.info("=" * 50)

        return {
            'total_pdfs': total_pdfs,
            'total_pages': total_pages,
            'total_time': total_time,
            'pages_per_sec': total_pages / total_time,
            'phase1_time': phase1_time,
            'phase2_time': phase2_time,
            'phase3_time': phase3_time,
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Turbo OCR Pipeline v3.1')
    parser.add_argument('--input', '-i', required=True, help='PDF 디렉토리')
    parser.add_argument('--output', '-o', default='/tmp/turbo_ocr_v2', help='출력 디렉토리')
    parser.add_argument('--limit', '-l', type=int, help='PDF 수 제한')
    parser.add_argument('--dpi', type=int, default=150, help='DPI (기본: 150)')
    parser.add_argument('--batch', type=int, default=32, help='배치 크기 (기본: 32)')
    parser.add_argument('--workers', type=int, default=8, help='로더 워커 수')
    parser.add_argument('--no-gpu', action='store_true', help='GPU 비활성화')

    args = parser.parse_args()

    config = TurboConfig(
        dpi=args.dpi,
        batch_size=args.batch,
        loader_workers=args.workers,
        use_gpu=not args.no_gpu,
    )

    pipeline = TurboPipelineV2(config)
    result = pipeline.run(
        pdf_dir=Path(args.input),
        output_dir=Path(args.output),
        limit=args.limit
    )

    print(f"\n최종 처리량: {result['pages_per_sec']:.1f} pages/sec")


if __name__ == '__main__':
    main()
