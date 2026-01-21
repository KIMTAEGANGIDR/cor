"""
Turbo OCR Pipeline v3.0 - GPU를 터지게 만드는 파이프라인

핵심 전략:
1. Multiprocessing으로 GIL 우회 (CPU 병렬화)
2. 거대한 이미지 버퍼 (GPU가 절대 굶주리지 않게)
3. 여러 PaddleOCR 인스턴스 동시 실행 (CUDA 스트림 활용)
4. DPI 낮춰서 I/O 부담 감소
5. 모든 것을 비동기로

목표: GPU 50%+ 활용률
"""

import os
import sys
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from multiprocessing import Process, Queue, Manager, cpu_count
from queue import Empty
import threading
from concurrent.futures import ThreadPoolExecutor
import tempfile

import numpy as np
from PIL import Image
import fitz  # PyMuPDF

# PaddleOCR
try:
    from paddleocr import PaddleOCR
    PADDLE_AVAILABLE = True
except ImportError:
    PADDLE_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class TurboConfig:
    """터보 설정"""
    # 이미지 설정
    dpi: int = 150  # 200→150으로 낮춤 (속도 우선)
    max_size: int = 2000  # 최대 이미지 크기

    # 병렬화 설정
    loader_workers: int = 8  # PDF 로더 프로세스 수
    ocr_workers: int = 2  # OCR 인스턴스 수 (GPU 스트림)
    saver_workers: int = 4  # 저장 스레드 수

    # 버퍼 설정
    image_buffer_size: int = 128  # 이미지 버퍼 (크게!)
    result_buffer_size: int = 64  # 결과 버퍼

    # OCR 설정
    batch_size: int = 16  # 배치 크기
    use_gpu: bool = True
    gpu_mem_fraction: float = 0.8  # GPU 메모리 80% 사용

    # 출력
    output_dir: Path = None


@dataclass
class ImageTask:
    """이미지 처리 태스크"""
    doc_id: str
    page_num: int
    image: np.ndarray
    pdf_path: str


@dataclass
class OCRResult:
    """OCR 결과"""
    doc_id: str
    page_num: int
    text: str
    confidence: float
    boxes: List = field(default_factory=list)


def pdf_to_images_worker(
    pdf_queue: Queue,
    image_queue: Queue,
    config: TurboConfig,
    worker_id: int,
    stop_event
):
    """PDF → 이미지 변환 워커 (별도 프로세스)"""
    logger.info(f"[Loader-{worker_id}] 시작")

    while not stop_event.is_set():
        try:
            task = pdf_queue.get(timeout=1.0)
            if task is None:  # 종료 신호
                break

            pdf_path, doc_id = task

            try:
                doc = fitz.open(pdf_path)
                for page_num in range(len(doc)):
                    page = doc[page_num]

                    # DPI 설정으로 렌더링
                    mat = fitz.Matrix(config.dpi / 72, config.dpi / 72)
                    pix = page.get_pixmap(matrix=mat)

                    # numpy 배열로 변환
                    img = np.frombuffer(pix.samples, dtype=np.uint8)
                    img = img.reshape(pix.height, pix.width, pix.n)

                    # RGB로 변환 (RGBA면)
                    if pix.n == 4:
                        img = img[:, :, :3]

                    # 크기 제한
                    h, w = img.shape[:2]
                    if max(h, w) > config.max_size:
                        scale = config.max_size / max(h, w)
                        new_h, new_w = int(h * scale), int(w * scale)
                        pil_img = Image.fromarray(img)
                        pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)
                        img = np.array(pil_img)

                    # 큐에 추가
                    image_task = ImageTask(
                        doc_id=doc_id,
                        page_num=page_num,
                        image=img,
                        pdf_path=pdf_path
                    )

                    while not stop_event.is_set():
                        try:
                            image_queue.put(image_task, timeout=1.0)
                            break
                        except:
                            continue

                doc.close()

            except Exception as e:
                logger.error(f"[Loader-{worker_id}] {pdf_path} 실패: {e}")

        except Empty:
            continue
        except Exception as e:
            logger.error(f"[Loader-{worker_id}] 오류: {e}")

    logger.info(f"[Loader-{worker_id}] 종료")


def ocr_worker(
    image_queue: Queue,
    result_queue: Queue,
    config: TurboConfig,
    worker_id: int,
    stop_event,
    stats: dict
):
    """OCR 워커 (별도 프로세스, GPU 사용)"""
    logger.info(f"[OCR-{worker_id}] 초기화 중...")

    # PaddleOCR 초기화 (각 워커가 자체 인스턴스)
    ocr = PaddleOCR(
        use_angle_cls=True,
        lang='en',  # latin 기반
        use_gpu=config.use_gpu,
        show_log=False,
        gpu_mem=int(24000 * config.gpu_mem_fraction / config.ocr_workers),  # 메모리 분배
    )

    logger.info(f"[OCR-{worker_id}] 준비 완료")

    batch = []
    batch_meta = []  # (doc_id, page_num) 쌍
    processed = 0

    while not stop_event.is_set():
        try:
            # 배치 수집
            while len(batch) < config.batch_size:
                try:
                    task = image_queue.get(timeout=0.1)
                    if task is None:  # 종료 신호
                        stop_event.set()
                        break
                    batch.append(task.image)
                    batch_meta.append((task.doc_id, task.page_num))
                except Empty:
                    break

            if not batch:
                continue

            # 배치 OCR 실행
            start_time = time.time()

            for i, img in enumerate(batch):
                try:
                    result = ocr.ocr(img, cls=True)

                    if result and result[0]:
                        texts = []
                        confs = []
                        boxes = []

                        for line in result[0]:
                            if line and len(line) >= 2:
                                box, (text, conf) = line[0], line[1]
                                texts.append(text)
                                confs.append(conf)
                                boxes.append(box)

                        ocr_result = OCRResult(
                            doc_id=batch_meta[i][0],
                            page_num=batch_meta[i][1],
                            text="\n".join(texts),
                            confidence=sum(confs) / len(confs) if confs else 0,
                            boxes=boxes
                        )
                    else:
                        ocr_result = OCRResult(
                            doc_id=batch_meta[i][0],
                            page_num=batch_meta[i][1],
                            text="",
                            confidence=0,
                            boxes=[]
                        )

                    result_queue.put(ocr_result)
                    processed += 1

                except Exception as e:
                    logger.error(f"[OCR-{worker_id}] 이미지 처리 실패: {e}")

            elapsed = time.time() - start_time

            # 통계 업데이트
            stats['processed'] = stats.get('processed', 0) + len(batch)
            stats['batch_time'] = elapsed
            stats['pages_per_sec'] = len(batch) / elapsed if elapsed > 0 else 0

            logger.info(
                f"[OCR-{worker_id}] 배치 {len(batch)}장 처리: "
                f"{elapsed:.2f}초 ({len(batch)/elapsed:.1f} pages/sec)"
            )

            batch = []
            batch_meta = []

        except Exception as e:
            logger.error(f"[OCR-{worker_id}] 오류: {e}")
            batch = []
            batch_meta = []

    logger.info(f"[OCR-{worker_id}] 종료 (처리: {processed})")


def result_saver_worker(
    result_queue: Queue,
    config: TurboConfig,
    stop_event,
    stats: dict
):
    """결과 저장 워커 (스레드)"""
    logger.info("[Saver] 시작")
    saved = 0

    output_dir = config.output_dir or Path("/tmp/turbo_ocr_output")
    output_dir.mkdir(parents=True, exist_ok=True)

    while not stop_event.is_set():
        try:
            result = result_queue.get(timeout=1.0)
            if result is None:
                break

            # 텍스트 파일로 저장
            doc_dir = output_dir / result.doc_id
            doc_dir.mkdir(exist_ok=True)

            text_file = doc_dir / f"page_{result.page_num:03d}.txt"
            with open(text_file, 'w', encoding='utf-8') as f:
                f.write(result.text)

            saved += 1
            stats['saved'] = saved

        except Empty:
            continue
        except Exception as e:
            logger.error(f"[Saver] 오류: {e}")

    logger.info(f"[Saver] 종료 (저장: {saved})")


class TurboPipeline:
    """
    터보 OCR 파이프라인

    아키텍처:

    [PDF Queue] → [Loader Process ×8] → [Image Queue (128)]
                                              ↓
    [Result Queue] ← [OCR Process ×2] ← ─────┘
         ↓
    [Saver Thread ×4]
    """

    def __init__(self, config: TurboConfig = None):
        self.config = config or TurboConfig()
        self.manager = Manager()
        self.stats = self.manager.dict()

    def run(self, pdf_dir: Path, output_dir: Path, limit: int = None):
        """파이프라인 실행"""
        self.config.output_dir = Path(output_dir)
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        # PDF 목록 수집
        pdf_files = list(Path(pdf_dir).rglob("*.pdf"))
        if limit:
            pdf_files = pdf_files[:limit]

        logger.info(f"처리할 PDF: {len(pdf_files)}개")
        logger.info(f"설정: DPI={self.config.dpi}, 배치={self.config.batch_size}, "
                    f"로더={self.config.loader_workers}, OCR={self.config.ocr_workers}")

        # 큐 생성
        pdf_queue = Queue(maxsize=len(pdf_files) + self.config.loader_workers)
        image_queue = Queue(maxsize=self.config.image_buffer_size)
        result_queue = Queue(maxsize=self.config.result_buffer_size)

        # 종료 이벤트
        stop_event = self.manager.Event()

        # PDF 작업 추가
        for pdf_path in pdf_files:
            doc_id = pdf_path.stem
            pdf_queue.put((str(pdf_path), doc_id))

        # 종료 신호 추가
        for _ in range(self.config.loader_workers):
            pdf_queue.put(None)

        # 워커 프로세스 시작
        start_time = time.time()

        # 1. PDF 로더 프로세스
        loader_processes = []
        for i in range(self.config.loader_workers):
            p = Process(
                target=pdf_to_images_worker,
                args=(pdf_queue, image_queue, self.config, i, stop_event)
            )
            p.start()
            loader_processes.append(p)

        # 2. OCR 프로세스
        ocr_processes = []
        for i in range(self.config.ocr_workers):
            p = Process(
                target=ocr_worker,
                args=(image_queue, result_queue, self.config, i, stop_event, self.stats)
            )
            p.start()
            ocr_processes.append(p)

        # 3. 결과 저장 스레드
        saver_thread = threading.Thread(
            target=result_saver_worker,
            args=(result_queue, self.config, stop_event, self.stats)
        )
        saver_thread.start()

        # 4. 모니터링 스레드
        def monitor():
            while not stop_event.is_set():
                time.sleep(5)
                processed = self.stats.get('processed', 0)
                saved = self.stats.get('saved', 0)
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0

                logger.info(
                    f"[Monitor] 처리: {processed}, 저장: {saved}, "
                    f"속도: {rate:.1f} pages/sec, "
                    f"이미지큐: {image_queue.qsize()}, 결과큐: {result_queue.qsize()}"
                )

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()

        # 로더 완료 대기
        for p in loader_processes:
            p.join()
        logger.info("모든 로더 완료")

        # 이미지 큐에 종료 신호
        for _ in range(self.config.ocr_workers):
            image_queue.put(None)

        # OCR 완료 대기
        for p in ocr_processes:
            p.join()
        logger.info("모든 OCR 완료")

        # 결과 큐에 종료 신호
        result_queue.put(None)
        stop_event.set()

        # 저장 완료 대기
        saver_thread.join()

        # 최종 통계
        elapsed = time.time() - start_time
        processed = self.stats.get('processed', 0)
        saved = self.stats.get('saved', 0)

        logger.info("=" * 50)
        logger.info(f"완료!")
        logger.info(f"처리 페이지: {processed}")
        logger.info(f"저장 페이지: {saved}")
        logger.info(f"총 시간: {elapsed:.1f}초")
        logger.info(f"평균 속도: {processed/elapsed:.1f} pages/sec")
        logger.info("=" * 50)

        return {
            'processed': processed,
            'saved': saved,
            'elapsed': elapsed,
            'pages_per_sec': processed / elapsed if elapsed > 0 else 0
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Turbo OCR Pipeline v3.0')
    parser.add_argument('--input', '-i', required=True, help='PDF 디렉토리')
    parser.add_argument('--output', '-o', default='/tmp/turbo_ocr_output', help='출력 디렉토리')
    parser.add_argument('--limit', '-l', type=int, help='처리할 PDF 수 제한')
    parser.add_argument('--dpi', type=int, default=150, help='렌더링 DPI (기본: 150)')
    parser.add_argument('--batch', type=int, default=16, help='배치 크기 (기본: 16)')
    parser.add_argument('--loaders', type=int, default=8, help='로더 프로세스 수')
    parser.add_argument('--ocr-workers', type=int, default=2, help='OCR 프로세스 수')
    parser.add_argument('--buffer', type=int, default=128, help='이미지 버퍼 크기')

    args = parser.parse_args()

    config = TurboConfig(
        dpi=args.dpi,
        batch_size=args.batch,
        loader_workers=args.loaders,
        ocr_workers=args.ocr_workers,
        image_buffer_size=args.buffer,
    )

    pipeline = TurboPipeline(config)
    result = pipeline.run(
        pdf_dir=Path(args.input),
        output_dir=Path(args.output),
        limit=args.limit
    )

    print(f"\n처리량: {result['pages_per_sec']:.1f} pages/sec")


if __name__ == '__main__':
    main()
