"""
ILIS OCR Pipeline - Quality Controller

품질 검증 및 수동 검토 대상 분류
- 자동 품질 점수 계산
- 수동 검토 필요 문서 식별
- 품질 통계 및 리포트
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .checkpoint_manager import CheckpointManager
from .models import CheckpointStatus, PipelineV3Config, QualityMetrics, QualityTier

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"

# 인도네시아 법률 용어 목록
INDONESIAN_LEGAL_TERMS = {
    "undang-undang", "peraturan", "pemerintah", "presiden", "menteri",
    "pasal", "ayat", "huruf", "angka", "bab", "bagian", "paragraf",
    "menimbang", "mengingat", "memutuskan", "menetapkan",
    "ketentuan", "umum", "peralihan", "penutup",
    "republik", "indonesia", "negara", "hukum",
    "warga", "rakyat", "dewan", "perwakilan",
    "ditetapkan", "diundangkan", "berlaku", "tanggal",
    "jakarta", "tahun", "nomor", "tentang",
    "penjelasan", "tambahan", "lembaran",
    "perubahan", "pencabutan", "pengesahan",
}


@dataclass
class QualityAssessment:
    """품질 평가 결과"""
    document_id: str
    metrics: QualityMetrics
    issues: list[str]
    recommendations: list[str]


def calculate_word_recognition_rate(text: str) -> float:
    """
    사전 단어 인식률 계산

    실제 인도네시아어 사전이 없으므로, 라틴 알파벳과 일반적인
    단어 패턴을 기준으로 계산

    Args:
        text: 입력 텍스트

    Returns:
        인식률 (0.0-1.0)
    """
    if not text:
        return 0.0

    # 단어 추출
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return 0.0

    # 유효한 단어 패턴
    valid_count = 0
    for word in words:
        word_lower = word.lower()
        # 3자 이상이고 모음 포함
        if len(word_lower) >= 3 and re.search(r"[aeiou]", word_lower):
            valid_count += 1
        # 법률 용어인 경우
        elif word_lower in INDONESIAN_LEGAL_TERMS:
            valid_count += 1

    return valid_count / len(words)


def calculate_legal_term_rate(text: str) -> float:
    """
    법률 용어 비율 계산

    Args:
        text: 입력 텍스트

    Returns:
        법률 용어 비율 (0.0-1.0)
    """
    if not text:
        return 0.0

    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return 0.0

    legal_count = sum(1 for w in words if w.lower() in INDONESIAN_LEGAL_TERMS)
    return legal_count / len(words)


def calculate_broken_char_ratio(text: str) -> float:
    """
    깨진 문자 비율 계산

    OCR 오류로 인한 이상한 문자 패턴 감지

    Args:
        text: 입력 텍스트

    Returns:
        깨진 문자 비율 (0.0-1.0)
    """
    if not text:
        return 0.0

    # 이상 패턴
    # - 연속된 자음만 (4개 이상)
    # - 숫자-문자 혼합 (예: 2OO9)
    # - 특수 문자 과다

    total_chars = len(text)
    broken_patterns = [
        r"[bcdfghjklmnpqrstvwxyz]{4,}",  # 연속 자음
        r"\d[O0Il]{2,}\d",  # 숫자-문자 혼합 (OCR 오류)
        r"[^\w\s.,;:!?\-()\"'\[\]{}]{3,}",  # 연속 특수문자
        r"(?<![A-Za-z])[lI1]{3,}(?![A-Za-z])",  # 연속 l/I/1
    ]

    broken_count = 0
    for pattern in broken_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        broken_count += sum(len(m) for m in matches)

    return min(broken_count / total_chars, 1.0) if total_chars > 0 else 0.0


def check_structure(text: str) -> tuple[bool, bool, float]:
    """
    법률 구조 존재 확인

    Args:
        text: 입력 텍스트

    Returns:
        (has_pasal, has_ayat, structure_score)
    """
    has_pasal = bool(re.search(r"Pasal\s+\d+", text, re.IGNORECASE))
    has_ayat = bool(re.search(r"\(\d+\)", text))

    # 구조 점수 계산
    structure_elements = [
        (r"BAB\s+[IVX]+", 0.15),  # 장
        (r"Bagian\s+\w+", 0.10),  # 절
        (r"Pasal\s+\d+", 0.25),  # 조
        (r"\(\d+\)", 0.20),  # 항
        (r"[a-z]\.", 0.10),  # 호
        (r"Menimbang", 0.10),  # 고려사항
        (r"Mengingat", 0.10),  # 법적 근거
    ]

    score = 0.0
    for pattern, weight in structure_elements:
        if re.search(pattern, text, re.IGNORECASE):
            score += weight

    return has_pasal, has_ayat, min(score, 1.0)


class QualityController:
    """품질 검증기"""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        config: Optional[PipelineV3Config] = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.config = config or PipelineV3Config()
        self.checkpoint_manager = CheckpointManager(self.db_path)

    def get_connection(self):
        """데이터베이스 연결"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get_documents_to_assess(
        self,
        cluster_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        품질 평가할 문서 목록 조회

        Args:
            cluster_id: 특정 클러스터만
            limit: 최대 문서 수
        """
        conn = self.get_connection()
        try:
            query = """
                SELECT o.document_id, o.full_text, o.average_confidence,
                       o.total_pages, o.processed_pages, o.failed_pages,
                       h.cluster_id
                FROM ocr_results o
                LEFT JOIN headers h ON o.document_id = h.document_id
                WHERE o.status IN ('completed', 'partial')
                  AND o.full_text IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM quality_metrics q
                      WHERE q.document_id = o.document_id
                  )
            """
            params = []

            if cluster_id:
                query += " AND h.cluster_id = ?"
                params.append(cluster_id)

            query += " ORDER BY o.document_id"

            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_page_confidences(self, document_id: str) -> list[float]:
        """문서의 페이지별 OCR 신뢰도 조회"""
        conn = self.get_connection()
        try:
            rows = conn.execute("""
                SELECT confidence FROM ocr_pages
                WHERE document_id = ? AND status = 'processed' AND confidence IS NOT NULL
                ORDER BY page_number
            """, (document_id,)).fetchall()
            return [row["confidence"] for row in rows]
        finally:
            conn.close()

    def assess_document(self, document_id: str, text: str, avg_confidence: float) -> QualityAssessment:
        """
        문서 품질 평가

        Args:
            document_id: 문서 ID
            text: 전체 텍스트
            avg_confidence: 평균 OCR 신뢰도

        Returns:
            품질 평가 결과
        """
        issues = []
        recommendations = []

        # 페이지별 신뢰도 조회
        page_confidences = self.get_page_confidences(document_id)
        min_conf = min(page_confidences) if page_confidences else 0.0
        max_conf = max(page_confidences) if page_confidences else 0.0

        # 텍스트 품질 지표 계산
        word_recognition_rate = calculate_word_recognition_rate(text)
        legal_term_rate = calculate_legal_term_rate(text)
        broken_char_ratio = calculate_broken_char_ratio(text)

        # 구조 검사
        has_pasal, has_ayat, structure_score = check_structure(text)

        # QualityMetrics 생성
        metrics = QualityMetrics(
            document_id=document_id,
            avg_ocr_confidence=avg_confidence,
            min_ocr_confidence=min_conf,
            max_ocr_confidence=max_conf,
            word_recognition_rate=word_recognition_rate,
            legal_term_rate=legal_term_rate,
            broken_char_ratio=broken_char_ratio,
            has_pasal=has_pasal,
            has_ayat=has_ayat,
            structure_score=structure_score,
            xml_valid=False,  # XML 생성 전이므로 False
        )

        # 종합 점수 계산
        metrics.overall_score = metrics.calculate_overall_score()
        metrics.quality_tier = metrics.determine_tier()

        # 문제점 분석
        if avg_confidence < 0.50:
            issues.append("OCR 신뢰도가 매우 낮음")
            recommendations.append("원본 PDF 품질 확인 필요")

        if word_recognition_rate < 0.40:
            issues.append("단어 인식률이 낮음")
            recommendations.append("텍스트 정제 규칙 검토 필요")

        if legal_term_rate < 0.02:
            issues.append("법률 용어가 거의 없음")
            recommendations.append("문서 유형 확인 필요")

        if broken_char_ratio > 0.10:
            issues.append("깨진 문자가 많음")
            recommendations.append("OCR 품질 개선 필요")

        if not has_pasal:
            issues.append("Pasal 구조가 없음")

        if metrics.quality_tier == QualityTier.ERROR.value:
            metrics.needs_manual_review = True
            metrics.manual_review_reason = "; ".join(issues[:3])
        elif metrics.quality_tier == QualityTier.LOW.value:
            if len(issues) >= 2:
                metrics.needs_manual_review = True
                metrics.manual_review_reason = "; ".join(issues[:2])

        return QualityAssessment(
            document_id=document_id,
            metrics=metrics,
            issues=issues,
            recommendations=recommendations,
        )

    def save_metrics(self, metrics: QualityMetrics) -> None:
        """품질 지표 저장"""
        conn = self.get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO quality_metrics
                (document_id, avg_ocr_confidence, min_ocr_confidence, max_ocr_confidence,
                 word_recognition_rate, legal_term_rate, broken_char_ratio,
                 has_pasal, has_ayat, structure_score, xml_valid, xml_errors,
                 overall_score, quality_tier, needs_manual_review, manual_review_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metrics.document_id,
                metrics.avg_ocr_confidence,
                metrics.min_ocr_confidence,
                metrics.max_ocr_confidence,
                metrics.word_recognition_rate,
                metrics.legal_term_rate,
                metrics.broken_char_ratio,
                int(metrics.has_pasal),
                int(metrics.has_ayat),
                metrics.structure_score,
                int(metrics.xml_valid),
                json.dumps(metrics.xml_errors) if metrics.xml_errors else None,
                metrics.overall_score,
                metrics.quality_tier,
                int(metrics.needs_manual_review),
                metrics.manual_review_reason,
            ))
            conn.commit()
        finally:
            conn.close()

    def assess_batch(
        self,
        documents: Optional[list[dict]] = None,
        cluster_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """
        배치 품질 평가

        Args:
            documents: 평가할 문서 목록 (없으면 DB에서 조회)
            cluster_id: 특정 클러스터만
            limit: 최대 문서 수

        Returns:
            평가 통계
        """
        if documents is None:
            documents = self.get_documents_to_assess(cluster_id=cluster_id, limit=limit)

        if not documents:
            logger.info("평가할 문서가 없습니다")
            return {"total": 0}

        total = len(documents)
        tier_counts = {tier.value: 0 for tier in QualityTier}
        manual_review_count = 0

        logger.info(f"품질 평가 시작: {total}개 문서")

        # 체크포인트 생성
        checkpoint = self.checkpoint_manager.create_checkpoint(
            stage="quality",
            total_count=total,
            config=self.config.to_dict(),
        )
        batch_id = checkpoint.batch_id

        try:
            for i, doc in enumerate(documents):
                assessment = self.assess_document(
                    doc["document_id"],
                    doc["full_text"],
                    doc["average_confidence"] or 0.0,
                )

                # 저장
                self.save_metrics(assessment.metrics)

                # 통계 업데이트
                tier_counts[assessment.metrics.quality_tier] += 1
                if assessment.metrics.needs_manual_review:
                    manual_review_count += 1

                # 체크포인트 업데이트
                self.checkpoint_manager.update_progress(
                    "quality", batch_id, doc["document_id"]
                )

                # 진행 상황
                if (i + 1) % 100 == 0:
                    logger.info(f"진행: {i + 1}/{total}")

            # 완료
            self.checkpoint_manager.set_status("quality", batch_id, CheckpointStatus.COMPLETED)

        except KeyboardInterrupt:
            logger.info("사용자 중단 - 체크포인트 저장")
            self.checkpoint_manager.set_status("quality", batch_id, CheckpointStatus.PAUSED)
            raise

        except Exception as e:
            logger.error(f"배치 처리 실패: {e}")
            self.checkpoint_manager.set_status(
                "quality", batch_id, CheckpointStatus.FAILED, str(e)
            )
            raise

        stats = {
            "total": total,
            "by_tier": tier_counts,
            "manual_review_needed": manual_review_count,
            "manual_review_ratio": manual_review_count / total if total > 0 else 0,
        }

        logger.info(f"품질 평가 완료: {stats}")
        return stats

    def get_stats(self) -> dict:
        """품질 통계 조회"""
        conn = self.get_connection()
        try:
            # 등급별 통계
            rows = conn.execute("""
                SELECT quality_tier, COUNT(*) as count,
                       AVG(overall_score) as avg_score,
                       AVG(avg_ocr_confidence) as avg_confidence
                FROM quality_metrics
                GROUP BY quality_tier
            """).fetchall()

            by_tier = {}
            for row in rows:
                by_tier[row["quality_tier"]] = {
                    "count": row["count"],
                    "avg_score": row["avg_score"],
                    "avg_confidence": row["avg_confidence"],
                }

            # 수동 검토 통계
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN needs_manual_review = 1 THEN 1 ELSE 0 END) as needs_review,
                    SUM(CASE WHEN reviewed_at IS NOT NULL THEN 1 ELSE 0 END) as reviewed
                FROM quality_metrics
            """).fetchone()

            manual_review = {
                "total": row["total"],
                "needs_review": row["needs_review"],
                "reviewed": row["reviewed"],
                "pending": (row["needs_review"] or 0) - (row["reviewed"] or 0),
            }

            # 전체 통계
            row = conn.execute("""
                SELECT AVG(overall_score) as avg_score,
                       AVG(word_recognition_rate) as avg_word_rate,
                       AVG(legal_term_rate) as avg_legal_rate
                FROM quality_metrics
            """).fetchone()

            overall = {
                "avg_score": row["avg_score"],
                "avg_word_recognition_rate": row["avg_word_rate"],
                "avg_legal_term_rate": row["avg_legal_rate"],
            }

            return {
                "by_tier": by_tier,
                "manual_review": manual_review,
                "overall": overall,
            }
        finally:
            conn.close()

    def get_documents_for_review(
        self,
        tier: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """수동 검토 대상 문서 목록"""
        conn = self.get_connection()
        try:
            query = """
                SELECT q.*, d.jenis, d.nomor, d.tahun, d.tentang
                FROM quality_metrics q
                JOIN documents d ON q.document_id = d.id
                WHERE q.needs_manual_review = 1
                  AND q.reviewed_at IS NULL
            """
            params = []

            if tier:
                query += " AND q.quality_tier = ?"
                params.append(tier)

            query += " ORDER BY q.overall_score ASC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def mark_reviewed(
        self,
        document_id: str,
        notes: Optional[str] = None,
    ) -> None:
        """문서 검토 완료 표시"""
        conn = self.get_connection()
        try:
            conn.execute("""
                UPDATE quality_metrics SET
                    reviewed_at = datetime('now'),
                    reviewer_notes = ?
                WHERE document_id = ?
            """, (notes, document_id))
            conn.commit()
        finally:
            conn.close()

    def generate_report(self) -> str:
        """품질 리포트 생성"""
        stats = self.get_stats()

        lines = [
            "=" * 60,
            "OCR Pipeline V3 Quality Report",
            "=" * 60,
            "",
            "## Overall Statistics",
            f"- Average Quality Score: {stats['overall']['avg_score']:.3f}" if stats['overall']['avg_score'] else "- No data",
            f"- Average Word Recognition Rate: {stats['overall']['avg_word_recognition_rate']:.1%}" if stats['overall']['avg_word_recognition_rate'] else "",
            f"- Average Legal Term Rate: {stats['overall']['avg_legal_term_rate']:.1%}" if stats['overall']['avg_legal_term_rate'] else "",
            "",
            "## Quality Tier Distribution",
        ]

        total = sum(t.get("count", 0) for t in stats["by_tier"].values())
        for tier in ["high", "medium", "low", "error"]:
            data = stats["by_tier"].get(tier, {})
            count = data.get("count", 0)
            pct = (count / total * 100) if total > 0 else 0
            lines.append(f"- {tier.upper()}: {count} ({pct:.1f}%)")

        lines.extend([
            "",
            "## Manual Review Status",
            f"- Documents needing review: {stats['manual_review']['needs_review']}",
            f"- Already reviewed: {stats['manual_review']['reviewed']}",
            f"- Pending review: {stats['manual_review']['pending']}",
        ])

        if total > 0:
            review_rate = (stats['manual_review']['needs_review'] or 0) / total * 100
            lines.append(f"- Manual review rate: {review_rate:.1f}%")

            # 목표 대비 상태
            if review_rate <= 5:
                lines.append("✅ Target achieved (< 5% manual review)")
            else:
                lines.append(f"⚠️ Above target ({review_rate:.1f}% > 5%)")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)


# CLI
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Quality Controller")
    parser.add_argument("command", choices=["assess", "stats", "report", "review-list"], help="Command")
    parser.add_argument("--limit", "-l", type=int, help="Max documents to process")
    parser.add_argument("--cluster", "-c", type=int, help="Cluster ID filter")
    parser.add_argument("--tier", "-t", type=str, help="Quality tier filter")

    args = parser.parse_args()

    controller = QualityController()

    if args.command == "assess":
        stats = controller.assess_batch(cluster_id=args.cluster, limit=args.limit)
        print(f"\n=== 품질 평가 결과 ===")
        print(f"총 문서: {stats['total']}")
        print(f"\n등급별 분포:")
        for tier, count in stats["by_tier"].items():
            print(f"  {tier}: {count}")
        print(f"\n수동 검토 필요: {stats['manual_review_needed']} ({stats['manual_review_ratio']:.1%})")

    elif args.command == "stats":
        stats = controller.get_stats()
        print("\n=== Quality Statistics ===")
        print("\nBy tier:")
        for tier, data in stats["by_tier"].items():
            print(f"  {tier}: {data['count']} (avg score: {data['avg_score']:.3f})")
        print(f"\nManual review:")
        print(f"  Needs review: {stats['manual_review']['needs_review']}")
        print(f"  Reviewed: {stats['manual_review']['reviewed']}")
        print(f"  Pending: {stats['manual_review']['pending']}")

    elif args.command == "report":
        report = controller.generate_report()
        print(report)

    elif args.command == "review-list":
        docs = controller.get_documents_for_review(tier=args.tier, limit=args.limit or 20)
        print(f"\n=== Documents Needing Review ({len(docs)}) ===")
        for doc in docs:
            print(f"\n{doc['document_id']}")
            print(f"  {doc['jenis']} {doc['nomor']} ({doc['tahun']})")
            print(f"  Score: {doc['overall_score']:.3f}, Tier: {doc['quality_tier']}")
            if doc.get("manual_review_reason"):
                print(f"  Reason: {doc['manual_review_reason']}")
