"""
ILIS OCR Pipeline - Surya Edition (Automated)

Surya OCR 기반 자동화 파이프라인
- PDF 직접 읽기 (이미지 변환 없음)
- LAMPIRAN(부록) 섹션 자동 제외
- 본문 + 해설(PENJELASAN)만 처리
- 자동 품질 검증 (KOICA zero-defect 기준)
- 95% 이상만 자동 승인, 90-95%는 샘플 검토, 90% 미만은 수동 검토

사용법:
    # 단일 PDF 처리 (자동 품질 검증 포함)
    python -m peraturan.src.ocr.surya_pipeline process /path/to/file.pdf

    # 배치 처리
    python -m peraturan.src.ocr.surya_pipeline batch --limit 100

    # 섹션 분석
    python -m peraturan.src.ocr.surya_pipeline analyze /path/to/file.pdf

    # 상태 확인
    python -m peraturan.src.ocr.surya_pipeline status
"""

import argparse
import json
import logging
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from .quality_validator import QualityValidator, QualityGrade, QualityReport

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"
DEFAULT_PDF_DIR = Path(__file__).parent.parent.parent / "data" / "pdfs"


@dataclass
class PageInfo:
    """페이지 정보"""
    page_num: int
    section: str  # 'body', 'penjelasan', 'lampiran'
    has_text: bool
    text_preview: str = ""


@dataclass
class OCRResult:
    """OCR 결과"""
    pdf_path: str
    total_pages: int
    processed_pages: int
    skipped_pages: int  # LAMPIRAN으로 건너뛴 페이지
    lampiran_start: Optional[int]  # LAMPIRAN 시작 페이지 (없으면 None)
    text_lines: list = field(default_factory=list)
    confidence_avg: float = 0.0
    elapsed_sec: float = 0.0
    error: Optional[str] = None

    # 품질 검증 결과
    quality_grade: Optional[str] = None  # "pass", "warning", "fail"
    quality_score: float = 0.0
    auto_approved: bool = False
    needs_manual_review: bool = False


class SuryaPipeline:
    """Surya OCR 기반 파이프라인"""

    # LAMPIRAN 감지 패턴
    LAMPIRAN_PATTERNS = [
        r"^LAMPIRAN\s*$",
        r"^LAMPIRAN\s+[IVX\d]+",
        r"^LAMPIRAN\s*:",
        r"^LAMPIRAN\s+PERATURAN",
        r"^LAMPIRAN\s+UNDANG",
        r"^LAMPIRAN\s+KEPUTUSAN",
    ]

    def __init__(
        self,
        db_path: Optional[Path] = None,
        pdf_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.pdf_dir = pdf_dir or DEFAULT_PDF_DIR
        self.output_dir = output_dir or Path("/tmp/surya_output")
        self._compiled_patterns = [
            re.compile(p, re.IGNORECASE | re.MULTILINE)
            for p in self.LAMPIRAN_PATTERNS
        ]
        # 품질 검증기
        self._quality_validator = QualityValidator()

        # 통계
        self.stats = {
            "total": 0,
            "auto_approved": 0,
            "warning": 0,
            "manual_review": 0,
            "errors": 0,
        }

    def get_connection(self):
        """데이터베이스 연결"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def detect_lampiran_page(self, page: fitz.Page) -> bool:
        """
        페이지가 LAMPIRAN 섹션의 시작인지 감지

        Args:
            page: PyMuPDF 페이지 객체

        Returns:
            True if LAMPIRAN section starts on this page
        """
        # 페이지 텍스트 추출 (상단 20%만)
        rect = page.rect
        top_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.2)
        text = page.get_text("text", clip=top_rect).strip()

        # 패턴 매칭
        for pattern in self._compiled_patterns:
            if pattern.search(text):
                return True

        return False

    def analyze_pdf_sections(self, pdf_path: Path) -> list[PageInfo]:
        """
        PDF의 각 페이지 섹션 분석

        Args:
            pdf_path: PDF 파일 경로

        Returns:
            페이지별 섹션 정보 리스트
        """
        pages_info = []
        lampiran_started = False

        doc = fitz.open(pdf_path)
        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text("text").strip()

                # LAMPIRAN 감지
                if not lampiran_started and self.detect_lampiran_page(page):
                    lampiran_started = True
                    logger.info(f"LAMPIRAN detected at page {page_num + 1}")

                # 섹션 결정
                if lampiran_started:
                    section = "lampiran"
                else:
                    # PENJELASAN 감지 (본문과 해설 구분은 선택적)
                    if "PENJELASAN" in text.upper()[:500]:
                        section = "penjelasan"
                    else:
                        section = "body"

                pages_info.append(PageInfo(
                    page_num=page_num,
                    section=section,
                    has_text=len(text) > 50,
                    text_preview=text[:200] if text else "",
                ))
        finally:
            doc.close()

        return pages_info

    # PDF → 이미지 변환 설정
    DEFAULT_DPI = 200  # 200 DPI (품질/속도 균형)

    def _convert_pdf_to_images(
        self,
        pdf_path: Path,
        output_dir: Path,
        pages: Optional[list[int]] = None,
        dpi: int = DEFAULT_DPI,
    ) -> list[Path]:
        """
        PDF 페이지를 이미지로 변환

        Args:
            pdf_path: PDF 파일 경로
            output_dir: 이미지 출력 디렉토리
            pages: 변환할 페이지 번호 리스트 (None이면 전체)
            dpi: 해상도 (기본 200)

        Returns:
            생성된 이미지 파일 경로 리스트
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        image_paths = []

        doc = fitz.open(pdf_path)
        try:
            page_nums = pages if pages is not None else range(len(doc))

            for page_num in page_nums:
                if page_num >= len(doc):
                    continue

                page = doc[page_num]
                # DPI 기반 매트릭스 (72 DPI 기준)
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat)

                # PNG로 저장
                img_path = output_dir / f"page_{page_num:04d}.png"
                pix.save(str(img_path))
                image_paths.append(img_path)

                logger.debug(f"Converted page {page_num + 1} to {img_path.name}")

        finally:
            doc.close()

        return image_paths

    def process_pdf(
        self,
        pdf_path: Path,
        output_dir: Optional[Path] = None,
        skip_lampiran: bool = True,
    ) -> OCRResult:
        """
        단일 PDF 처리

        Args:
            pdf_path: PDF 파일 경로
            output_dir: 출력 디렉토리 (기본: 임시 디렉토리)
            skip_lampiran: LAMPIRAN 섹션 건너뛰기 여부

        Returns:
            OCR 결과
        """
        start_time = time.time()
        pdf_path = Path(pdf_path)

        # 섹션 분석
        pages_info = self.analyze_pdf_sections(pdf_path)
        total_pages = len(pages_info)

        # LAMPIRAN 시작 페이지 찾기
        lampiran_start = None
        if skip_lampiran:
            for info in pages_info:
                if info.section == "lampiran":
                    lampiran_start = info.page_num
                    break

        # 처리할 페이지 범위 결정
        if lampiran_start is not None:
            pages_to_process = list(range(lampiran_start))
            skipped_pages = total_pages - lampiran_start
        else:
            pages_to_process = list(range(total_pages))
            skipped_pages = 0

        if not pages_to_process:
            # 첫 페이지부터 LAMPIRAN인 경우 (드물지만)
            return OCRResult(
                pdf_path=str(pdf_path),
                total_pages=total_pages,
                processed_pages=0,
                skipped_pages=total_pages,
                lampiran_start=0,
                elapsed_sec=time.time() - start_time,
                error="PDF starts with LAMPIRAN section",
            )

        # 임시 디렉토리 생성
        work_dir = Path(tempfile.mkdtemp(prefix="surya_"))
        images_dir = work_dir / "images"
        if output_dir is None:
            output_dir = work_dir / "output"

        try:
            # 1. PDF → 이미지 변환
            logger.info(f"Converting {len(pages_to_process)} pages to images...")
            image_paths = self._convert_pdf_to_images(
                pdf_path, images_dir, pages_to_process
            )

            if not image_paths:
                return OCRResult(
                    pdf_path=str(pdf_path),
                    total_pages=total_pages,
                    processed_pages=0,
                    skipped_pages=skipped_pages,
                    lampiran_start=lampiran_start,
                    elapsed_sec=time.time() - start_time,
                    error="Failed to convert PDF to images",
                )

            # 2. Surya OCR 실행 (이미지 디렉토리)
            cmd = ["surya_ocr", str(images_dir), "--output_dir", str(output_dir)]

            logger.info(f"Running Surya OCR: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10분 타임아웃
            )

            if result.returncode != 0:
                return OCRResult(
                    pdf_path=str(pdf_path),
                    total_pages=total_pages,
                    processed_pages=0,
                    skipped_pages=skipped_pages,
                    lampiran_start=lampiran_start,
                    elapsed_sec=time.time() - start_time,
                    error=f"Surya OCR failed: {result.stderr}",
                )

            # 결과 파싱 (이미지 디렉토리 이름으로 검색)
            result_file = self._find_result_file(output_dir, "images")
            if not result_file:
                # PDF 이름으로도 시도
                result_file = self._find_result_file(output_dir, pdf_path.stem)
            if not result_file:
                return OCRResult(
                    pdf_path=str(pdf_path),
                    total_pages=total_pages,
                    processed_pages=0,
                    skipped_pages=skipped_pages,
                    lampiran_start=lampiran_start,
                    elapsed_sec=time.time() - start_time,
                    error="Result file not found",
                )

            text_lines, confidence_avg = self._parse_surya_result(result_file)
            processed_pages = len(pages_to_process)

            # 품질 검증
            text = "\n".join(line["text"] for line in text_lines)
            confidences = [line["confidence"] for line in text_lines]
            quality_report = self._quality_validator.validate(text, confidences)

            # 자동화 결정
            auto_approved = quality_report.grade == QualityGrade.PASS
            needs_manual = quality_report.grade == QualityGrade.FAIL

            # 통계 업데이트
            self.stats["total"] += 1
            if auto_approved:
                self.stats["auto_approved"] += 1
            elif quality_report.grade == QualityGrade.WARNING:
                self.stats["warning"] += 1
            else:
                self.stats["manual_review"] += 1

            return OCRResult(
                pdf_path=str(pdf_path),
                total_pages=total_pages,
                processed_pages=processed_pages,
                skipped_pages=skipped_pages,
                lampiran_start=lampiran_start,
                text_lines=text_lines,
                confidence_avg=confidence_avg,
                elapsed_sec=time.time() - start_time,
                quality_grade=quality_report.grade.value,
                quality_score=quality_report.overall_score,
                auto_approved=auto_approved,
                needs_manual_review=needs_manual,
            )

        except subprocess.TimeoutExpired:
            return OCRResult(
                pdf_path=str(pdf_path),
                total_pages=total_pages,
                processed_pages=0,
                skipped_pages=skipped_pages,
                lampiran_start=lampiran_start,
                elapsed_sec=time.time() - start_time,
                error="Surya OCR timeout (10 min)",
            )
        except Exception as e:
            return OCRResult(
                pdf_path=str(pdf_path),
                total_pages=total_pages,
                processed_pages=0,
                skipped_pages=skipped_pages,
                lampiran_start=lampiran_start,
                elapsed_sec=time.time() - start_time,
                error=str(e),
            )

    def _find_result_file(self, output_dir: Path, pdf_stem: str) -> Optional[Path]:
        """Surya 결과 파일 찾기"""
        # Surya는 output_dir/pdf_stem/results.json 형식으로 저장
        patterns = [
            output_dir / pdf_stem / "results.json",
            output_dir / f"{pdf_stem}" / "results.json",
        ]

        for pattern in patterns:
            if pattern.exists():
                return pattern

        # 디렉토리 탐색
        for subdir in output_dir.iterdir():
            if subdir.is_dir():
                result_file = subdir / "results.json"
                if result_file.exists():
                    return result_file

        return None

    def _parse_surya_result(self, result_file: Path) -> tuple[list[dict], float]:
        """
        Surya 결과 파싱

        Returns:
            (text_lines, confidence_avg)
        """
        with open(result_file) as f:
            data = json.load(f)

        all_lines = []
        all_confidences = []

        for key, pages in data.items():
            for page_result in pages:
                for line in page_result.get("text_lines", []):
                    all_lines.append({
                        "text": line["text"],
                        "confidence": line["confidence"],
                        "polygon": line.get("polygon", []),
                    })
                    all_confidences.append(line["confidence"])

        confidence_avg = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0

        return all_lines, confidence_avg

    def get_full_text(self, result: OCRResult) -> str:
        """OCR 결과에서 전체 텍스트 추출"""
        return "\n".join(line["text"] for line in result.text_lines)

    def save_result(self, result: OCRResult, output_path: Path):
        """결과 저장"""
        output_data = {
            "pdf_path": result.pdf_path,
            "total_pages": result.total_pages,
            "processed_pages": result.processed_pages,
            "skipped_pages": result.skipped_pages,
            "lampiran_start": result.lampiran_start,
            "confidence_avg": result.confidence_avg,
            "elapsed_sec": result.elapsed_sec,
            "error": result.error,
            "text_lines": result.text_lines,
            "full_text": self.get_full_text(result),
            "timestamp": datetime.now().isoformat(),
            # 품질 검증 결과
            "quality_grade": result.quality_grade,
            "quality_score": result.quality_score,
            "auto_approved": result.auto_approved,
            "needs_manual_review": result.needs_manual_review,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

    def batch_process(
        self,
        pdf_dir: Optional[Path] = None,
        limit: int = 10,
        output_dir: Optional[Path] = None,
    ) -> list[OCRResult]:
        """
        배치 처리

        Args:
            pdf_dir: PDF 디렉토리
            limit: 최대 처리 개수
            output_dir: 출력 디렉토리

        Returns:
            OCRResult 리스트
        """
        import glob

        pdf_dir = pdf_dir or self.pdf_dir
        output_dir = output_dir or self.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        # PDF 파일 찾기
        pdf_files = list(Path(pdf_dir).glob("**/*.pdf"))[:limit]
        logger.info(f"처리할 PDF: {len(pdf_files)}개")

        results = []
        for i, pdf_path in enumerate(pdf_files, 1):
            print(f"\n[{i}/{len(pdf_files)}] {pdf_path.name}")

            result = self.process_pdf(pdf_path, skip_lampiran=True)

            # 결과 저장
            if not result.error:
                output_path = output_dir / f"{pdf_path.stem}.json"
                self.save_result(result, output_path)

                # 상태 표시
                grade_emoji = {"pass": "✅", "warning": "⚠️", "fail": "❌"}
                emoji = grade_emoji.get(result.quality_grade, "?")
                print(f"  {emoji} 점수: {result.quality_score:.1f}% | "
                      f"페이지: {result.processed_pages}/{result.total_pages}")
            else:
                print(f"  ❌ 오류: {result.error}")
                self.stats["errors"] += 1

            results.append(result)

        return results

    def get_stats_summary(self) -> str:
        """통계 요약"""
        total = self.stats["total"]
        if total == 0:
            return "처리된 PDF 없음"

        auto_rate = self.stats["auto_approved"] / total * 100 if total > 0 else 0
        warning_rate = self.stats["warning"] / total * 100 if total > 0 else 0
        manual_rate = self.stats["manual_review"] / total * 100 if total > 0 else 0

        sample_review = self.stats['warning'] + self.stats['manual_review']
        sample_rate = sample_review / total * 100 if total > 0 else 0

        return f"""
{'=' * 60}
처리 통계 (KOICA zero-defect 기준)
{'=' * 60}
총 처리: {total}
  ✅ 자동 승인 (≥95%): {self.stats['auto_approved']} ({auto_rate:.1f}%)
  ⚠️ 샘플 검토 권장 (90-95%): {self.stats['warning']} ({warning_rate:.1f}%)
  ❌ 수동 검토 필요 (<90%): {self.stats['manual_review']} ({manual_rate:.1f}%)
  🔴 오류: {self.stats['errors']}

→ 인간 확인 필요: {sample_review}/{total} ({sample_rate:.1f}%)
  - 샘플 검토: {self.stats['warning']}건 (무작위 10% 확인 권장)
  - 전수 검토: {self.stats['manual_review']}건 (전체 확인 필수)
"""


def main():
    """CLI 진입점"""
    parser = argparse.ArgumentParser(
        description="ILIS OCR Pipeline - Surya Edition"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # process 명령어
    process_parser = subparsers.add_parser("process", help="Process single PDF")
    process_parser.add_argument("pdf_path", type=Path, help="PDF file path")
    process_parser.add_argument(
        "--output", "-o", type=Path, help="Output JSON file path"
    )
    process_parser.add_argument(
        "--include-lampiran", action="store_true",
        help="Include LAMPIRAN section (default: skip)"
    )

    # analyze 명령어
    analyze_parser = subparsers.add_parser("analyze", help="Analyze PDF sections")
    analyze_parser.add_argument("pdf_path", type=Path, help="PDF file path")

    # batch 명령어
    batch_parser = subparsers.add_parser("batch", help="Batch process PDFs")
    batch_parser.add_argument("--limit", type=int, default=10, help="Max PDFs to process")
    batch_parser.add_argument("--pdf-dir", type=Path, help="PDF directory")

    # auto 명령어 (자동화 파이프라인 - 재처리 포함)
    auto_parser = subparsers.add_parser(
        "auto",
        help="Automated pipeline with reprocessing (minimizes human intervention)"
    )
    auto_parser.add_argument("--limit", type=int, default=10, help="Max PDFs to process")
    auto_parser.add_argument("--pdf-dir", type=Path, help="PDF directory")

    args = parser.parse_args()

    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    pipeline = SuryaPipeline()

    if args.command == "process":
        if not args.pdf_path.exists():
            print(f"Error: File not found: {args.pdf_path}")
            sys.exit(1)

        print(f"Processing: {args.pdf_path}")
        result = pipeline.process_pdf(
            args.pdf_path,
            skip_lampiran=not args.include_lampiran,
        )

        print(f"\n{'=' * 60}")
        print(f"결과 요약")
        print(f"{'=' * 60}")
        print(f"총 페이지: {result.total_pages}")
        print(f"처리된 페이지: {result.processed_pages}")
        print(f"건너뛴 페이지 (LAMPIRAN): {result.skipped_pages}")
        if result.lampiran_start is not None:
            print(f"LAMPIRAN 시작: 페이지 {result.lampiran_start + 1}")
        print(f"평균 OCR 신뢰도: {result.confidence_avg:.3f}")
        print(f"처리 시간: {result.elapsed_sec:.2f}초")

        if result.error:
            print(f"오류: {result.error}")
        else:
            print(f"텍스트 라인 수: {len(result.text_lines)}")

            # 품질 검증 결과
            print(f"\n{'=' * 60}")
            print(f"품질 검증 결과")
            print(f"{'=' * 60}")
            grade_emoji = {"pass": "✅", "warning": "⚠️", "fail": "❌"}
            emoji = grade_emoji.get(result.quality_grade, "?")
            print(f"등급: {emoji} {result.quality_grade.upper() if result.quality_grade else 'N/A'}")
            print(f"품질 점수: {result.quality_score:.1f}%")

            if result.auto_approved:
                print(f"\n→ ✅ 자동 승인됨 (95% 이상 - 인간 개입 불필요)")
            elif result.needs_manual_review:
                print(f"\n→ ❌ 수동 검토 필요 (90% 미만)")
            else:
                print(f"\n→ ⚠️ 샘플 검토 권장 (90-95% - 일부 확인 필요)")

            # 결과 저장
            if args.output:
                pipeline.save_result(result, args.output)
                print(f"\n결과 저장: {args.output}")
            else:
                # 기본 출력 경로
                output_path = args.pdf_path.with_suffix(".surya.json")
                pipeline.save_result(result, output_path)
                print(f"\n결과 저장: {output_path}")

    elif args.command == "analyze":
        if not args.pdf_path.exists():
            print(f"Error: File not found: {args.pdf_path}")
            sys.exit(1)

        print(f"Analyzing: {args.pdf_path}")
        pages_info = pipeline.analyze_pdf_sections(args.pdf_path)

        print(f"\n{'페이지':<8} {'섹션':<12} {'텍스트':<8} 미리보기")
        print("-" * 70)
        for info in pages_info:
            preview = info.text_preview[:40].replace("\n", " ")
            print(f"{info.page_num + 1:<8} {info.section:<12} {'있음' if info.has_text else '없음':<8} {preview}...")

        # 요약
        body_pages = sum(1 for p in pages_info if p.section == "body")
        penjelasan_pages = sum(1 for p in pages_info if p.section == "penjelasan")
        lampiran_pages = sum(1 for p in pages_info if p.section == "lampiran")

        print(f"\n{'=' * 60}")
        print(f"요약")
        print(f"{'=' * 60}")
        print(f"본문: {body_pages} 페이지")
        print(f"해설: {penjelasan_pages} 페이지")
        print(f"부록: {lampiran_pages} 페이지 (제외 대상)")

    elif args.command == "batch":
        pdf_dir = args.pdf_dir or Path("/home/tylor/peraturan_pdfs")
        output_dir = Path("/tmp/surya_batch_output")

        print(f"배치 처리 시작")
        print(f"  PDF 디렉토리: {pdf_dir}")
        print(f"  출력 디렉토리: {output_dir}")
        print(f"  최대 처리: {args.limit}개")

        results = pipeline.batch_process(
            pdf_dir=pdf_dir,
            limit=args.limit,
            output_dir=output_dir,
        )

        # 통계 출력
        print(pipeline.get_stats_summary())

        # 수동 검토 필요한 파일 목록
        manual_review_files = [
            r.pdf_path for r in results
            if r.needs_manual_review
        ]
        if manual_review_files:
            print("\n수동 검토 필요한 파일:")
            for f in manual_review_files:
                print(f"  - {f}")

    elif args.command == "auto":
        # 자동화 파이프라인 (재처리 로직 포함)
        from .quality_validator import AutomatedPipeline

        pdf_dir = args.pdf_dir or Path("/home/tylor/cor/peraturan/data/pdfs")
        output_dir = Path("/tmp/ocr_auto_output")

        print(f"{'=' * 60}")
        print(f"자동화 파이프라인 시작 (인간 개입 최소화)")
        print(f"{'=' * 60}")
        print(f"  PDF 디렉토리: {pdf_dir}")
        print(f"  출력 디렉토리: {output_dir}")
        print(f"  최대 처리: {args.limit}개")
        print(f"  재처리 DPI: 300")
        print()

        auto_pipeline = AutomatedPipeline(
            surya_pipeline=pipeline,
            output_dir=output_dir,
        )

        # PDF 파일 찾기
        pdf_files = list(Path(pdf_dir).glob("**/*.pdf"))[:args.limit]
        print(f"처리할 PDF: {len(pdf_files)}개\n")

        for i, pdf_path in enumerate(pdf_files, 1):
            print(f"[{i}/{len(pdf_files)}] {pdf_path.name}")
            result = auto_pipeline.process(pdf_path)

            # 상태 표시
            if result.auto_approved:
                print(f"  ✅ 자동 승인 ({result.quality_report.overall_score:.1f}%)")
            elif result.reprocess_improved:
                print(f"  🔄 재처리로 개선 ({result.quality_report.overall_score:.1f}%)")
            elif result.needs_reprocess:
                print(f"  ⚠️ 샘플 검토 필요 ({result.quality_report.overall_score:.1f}%)")
            elif result.needs_manual_review:
                print(f"  ❌ 수동 검토 필요 ({result.quality_report.overall_score:.1f}%)")
            else:
                print(f"  ? 알 수 없음")

        # 최종 통계
        print(auto_pipeline.get_stats_summary())

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
