"""
ILIS OCR Pipeline - Text Cleaner

OCR 후 텍스트 정제
- 반복 라인 제거
- 노이즈 패턴 매칭
- 메타데이터 추출
"""

import json
import logging
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .checkpoint_manager import CheckpointManager
from .models import CheckpointStatus, PipelineV3Config, TextNoiseRule, TextNoiseRuleType

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"


@dataclass
class CleanedText:
    """정제된 텍스트"""
    original: str
    cleaned: str
    removed_lines: list[str]
    extracted_metadata: dict


@dataclass
class DocumentCleanResult:
    """문서 텍스트 정제 결과"""
    document_id: str
    success: bool
    original_length: int = 0
    cleaned_length: int = 0
    removed_lines_count: int = 0
    metadata: Optional[dict] = None
    error_message: Optional[str] = None


class TextCleaner:
    """텍스트 정제기"""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        config: Optional[PipelineV3Config] = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.config = config or PipelineV3Config()
        self.checkpoint_manager = CheckpointManager(self.db_path)

        # 규칙 캐시
        self._global_rules: list[TextNoiseRule] = []
        self._cluster_rules: dict[int, list[TextNoiseRule]] = {}
        self._rules_loaded = False

    def get_connection(self):
        """데이터베이스 연결"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def load_rules(self) -> None:
        """텍스트 노이즈 규칙 로드"""
        if self._rules_loaded:
            return

        conn = self.get_connection()
        try:
            # 글로벌 규칙
            rows = conn.execute("""
                SELECT * FROM text_noise_rules
                WHERE scope = 'global' AND is_active = 1
                ORDER BY priority
            """).fetchall()

            self._global_rules = [
                TextNoiseRule(
                    id=row["id"],
                    scope=row["scope"],
                    rule_type=row["rule_type"],
                    pattern=row["pattern"],
                    replacement=row["replacement"] or "",
                    min_frequency=row["min_frequency"],
                    position=row["position"],
                    description=row["description"],
                    priority=row["priority"],
                )
                for row in rows
            ]

            # 클러스터별 규칙
            rows = conn.execute("""
                SELECT * FROM text_noise_rules
                WHERE scope = 'cluster_specific' AND is_active = 1
                ORDER BY cluster_id, priority
            """).fetchall()

            for row in rows:
                cluster_id = row["cluster_id"]
                if cluster_id not in self._cluster_rules:
                    self._cluster_rules[cluster_id] = []

                self._cluster_rules[cluster_id].append(TextNoiseRule(
                    id=row["id"],
                    scope=row["scope"],
                    cluster_id=cluster_id,
                    rule_type=row["rule_type"],
                    pattern=row["pattern"],
                    replacement=row["replacement"] or "",
                    min_frequency=row["min_frequency"],
                    position=row["position"],
                    description=row["description"],
                    priority=row["priority"],
                ))

            self._rules_loaded = True
            logger.info(
                f"텍스트 규칙 로드: {len(self._global_rules)}개 글로벌, "
                f"{sum(len(r) for r in self._cluster_rules.values())}개 클러스터별"
            )
        finally:
            conn.close()

    def get_rules_for_cluster(self, cluster_id: Optional[int]) -> list[TextNoiseRule]:
        """클러스터에 적용할 규칙 목록"""
        self.load_rules()

        rules = list(self._global_rules)
        if cluster_id and cluster_id in self._cluster_rules:
            rules.extend(self._cluster_rules[cluster_id])

        # 우선순위로 정렬
        return sorted(rules, key=lambda r: r.priority)

    def apply_rule(self, text: str, rule: TextNoiseRule) -> tuple[str, list[str]]:
        """
        단일 규칙 적용

        Args:
            text: 입력 텍스트
            rule: 적용할 규칙

        Returns:
            (정제된 텍스트, 제거된 라인 목록)
        """
        removed = []
        lines = text.split("\n")

        if rule.rule_type == TextNoiseRuleType.REGEX.value:
            try:
                pattern = re.compile(rule.pattern, re.IGNORECASE | re.MULTILINE)
                new_lines = []
                for line in lines:
                    if pattern.fullmatch(line.strip()):
                        removed.append(line)
                    elif rule.replacement:
                        new_line = pattern.sub(rule.replacement, line)
                        new_lines.append(new_line)
                    else:
                        new_lines.append(line)
                return "\n".join(new_lines), removed
            except re.error as e:
                logger.warning(f"정규식 오류: {rule.pattern}: {e}")
                return text, []

        elif rule.rule_type == TextNoiseRuleType.EXACT.value:
            new_lines = []
            for line in lines:
                if line.strip() == rule.pattern:
                    removed.append(line)
                else:
                    new_lines.append(line)
            return "\n".join(new_lines), removed

        elif rule.rule_type == TextNoiseRuleType.CONTAINS.value:
            new_lines = []
            for line in lines:
                if rule.pattern.lower() in line.lower():
                    removed.append(line)
                else:
                    new_lines.append(line)
            return "\n".join(new_lines), removed

        elif rule.rule_type == TextNoiseRuleType.STARTSWITH.value:
            new_lines = []
            for line in lines:
                if line.strip().lower().startswith(rule.pattern.lower()):
                    removed.append(line)
                else:
                    new_lines.append(line)
            return "\n".join(new_lines), removed

        elif rule.rule_type == TextNoiseRuleType.ENDSWITH.value:
            new_lines = []
            for line in lines:
                if line.strip().lower().endswith(rule.pattern.lower()):
                    removed.append(line)
                else:
                    new_lines.append(line)
            return "\n".join(new_lines), removed

        return text, []

    def remove_frequent_lines(
        self,
        pages_text: list[str],
        min_frequency: float = 0.6,
    ) -> list[str]:
        """
        자주 등장하는 라인 제거 (문서 전체 대상)

        Args:
            pages_text: 페이지별 텍스트 목록
            min_frequency: 최소 등장 비율 (0.0-1.0)

        Returns:
            정제된 페이지별 텍스트 목록
        """
        if not pages_text:
            return pages_text

        total_pages = len(pages_text)

        # 라인별 등장 횟수 계산
        line_counts = Counter()
        for text in pages_text:
            seen = set()  # 페이지당 한 번만 카운트
            for line in text.split("\n"):
                normalized = line.strip()
                if normalized and normalized not in seen:
                    line_counts[normalized] += 1
                    seen.add(normalized)

        # 자주 등장하는 라인 식별
        frequent_lines = {
            line for line, count in line_counts.items()
            if count / total_pages >= min_frequency
        }

        if frequent_lines:
            logger.debug(f"자주 등장하는 라인 {len(frequent_lines)}개 발견")

        # 라인 제거
        cleaned_pages = []
        for text in pages_text:
            new_lines = [
                line for line in text.split("\n")
                if line.strip() not in frequent_lines
            ]
            cleaned_pages.append("\n".join(new_lines))

        return cleaned_pages

    def extract_metadata(self, text: str) -> dict:
        """
        텍스트에서 메타데이터 추출

        Args:
            text: 입력 텍스트

        Returns:
            추출된 메타데이터
        """
        metadata = {}

        # 법령 유형
        type_patterns = [
            (r"UNDANG-UNDANG", "UU"),
            (r"PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG", "PERPPU"),
            (r"PERATURAN PEMERINTAH", "PP"),
            (r"PERATURAN PRESIDEN", "PERPRES"),
            (r"PERATURAN MENTERI", "PERMEN"),
        ]

        for pattern, law_type in type_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                metadata["law_type"] = law_type
                break

        # 번호와 연도
        # 패턴: NOMOR XX TAHUN YYYY
        nomor_match = re.search(
            r"NOMOR\s+(\d+)\s+TAHUN\s+(\d{4})",
            text,
            re.IGNORECASE
        )
        if nomor_match:
            metadata["nomor"] = nomor_match.group(1)
            metadata["tahun"] = int(nomor_match.group(2))

        # 제목 (TENTANG 이후)
        tentang_match = re.search(
            r"TENTANG\s+(.+?)(?=\n\n|DENGAN RAHMAT|$)",
            text,
            re.IGNORECASE | re.DOTALL
        )
        if tentang_match:
            title = tentang_match.group(1).strip()
            # 줄바꿈 정리
            title = re.sub(r"\s+", " ", title)
            metadata["tentang"] = title[:500]  # 최대 500자

        return metadata

    def clean_document_text(
        self,
        document_id: str,
        pages_text: list[str],
        cluster_id: Optional[int] = None,
    ) -> CleanedText:
        """
        문서 텍스트 정제

        Args:
            document_id: 문서 ID
            pages_text: 페이지별 텍스트 목록
            cluster_id: 클러스터 ID

        Returns:
            정제된 텍스트
        """
        original_text = "\n\n".join(pages_text)
        removed_lines = []

        # 1. 자주 등장하는 라인 제거
        cleaned_pages = self.remove_frequent_lines(pages_text, min_frequency=0.6)

        # 2. 규칙 기반 정제
        rules = self.get_rules_for_cluster(cluster_id)

        cleaned_text = "\n\n".join(cleaned_pages)
        for rule in rules:
            if rule.rule_type == TextNoiseRuleType.LINE_FREQUENCY.value:
                continue  # 이미 처리됨

            cleaned_text, removed = self.apply_rule(cleaned_text, rule)
            removed_lines.extend(removed)

        # 3. 빈 줄 정리 (연속 빈 줄을 하나로)
        cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)
        cleaned_text = cleaned_text.strip()

        # 4. 메타데이터 추출
        metadata = self.extract_metadata(original_text)

        return CleanedText(
            original=original_text,
            cleaned=cleaned_text,
            removed_lines=removed_lines,
            extracted_metadata=metadata,
        )

    def get_documents_to_process(
        self,
        cluster_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        정제할 문서 목록 조회

        Args:
            cluster_id: 특정 클러스터만
            limit: 최대 문서 수
        """
        conn = self.get_connection()
        try:
            query = """
                SELECT DISTINCT o.document_id, h.cluster_id
                FROM ocr_results o
                LEFT JOIN headers h ON o.document_id = h.document_id
                WHERE o.status IN ('completed', 'partial')
                  AND o.full_text IS NOT NULL
                  AND (
                      NOT EXISTS (
                          SELECT 1 FROM ocr_results r2
                          WHERE r2.document_id = o.document_id
                          AND r2.extracted_metadata IS NOT NULL
                      )
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

    def get_document_pages_text(self, document_id: str) -> list[str]:
        """문서의 페이지별 OCR 텍스트 조회"""
        conn = self.get_connection()
        try:
            rows = conn.execute("""
                SELECT page_number, raw_text
                FROM ocr_pages
                WHERE document_id = ? AND status = 'processed'
                ORDER BY page_number
            """, (document_id,)).fetchall()

            return [row["raw_text"] or "" for row in rows]
        finally:
            conn.close()

    def save_cleaned_result(
        self,
        document_id: str,
        cleaned_text: str,
        metadata: dict,
    ) -> None:
        """정제 결과 저장"""
        conn = self.get_connection()
        try:
            conn.execute("""
                UPDATE ocr_results SET
                    full_text = ?,
                    extracted_metadata = ?
                WHERE document_id = ?
            """, (
                cleaned_text,
                json.dumps(metadata) if metadata else None,
                document_id,
            ))
            conn.commit()
        finally:
            conn.close()

    def process_document(
        self,
        document_id: str,
        cluster_id: Optional[int] = None,
    ) -> DocumentCleanResult:
        """
        단일 문서 정제

        Args:
            document_id: 문서 ID
            cluster_id: 클러스터 ID

        Returns:
            정제 결과
        """
        try:
            # 페이지별 텍스트 로드
            pages_text = self.get_document_pages_text(document_id)
            if not pages_text:
                return DocumentCleanResult(
                    document_id=document_id,
                    success=False,
                    error_message="No OCR text found",
                )

            # 텍스트 정제
            result = self.clean_document_text(document_id, pages_text, cluster_id)

            # 결과 저장
            self.save_cleaned_result(
                document_id,
                result.cleaned,
                result.extracted_metadata,
            )

            return DocumentCleanResult(
                document_id=document_id,
                success=True,
                original_length=len(result.original),
                cleaned_length=len(result.cleaned),
                removed_lines_count=len(result.removed_lines),
                metadata=result.extracted_metadata,
            )

        except Exception as e:
            return DocumentCleanResult(
                document_id=document_id,
                success=False,
                error_message=str(e),
            )

    def process_batch(
        self,
        documents: Optional[list[dict]] = None,
        cluster_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """
        배치 텍스트 정제

        Args:
            documents: 처리할 문서 목록 (없으면 DB에서 조회)
            cluster_id: 특정 클러스터만
            limit: 최대 문서 수

        Returns:
            처리 통계
        """
        if documents is None:
            documents = self.get_documents_to_process(cluster_id=cluster_id, limit=limit)

        if not documents:
            logger.info("처리할 문서가 없습니다")
            return {"total": 0, "processed": 0, "failed": 0}

        total = len(documents)
        processed = 0
        failed = 0

        logger.info(f"텍스트 정제 시작: {total}개 문서")

        # 규칙 로드
        self.load_rules()

        # 체크포인트 생성
        checkpoint = self.checkpoint_manager.create_checkpoint(
            stage="text_clean",
            total_count=total,
            config=self.config.to_dict(),
        )
        batch_id = checkpoint.batch_id

        try:
            for i, doc in enumerate(documents):
                result = self.process_document(doc["document_id"], doc.get("cluster_id"))

                if result.success:
                    processed += 1
                else:
                    failed += 1
                    logger.warning(f"정제 실패: {result.document_id}: {result.error_message}")

                # 체크포인트 업데이트
                self.checkpoint_manager.update_progress(
                    "text_clean", batch_id, result.document_id
                )

                # 진행 상황
                if (i + 1) % 100 == 0:
                    logger.info(f"진행: {i + 1}/{total}")

            # 완료
            self.checkpoint_manager.set_status("text_clean", batch_id, CheckpointStatus.COMPLETED)

        except KeyboardInterrupt:
            logger.info("사용자 중단 - 체크포인트 저장")
            self.checkpoint_manager.set_status("text_clean", batch_id, CheckpointStatus.PAUSED)
            raise

        except Exception as e:
            logger.error(f"배치 처리 실패: {e}")
            self.checkpoint_manager.set_status(
                "text_clean", batch_id, CheckpointStatus.FAILED, str(e)
            )
            raise

        stats = {
            "total": total,
            "processed": processed,
            "failed": failed,
        }

        logger.info(f"텍스트 정제 완료: {processed} 성공, {failed} 실패")
        return stats

    def add_rule(
        self,
        rule_type: str,
        pattern: str,
        description: str,
        replacement: str = "",
        cluster_id: Optional[int] = None,
        priority: int = 100,
    ) -> int:
        """
        새 텍스트 노이즈 규칙 추가

        Args:
            rule_type: 규칙 유형
            pattern: 패턴
            description: 설명
            replacement: 치환 문자열
            cluster_id: 클러스터 ID (없으면 글로벌)
            priority: 우선순위

        Returns:
            생성된 규칙 ID
        """
        conn = self.get_connection()
        try:
            scope = "cluster_specific" if cluster_id else "global"
            cursor = conn.execute("""
                INSERT INTO text_noise_rules
                (scope, cluster_id, rule_type, pattern, replacement, description, priority, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (scope, cluster_id, rule_type, pattern, replacement, description, priority))
            conn.commit()

            # 캐시 무효화
            self._rules_loaded = False

            return cursor.lastrowid
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """통계 조회"""
        conn = self.get_connection()
        try:
            # 규칙 통계
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN scope = 'global' THEN 1 ELSE 0 END) as global_rules,
                    SUM(CASE WHEN scope = 'cluster_specific' THEN 1 ELSE 0 END) as cluster_rules
                FROM text_noise_rules
                WHERE is_active = 1
            """).fetchone()

            rule_stats = {
                "total": row["total"],
                "global": row["global_rules"],
                "cluster_specific": row["cluster_rules"],
            }

            # 정제된 문서 수
            row = conn.execute("""
                SELECT COUNT(*) FROM ocr_results
                WHERE extracted_metadata IS NOT NULL
            """).fetchone()
            cleaned_docs = row[0]

            # 대기 중인 문서 수
            row = conn.execute("""
                SELECT COUNT(*) FROM ocr_results
                WHERE status IN ('completed', 'partial')
                  AND extracted_metadata IS NULL
            """).fetchone()
            pending_docs = row[0]

            return {
                "rules": rule_stats,
                "cleaned_documents": cleaned_docs,
                "pending_documents": pending_docs,
            }
        finally:
            conn.close()


# CLI
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Text Cleaner")
    parser.add_argument("command", choices=["clean", "stats", "add-rule"], help="Command")
    parser.add_argument("--limit", "-l", type=int, help="Max documents to process")
    parser.add_argument("--cluster", "-c", type=int, help="Cluster ID filter")
    parser.add_argument("--document", "-d", type=str, help="Single document ID")

    # add-rule 옵션
    parser.add_argument("--type", choices=["regex", "exact", "contains", "startswith", "endswith"])
    parser.add_argument("--pattern", type=str)
    parser.add_argument("--desc", type=str)
    parser.add_argument("--priority", type=int, default=100)

    args = parser.parse_args()

    cleaner = TextCleaner()

    if args.command == "clean":
        if args.document:
            result = cleaner.process_document(args.document, args.cluster)
            if result.success:
                print(f"정제 완료: {result.document_id}")
                print(f"  원본: {result.original_length} chars")
                print(f"  정제: {result.cleaned_length} chars")
                print(f"  제거된 라인: {result.removed_lines_count}")
                if result.metadata:
                    print(f"  메타데이터: {result.metadata}")
            else:
                print(f"정제 실패: {result.error_message}")
        else:
            stats = cleaner.process_batch(cluster_id=args.cluster, limit=args.limit)
            print(f"\n=== 텍스트 정제 결과 ===")
            print(f"총 문서: {stats['total']}")
            print(f"성공: {stats['processed']}")
            print(f"실패: {stats['failed']}")

    elif args.command == "stats":
        stats = cleaner.get_stats()
        print("\n=== Text Cleaner Statistics ===")
        print(f"\nRules:")
        print(f"  Total: {stats['rules']['total']}")
        print(f"  Global: {stats['rules']['global']}")
        print(f"  Cluster-specific: {stats['rules']['cluster_specific']}")
        print(f"\nDocuments:")
        print(f"  Cleaned: {stats['cleaned_documents']}")
        print(f"  Pending: {stats['pending_documents']}")

    elif args.command == "add-rule":
        if not args.type or not args.pattern:
            print("Error: --type and --pattern required")
        else:
            rule_id = cleaner.add_rule(
                rule_type=args.type,
                pattern=args.pattern,
                description=args.desc or args.pattern[:50],
                cluster_id=args.cluster,
                priority=args.priority,
            )
            print(f"규칙 추가됨: ID={rule_id}")
