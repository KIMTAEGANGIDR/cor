"""
OCR 파이프라인 메인 모듈

인도네시아 법령 PDF → Akoma Ntoso XML 변환 파이프라인

Phase 구성:
- Phase 0: 파일럿/캘리브레이션
- Phase 1: 전수 품질 스캔 (페이지 단위)
- Phase 2: 우선순위 배치 처리
- Phase 3: 페이지별 적응형 OCR
- Phase 4: 이중 검증 (구조 + 내용)
- Phase 5: Akoma Ntoso 변환
"""

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Iterator, List

import fitz  # PyMuPDF
from rich.console import Console
from rich.progress import Progress, TaskID
from rich.table import Table

from .quality_scorer import QualityScorer, QualityResult, ProcessingStrategy
from .ensemble_ocr import EnsembleOCR, OCRResult, OCREngine
from .indonesian_dict import IndonesianDictionary


# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
console = Console()


class DocumentTier(Enum):
    """문서 우선순위 티어"""
    TIER1 = 1  # UU (현행 우선)
    TIER2 = 2  # PP, PERPRES
    TIER3 = 3  # PERMEN, PERBAN


class PageStatus(Enum):
    """페이지 처리 상태"""
    PENDING = "pending"
    SCANNING = "scanning"
    SCANNED = "scanned"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"


class DocumentStatus(Enum):
    """문서 처리 상태"""
    PENDING = "pending"
    SCANNING = "scanning"
    SCANNED = "scanned"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class PageResult:
    """페이지 처리 결과"""
    page_num: int
    status: PageStatus
    strategy: ProcessingStrategy
    quality_score: float
    text: str = ""
    ocr_engine: Optional[str] = None
    ocr_confidence: float = 0.0
    retry_count: int = 0
    error_message: str = ""
    processed_at: Optional[str] = None


@dataclass
class DocumentResult:
    """문서 처리 결과"""
    doc_id: str
    pdf_path: str
    status: DocumentStatus
    total_pages: int
    pages: List[PageResult] = field(default_factory=list)
    tier: DocumentTier = DocumentTier.TIER3
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: str = ""
    # MANUAL_REVIEW 페이지 목록 (메타데이터로 분리 - 본문 오염 방지)
    manual_review_pages: List[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "pdf_path": self.pdf_path,
            "status": self.status.value,
            "total_pages": self.total_pages,
            "tier": self.tier.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "pages_completed": sum(1 for p in self.pages if p.status == PageStatus.COMPLETED),
            "pages_failed": sum(1 for p in self.pages if p.status == PageStatus.FAILED),
            "pages_manual_review": len(self.manual_review_pages),
            "manual_review_pages": self.manual_review_pages,
        }


class CheckpointManager:
    """체크포인트 관리 (진행 상태 저장/복구)"""

    def __init__(self, checkpoint_dir: Path):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.checkpoint_dir / "checkpoint.db"
        self._init_db()

    def _init_db(self):
        """SQLite DB 초기화"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                pdf_path TEXT,
                status TEXT,
                total_pages INTEGER,
                tier INTEGER,
                started_at TEXT,
                completed_at TEXT,
                error_message TEXT,
                result_json TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                doc_id TEXT,
                page_num INTEGER,
                status TEXT,
                strategy TEXT,
                quality_score REAL,
                ocr_engine TEXT,
                ocr_confidence REAL,
                retry_count INTEGER,
                error_message TEXT,
                processed_at TEXT,
                PRIMARY KEY (doc_id, page_num)
            )
        """)

        conn.commit()
        conn.close()

    def save_document(self, result: DocumentResult):
        """문서 상태 저장"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO documents
            (doc_id, pdf_path, status, total_pages, tier, started_at, completed_at, error_message, result_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result.doc_id,
            result.pdf_path,
            result.status.value,
            result.total_pages,
            result.tier.value,
            result.started_at,
            result.completed_at,
            result.error_message,
            json.dumps(result.to_dict()),
        ))

        conn.commit()
        conn.close()

    def save_page(self, doc_id: str, page: PageResult):
        """페이지 상태 저장"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO pages
            (doc_id, page_num, status, strategy, quality_score, ocr_engine, ocr_confidence, retry_count, error_message, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            doc_id,
            page.page_num,
            page.status.value,
            page.strategy.value,
            page.quality_score,
            page.ocr_engine,
            page.ocr_confidence,
            page.retry_count,
            page.error_message,
            page.processed_at,
        ))

        conn.commit()
        conn.close()

    def get_document_status(self, doc_id: str) -> Optional[str]:
        """문서 상태 조회"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT status FROM documents WHERE doc_id = ?", (doc_id,))
        row = cursor.fetchone()
        conn.close()

        return row[0] if row else None

    def get_pending_pages(self, doc_id: str) -> List[int]:
        """미처리 페이지 번호 조회"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT page_num FROM pages
            WHERE doc_id = ? AND status IN ('pending', 'failed')
            ORDER BY page_num
        """, (doc_id,))

        pages = [row[0] for row in cursor.fetchall()]
        conn.close()

        return pages

    def get_stats(self) -> dict:
        """전체 통계"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT status, COUNT(*) FROM documents GROUP BY status")
        doc_stats = dict(cursor.fetchall())

        cursor.execute("SELECT status, COUNT(*) FROM pages GROUP BY status")
        page_stats = dict(cursor.fetchall())

        conn.close()

        return {
            "documents": doc_stats,
            "pages": page_stats,
        }


class OCRPipeline:
    """
    OCR 파이프라인 메인 클래스 (하이브리드 v2)

    전략:
    - 텍스트 추출 시도 → 품질 검사
    - 품질 >= 임계치: 텍스트 사용 (빠름)
    - 품질 < 임계치: 이미지 → OCR (일관된 품질)

    사용법:
        pipeline = OCRPipeline(
            input_dir="/path/to/pdfs",
            output_dir="/path/to/output",
        )
        pipeline.run()
    """

    MAX_RETRIES = 3  # 최대 재처리 횟수
    QUALITY_THRESHOLD = 0.92  # 이 점수 이상이면 텍스트 사용, 미만이면 OCR

    def __init__(
        self,
        input_dir: str | Path,
        output_dir: str | Path,
        checkpoint_dir: Optional[str | Path] = None,
        use_gpu: bool = True,
        batch_size: int = 10,
    ):
        """
        Args:
            input_dir: PDF 입력 디렉토리
            output_dir: 결과 출력 디렉토리
            checkpoint_dir: 체크포인트 디렉토리 (기본: output_dir/checkpoints)
            use_gpu: GPU 사용 여부
            batch_size: 배치 크기
        """
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else self.output_dir / "checkpoints"
        self.use_gpu = use_gpu
        self.batch_size = batch_size

        # 디렉토리 생성
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "xml").mkdir(exist_ok=True)
        (self.output_dir / "text").mkdir(exist_ok=True)
        (self.output_dir / "manual_review").mkdir(exist_ok=True)
        (self.output_dir / "logs").mkdir(exist_ok=True)

        # 컴포넌트 초기화
        self.dictionary = IndonesianDictionary()
        self.quality_scorer = QualityScorer(dictionary=self.dictionary)
        self.ocr_engine = EnsembleOCR(
            use_gpu=use_gpu,
            quality_scorer=self.quality_scorer,
        )
        self.checkpoint = CheckpointManager(self.checkpoint_dir)

        logger.info(f"OCR 파이프라인 초기화 완료")
        logger.info(f"  입력: {self.input_dir}")
        logger.info(f"  출력: {self.output_dir}")
        logger.info(f"  GPU: {self.use_gpu}")

    def scan_pdfs(self) -> Iterator[Path]:
        """PDF 파일 스캔"""
        for pdf_path in sorted(self.input_dir.rglob("*.pdf")):
            yield pdf_path

    def get_document_tier(self, pdf_path: Path) -> DocumentTier:
        """문서 티어 결정"""
        name = pdf_path.stem.lower()

        if name.startswith("uu-") or "/uu/" in str(pdf_path):
            return DocumentTier.TIER1
        elif name.startswith(("pp-", "perpres-")) or any(x in str(pdf_path) for x in ["/pp/", "/perpres/"]):
            return DocumentTier.TIER2
        else:
            return DocumentTier.TIER3

    def _generate_doc_id(self, pdf_path: Path) -> str:
        """
        고유한 문서 ID 생성 (폴더 충돌 방지)

        예: uu/uu-no-1-tahun-2020.pdf → uu__uu-no-1-tahun-2020
        """
        try:
            rel_path = pdf_path.relative_to(self.input_dir)
            # 경로 구분자를 __로 대체, 확장자 제거
            doc_id = str(rel_path.with_suffix('')).replace('/', '__').replace('\\', '__')
        except ValueError:
            # input_dir 외부 파일인 경우 stem만 사용
            doc_id = pdf_path.stem
        return doc_id

    def process_document(self, pdf_path: Path) -> DocumentResult:
        """
        단일 문서 처리

        Args:
            pdf_path: PDF 파일 경로

        Returns:
            DocumentResult: 처리 결과
        """
        doc_id = self._generate_doc_id(pdf_path)
        logger.info(f"문서 처리 시작: {doc_id}")

        # 이미 완료된 문서 스킵
        existing_status = self.checkpoint.get_document_status(doc_id)
        if existing_status == DocumentStatus.COMPLETED.value:
            logger.info(f"이미 완료된 문서 스킵: {doc_id}")
            return None

        # PDF 열기
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            logger.error(f"PDF 열기 실패: {pdf_path} - {e}")
            result = DocumentResult(
                doc_id=doc_id,
                pdf_path=str(pdf_path),
                status=DocumentStatus.FAILED,
                total_pages=0,
                error_message=str(e),
            )
            self.checkpoint.save_document(result)
            return result

        # 문서 결과 초기화
        result = DocumentResult(
            doc_id=doc_id,
            pdf_path=str(pdf_path),
            status=DocumentStatus.PROCESSING,
            total_pages=len(doc),
            tier=self.get_document_tier(pdf_path),
            started_at=datetime.now().isoformat(),
        )
        self.checkpoint.save_document(result)

        # 페이지별 처리 (재시도 포함)
        # High Fix: try-finally로 doc.close() 보장
        all_text = []
        manual_review_pages = []  # MANUAL_REVIEW 페이지 추적

        try:
            for page_num in range(len(doc)):
                page_result = None
                attempt = 0

                # 최대 3회 시도
                # High Fix: _process_page 호출을 try-except로 감싸기
                while attempt < self.MAX_RETRIES:
                    attempt += 1
                    try:
                        page_result = self._process_page(doc, page_num, doc_id)
                    except Exception as e:
                        logger.error(f"페이지 처리 예외: {doc_id} p.{page_num} - {e}")
                        page_result = PageResult(
                            page_num=page_num,
                            status=PageStatus.FAILED,
                            strategy=ProcessingStrategy.OCR,
                            quality_score=0.0,
                            error_message=str(e),
                            processed_at=datetime.now().isoformat(),
                        )

                    # 성공 또는 수동 검토면 종료
                    if page_result.status in [PageStatus.COMPLETED, PageStatus.MANUAL_REVIEW]:
                        break

                    # 실패면 재시도
                    if attempt < self.MAX_RETRIES:
                        logger.warning(f"페이지 재시도 {attempt}/{self.MAX_RETRIES}: {doc_id} p.{page_num}")

                # 최종 시도 횟수 기록 (1-based)
                page_result.retry_count = attempt

                result.pages.append(page_result)
                self.checkpoint.save_page(doc_id, page_result)

                # MANUAL_REVIEW 페이지도 본문에 포함하되, 메타데이터로 플래그 유지
                # - 본문은 유지 (후처리에서 선택적 필터링 가능)
                # - 페이지 번호는 manual_review_pages로 별도 기록
                # - 필요시 PageResult에서 개별 텍스트 접근 가능
                if page_result.status == PageStatus.COMPLETED:
                    all_text.append(page_result.text)
                elif page_result.status == PageStatus.MANUAL_REVIEW:
                    manual_review_pages.append(page_num)
                    all_text.append(page_result.text)

        finally:
            # High Fix: 예외 발생해도 doc.close() 보장
            doc.close()

        # 문서 완료 상태 결정
        # Medium Fix: MANUAL_REVIEW는 실패가 아닌 별도 상태로 처리
        completed = sum(1 for p in result.pages if p.status == PageStatus.COMPLETED)
        manual_review = sum(1 for p in result.pages if p.status == PageStatus.MANUAL_REVIEW)
        failed = sum(1 for p in result.pages if p.status == PageStatus.FAILED)

        if failed == 0 and manual_review == 0:
            result.status = DocumentStatus.COMPLETED
        elif failed == 0 and manual_review > 0:
            # MANUAL_REVIEW만 있으면 PARTIAL (텍스트는 포함됨)
            result.status = DocumentStatus.PARTIAL
        elif completed > 0 or manual_review > 0:
            result.status = DocumentStatus.PARTIAL
        else:
            result.status = DocumentStatus.FAILED

        # MANUAL_REVIEW 페이지 목록 저장 (메타데이터로 분리)
        result.manual_review_pages = manual_review_pages
        result.completed_at = datetime.now().isoformat()
        self.checkpoint.save_document(result)

        # 텍스트 저장
        if all_text:
            text_path = self.output_dir / "text" / f"{doc_id}.txt"
            with open(text_path, 'w', encoding='utf-8') as f:
                f.write("\n\n--- PAGE BREAK ---\n\n".join(all_text))

        logger.info(f"문서 처리 완료: {doc_id} - {result.status.value}")
        return result

    def _process_page(
        self,
        doc: fitz.Document,
        page_num: int,
        doc_id: str,
    ) -> PageResult:
        """
        단일 페이지 처리 (하이브리드 v2)

        로직:
        1. 텍스트 추출 시도
        2. 품질 점수 계산
        3. 점수 >= QUALITY_THRESHOLD: 텍스트 사용
        4. 점수 < QUALITY_THRESHOLD: 이미지 → OCR
        """
        page = doc[page_num]

        # 1. 내장 텍스트 추출
        try:
            embedded_text = page.get_text("text")
        except Exception as e:
            embedded_text = ""
            logger.warning(f"텍스트 추출 실패: {doc_id} p.{page_num} - {e}")

        # 2. 품질 점수 계산
        quality = self.quality_scorer.score_text(embedded_text)

        # 3. 빈 페이지 처리 (텍스트 거의 없음)
        if quality.strategy == ProcessingStrategy.SKIP:
            return PageResult(
                page_num=page_num,
                status=PageStatus.COMPLETED,
                strategy=ProcessingStrategy.SKIP,
                quality_score=quality.score,
                text="",
                processed_at=datetime.now().isoformat(),
            )

        # 4. 하이브리드 v2 로직: 품질 기반 분기
        if quality.score >= self.QUALITY_THRESHOLD:
            # 고품질: 내장 텍스트 사용
            logger.debug(f"텍스트 사용: {doc_id} p.{page_num} (품질: {quality.score:.3f})")
            return PageResult(
                page_num=page_num,
                status=PageStatus.COMPLETED,
                strategy=ProcessingStrategy.TEXT,
                quality_score=quality.score,
                text=embedded_text,
                processed_at=datetime.now().isoformat(),
            )
        else:
            # 저품질: 이미지 → OCR로 대체
            logger.debug(f"OCR 전환: {doc_id} p.{page_num} (품질: {quality.score:.3f} < {self.QUALITY_THRESHOLD})")
            return self._ocr_page(doc, page_num, doc_id, quality, embedded_text)

    def _ocr_page(
        self,
        doc: fitz.Document,
        page_num: int,
        doc_id: str,
        quality: QualityResult,
        embedded_text: str,
    ) -> PageResult:
        """
        페이지 OCR 처리 (하이브리드 v2)

        품질 점수가 임계치 미만일 때 호출됨.
        이미지로 변환 후 OCR 실행, 결과를 그대로 사용.
        """
        page = doc[page_num]

        # 1. 페이지를 이미지로 변환
        try:
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")

            from PIL import Image
            import io
            img = Image.open(io.BytesIO(img_data))

        except Exception as e:
            logger.error(f"이미지 변환 실패: {doc_id} p.{page_num} - {e}")
            # 이미지 변환 실패시 내장 텍스트라도 사용 (fallback)
            return PageResult(
                page_num=page_num,
                status=PageStatus.COMPLETED,
                strategy=ProcessingStrategy.TEXT,
                quality_score=quality.score,
                text=embedded_text,
                ocr_engine="fallback_embedded",
                error_message=f"이미지 변환 실패, 내장 텍스트 사용: {e}",
                processed_at=datetime.now().isoformat(),
            )

        # 2. OCR 실행
        ocr_result = self.ocr_engine.process_image(img)
        ocr_text = (ocr_result.text or "").strip()

        # 3. OCR 결과 품질 확인
        ocr_quality = self.quality_scorer.score_text(ocr_text)

        # 4. 빈 페이지 감지 (내장 텍스트도 없고 OCR도 빈약)
        MIN_OCR_TEXT_LENGTH = 5
        if not embedded_text.strip() and len(ocr_text) < MIN_OCR_TEXT_LENGTH:
            logger.debug(f"빈 페이지 감지: {doc_id} p.{page_num}")
            return PageResult(
                page_num=page_num,
                status=PageStatus.COMPLETED,
                strategy=ProcessingStrategy.SKIP,
                quality_score=0.0,
                text="",
                processed_at=datetime.now().isoformat(),
            )

        # 5. OCR 결과 vs 내장 텍스트 비교 (안전장치)
        # OCR이 확실히 나쁘면 (거의 빈 결과) 내장 텍스트 사용
        if len(ocr_text) < MIN_OCR_TEXT_LENGTH and len(embedded_text.strip()) > 50:
            logger.warning(f"OCR 실패, 내장 텍스트 사용: {doc_id} p.{page_num}")
            return PageResult(
                page_num=page_num,
                status=PageStatus.COMPLETED,
                strategy=ProcessingStrategy.TEXT,
                quality_score=quality.score,
                text=embedded_text,
                ocr_engine="fallback_embedded",
                processed_at=datetime.now().isoformat(),
            )

        # 6. OCR 결과 사용 (기본)
        final_text = ocr_text
        engine_used = ocr_result.engine.value if ocr_result.engine else "unknown"

        # 7. OCR 품질도 낮으면 MANUAL_REVIEW
        if ocr_result.engine == OCREngine.MANUAL or ocr_quality.score < 0.5:
            manual_path = self.output_dir / "manual_review" / f"{doc_id}_p{page_num}.json"
            with open(manual_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "doc_id": doc_id,
                    "page_num": page_num,
                    "embedded_text": embedded_text[:500],
                    "embedded_quality": quality.score,
                    "ocr_text": ocr_text[:500],
                    "ocr_quality": ocr_quality.score,
                    "ocr_confidence": ocr_result.confidence,
                    "reason": "OCR 품질도 낮음" if ocr_quality.score < 0.5 else "OCR 엔진 MANUAL",
                }, f, ensure_ascii=False, indent=2)

            return PageResult(
                page_num=page_num,
                status=PageStatus.MANUAL_REVIEW,
                strategy=ProcessingStrategy.OCR,
                quality_score=ocr_quality.score,
                text=final_text,
                ocr_engine=engine_used,
                ocr_confidence=ocr_result.confidence,
                processed_at=datetime.now().isoformat(),
            )

        # 8. 정상 완료
        logger.debug(f"OCR 완료: {doc_id} p.{page_num} (품질: {ocr_quality.score:.3f})")
        return PageResult(
            page_num=page_num,
            status=PageStatus.COMPLETED,
            strategy=ProcessingStrategy.OCR,
            quality_score=ocr_quality.score,
            text=final_text,
            ocr_engine=engine_used,
            ocr_confidence=ocr_result.confidence,
            processed_at=datetime.now().isoformat(),
        )

    def run(self, limit: Optional[int] = None) -> dict:
        """
        파이프라인 실행

        Args:
            limit: 처리할 최대 문서 수 (테스트용)

        Returns:
            실행 결과 통계
        """
        logger.info("=== OCR 파이프라인 시작 ===")

        pdfs = list(self.scan_pdfs())
        if limit:
            pdfs = pdfs[:limit]

        logger.info(f"처리 대상: {len(pdfs)}개 PDF")

        results = {
            "total": len(pdfs),
            "completed": 0,
            "partial": 0,
            "failed": 0,
            "skipped": 0,
        }

        with Progress() as progress:
            task = progress.add_task("[green]Processing...", total=len(pdfs))

            for pdf_path in pdfs:
                result = self.process_document(pdf_path)

                if result is None:
                    results["skipped"] += 1
                elif result.status == DocumentStatus.COMPLETED:
                    results["completed"] += 1
                elif result.status == DocumentStatus.PARTIAL:
                    results["partial"] += 1
                else:
                    results["failed"] += 1

                progress.update(task, advance=1)

        # 최종 통계 출력
        self._print_summary(results)

        return results

    def _print_summary(self, results: dict):
        """결과 요약 출력"""
        table = Table(title="OCR 파이프라인 결과")
        table.add_column("항목", style="cyan")
        table.add_column("수량", justify="right", style="green")

        table.add_row("총 문서", str(results["total"]))
        table.add_row("완료", str(results["completed"]))
        table.add_row("부분 완료", str(results["partial"]))
        table.add_row("실패", str(results["failed"]))
        table.add_row("스킵 (기완료)", str(results["skipped"]))

        console.print(table)

        # 체크포인트 통계
        stats = self.checkpoint.get_stats()
        console.print(f"\n체크포인트 통계: {stats}")


# CLI
if __name__ == "__main__":
    import click

    @click.group()
    def cli():
        """OCR 파이프라인 CLI"""
        pass

    @cli.command()
    @click.option('--input', '-i', required=True, help='PDF 입력 디렉토리')
    @click.option('--output', '-o', required=True, help='결과 출력 디렉토리')
    @click.option('--gpu/--no-gpu', default=True, help='GPU 사용 여부')
    @click.option('--limit', '-l', default=None, type=int, help='처리할 최대 문서 수')
    def run(input, output, gpu, limit):
        """파이프라인 실행"""
        pipeline = OCRPipeline(
            input_dir=input,
            output_dir=output,
            use_gpu=gpu,
        )
        pipeline.run(limit=limit)

    @cli.command()
    @click.option('--checkpoint', '-c', required=True, help='체크포인트 디렉토리')
    def stats(checkpoint):
        """통계 조회"""
        cm = CheckpointManager(checkpoint)
        stats = cm.get_stats()
        console.print(stats)

    cli()
