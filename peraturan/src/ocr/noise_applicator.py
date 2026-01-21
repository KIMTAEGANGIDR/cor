"""
ILIS OCR Pipeline - Image Noise Applicator

클러스터별 이미지 노이즈 제거 규칙 적용
- 헤더/푸터 크롭
- 워터마크/로고 마스킹
- 이미지 전처리 (그레이스케일, 노이즈 제거, 이진화)
"""

import json
import logging
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .checkpoint_manager import CheckpointManager
from .models import (
    CheckpointStatus,
    ImageNoiseRule,
    PageImage,
    PageImageStatus,
    PipelineV3Config,
)

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "cleaned_images"


@dataclass
class NoiseRemovalTask:
    """노이즈 제거 작업"""
    document_id: str
    page_number: int
    input_path: str
    output_path: str
    rule: ImageNoiseRule


@dataclass
class NoiseRemovalResult:
    """노이즈 제거 결과"""
    document_id: str
    page_number: int
    success: bool
    output_path: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: int = 0


def apply_noise_removal(task: NoiseRemovalTask) -> NoiseRemovalResult:
    """
    이미지에 노이즈 제거 규칙 적용 (워커 함수)

    Args:
        task: 노이즈 제거 작업

    Returns:
        처리 결과
    """
    start_time = time.time()
    try:
        # 이미지 로드
        img = cv2.imread(task.input_path)
        if img is None:
            return NoiseRemovalResult(
                document_id=task.document_id,
                page_number=task.page_number,
                success=False,
                error_message=f"Failed to load image: {task.input_path}",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        h, w = img.shape[:2]
        rule = task.rule

        # 1. 크롭 적용
        top = int(h * rule.header_crop_ratio)
        bottom = h - int(h * rule.footer_crop_ratio)
        left = int(w * rule.left_crop_ratio)
        right = w - int(w * rule.right_crop_ratio)

        if top < bottom and left < right:
            img = img[top:bottom, left:right]

        # 2. 마스킹 영역 적용 (화이트아웃)
        if rule.mask_regions:
            mask_regions = rule.mask_regions_list
            for region in mask_regions:
                x1 = int(region.get("x1", 0))
                y1 = int(region.get("y1", 0))
                x2 = int(region.get("x2", 0))
                y2 = int(region.get("y2", 0))

                # 비율로 지정된 경우 변환
                if region.get("is_ratio", False):
                    x1 = int(w * x1 / 100)
                    y1 = int(h * y1 / 100)
                    x2 = int(w * x2 / 100)
                    y2 = int(h * y2 / 100)

                # 크롭 후 좌표 조정
                x1 = max(0, x1 - left)
                y1 = max(0, y1 - top)
                x2 = min(img.shape[1], x2 - left)
                y2 = min(img.shape[0], y2 - top)

                if x1 < x2 and y1 < y2:
                    img[y1:y2, x1:x2] = 255  # 흰색으로 마스킹

        # 3. 그레이스케일 변환
        if rule.grayscale and len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 4. 노이즈 제거
        if rule.denoise:
            if len(img.shape) == 2:
                img = cv2.fastNlMeansDenoising(img, None, 10, 7, 21)
            else:
                img = cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)

        # 5. 기울기 보정 (deskew)
        if rule.deskew:
            img = deskew_image(img)

        # 6. 이진화
        if rule.binarize:
            if len(img.shape) == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            _, img = cv2.threshold(
                img, rule.binarize_threshold, 255, cv2.THRESH_BINARY
            )

        # 출력 저장
        output_path = Path(task.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), img)

        return NoiseRemovalResult(
            document_id=task.document_id,
            page_number=task.page_number,
            success=True,
            output_path=str(output_path),
            duration_ms=int((time.time() - start_time) * 1000),
        )

    except Exception as e:
        return NoiseRemovalResult(
            document_id=task.document_id,
            page_number=task.page_number,
            success=False,
            error_message=str(e),
            duration_ms=int((time.time() - start_time) * 1000),
        )


def deskew_image(img: np.ndarray) -> np.ndarray:
    """
    이미지 기울기 보정

    Args:
        img: 입력 이미지

    Returns:
        보정된 이미지
    """
    try:
        # 그레이스케일 변환 (필요한 경우)
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img.copy()

        # 이진화
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # 좌표 추출
        coords = np.column_stack(np.where(binary > 0))
        if len(coords) < 100:
            return img

        # 최소 영역 사각형
        angle = cv2.minAreaRect(coords)[-1]

        # 각도 조정
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle

        # 회전이 너무 크면 스킵 (잘못된 감지)
        if abs(angle) > 10:
            return img

        # 회전 적용
        h, w = img.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            img, M, (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE
        )

        return rotated

    except Exception:
        return img


class NoiseApplicator:
    """이미지 노이즈 제거 적용기"""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        config: Optional[PipelineV3Config] = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.output_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
        self.config = config or PipelineV3Config()
        self.checkpoint_manager = CheckpointManager(self.db_path)

        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 규칙 캐시
        self._rules_cache: dict[int, ImageNoiseRule] = {}

    def get_connection(self):
        """데이터베이스 연결"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get_noise_rule(self, cluster_id: int) -> Optional[ImageNoiseRule]:
        """
        클러스터별 노이즈 규칙 조회 (캐시 사용)

        Args:
            cluster_id: 클러스터 ID

        Returns:
            노이즈 규칙 (없으면 None)
        """
        if cluster_id in self._rules_cache:
            return self._rules_cache[cluster_id]

        conn = self.get_connection()
        try:
            row = conn.execute("""
                SELECT * FROM image_noise_rules
                WHERE cluster_id = ? AND is_active = 1
                ORDER BY id DESC
                LIMIT 1
            """, (cluster_id,)).fetchone()

            if row:
                rule = ImageNoiseRule(
                    id=row["id"],
                    cluster_id=row["cluster_id"],
                    header_crop_ratio=row["header_crop_ratio"] or 0.0,
                    footer_crop_ratio=row["footer_crop_ratio"] or 0.0,
                    left_crop_ratio=row["left_crop_ratio"] or 0.0,
                    right_crop_ratio=row["right_crop_ratio"] or 0.0,
                    mask_regions=json.loads(row["mask_regions"]) if row["mask_regions"] else None,
                    grayscale=bool(row["grayscale"]),
                    denoise=bool(row["denoise"]),
                    deskew=bool(row["deskew"]),
                    binarize=bool(row["binarize"]),
                    binarize_threshold=row["binarize_threshold"] or 127,
                    description=row["description"],
                    notes=row["notes"],
                    is_active=bool(row["is_active"]),
                )
                self._rules_cache[cluster_id] = rule
                return rule

            return None
        finally:
            conn.close()

    def save_noise_rule(self, rule: ImageNoiseRule) -> int:
        """노이즈 규칙 저장"""
        conn = self.get_connection()
        try:
            if rule.id:
                conn.execute("""
                    UPDATE image_noise_rules SET
                        header_crop_ratio = ?,
                        footer_crop_ratio = ?,
                        left_crop_ratio = ?,
                        right_crop_ratio = ?,
                        mask_regions = ?,
                        grayscale = ?,
                        denoise = ?,
                        deskew = ?,
                        binarize = ?,
                        binarize_threshold = ?,
                        description = ?,
                        notes = ?,
                        is_active = ?,
                        updated_at = datetime('now')
                    WHERE id = ?
                """, (
                    rule.header_crop_ratio,
                    rule.footer_crop_ratio,
                    rule.left_crop_ratio,
                    rule.right_crop_ratio,
                    json.dumps(rule.mask_regions) if rule.mask_regions else None,
                    int(rule.grayscale),
                    int(rule.denoise),
                    int(rule.deskew),
                    int(rule.binarize),
                    rule.binarize_threshold,
                    rule.description,
                    rule.notes,
                    int(rule.is_active),
                    rule.id,
                ))
                conn.commit()
                return rule.id
            else:
                cursor = conn.execute("""
                    INSERT INTO image_noise_rules
                    (cluster_id, header_crop_ratio, footer_crop_ratio, left_crop_ratio, right_crop_ratio,
                     mask_regions, grayscale, denoise, deskew, binarize, binarize_threshold,
                     description, notes, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rule.cluster_id,
                    rule.header_crop_ratio,
                    rule.footer_crop_ratio,
                    rule.left_crop_ratio,
                    rule.right_crop_ratio,
                    json.dumps(rule.mask_regions) if rule.mask_regions else None,
                    int(rule.grayscale),
                    int(rule.denoise),
                    int(rule.deskew),
                    int(rule.binarize),
                    rule.binarize_threshold,
                    rule.description,
                    rule.notes,
                    int(rule.is_active),
                ))
                conn.commit()

                # 캐시 무효화
                if rule.cluster_id in self._rules_cache:
                    del self._rules_cache[rule.cluster_id]

                return cursor.lastrowid
        finally:
            conn.close()

    def get_pages_to_process(
        self,
        cluster_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        처리할 페이지 목록 조회

        Args:
            cluster_id: 특정 클러스터만
            limit: 최대 페이지 수
        """
        conn = self.get_connection()
        try:
            query = """
                SELECT p.document_id, p.page_number, p.image_path, p.cluster_id
                FROM page_images p
                WHERE p.status = 'generated'
                  AND p.image_path IS NOT NULL
                  AND p.cluster_id IS NOT NULL
            """
            params = []

            if cluster_id:
                query += " AND p.cluster_id = ?"
                params.append(cluster_id)

            # 규칙이 있는 클러스터만
            query += """
                AND EXISTS (
                    SELECT 1 FROM image_noise_rules r
                    WHERE r.cluster_id = p.cluster_id AND r.is_active = 1
                )
            """

            query += " ORDER BY p.document_id, p.page_number"

            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def update_page_status(
        self,
        document_id: str,
        page_number: int,
        cleaned_path: Optional[str],
        status: PageImageStatus,
        error_message: Optional[str] = None,
    ) -> None:
        """페이지 상태 업데이트"""
        conn = self.get_connection()
        try:
            conn.execute("""
                UPDATE page_images SET
                    cleaned_image_path = ?,
                    status = ?,
                    error_message = ?,
                    updated_at = datetime('now')
                WHERE document_id = ? AND page_number = ?
            """, (cleaned_path, status.value, error_message, document_id, page_number))
            conn.commit()
        finally:
            conn.close()

    def get_output_path(self, document_id: str, page_number: int) -> Path:
        """정제된 이미지 경로 생성"""
        subdir = document_id[:2] if len(document_id) >= 2 else "00"
        return self.output_dir / subdir / f"{document_id}_p{page_number:04d}_clean.jpg"

    def apply_to_page(
        self,
        document_id: str,
        page_number: int,
        input_path: str,
        cluster_id: int,
    ) -> NoiseRemovalResult:
        """
        단일 페이지에 노이즈 제거 적용

        Args:
            document_id: 문서 ID
            page_number: 페이지 번호
            input_path: 입력 이미지 경로
            cluster_id: 클러스터 ID

        Returns:
            처리 결과
        """
        rule = self.get_noise_rule(cluster_id)
        if not rule:
            # 규칙이 없으면 원본 복사
            output_path = self.get_output_path(document_id, page_number)
            import shutil
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(input_path, str(output_path))

            self.update_page_status(
                document_id, page_number,
                str(output_path),
                PageImageStatus.CLEANED,
            )

            return NoiseRemovalResult(
                document_id=document_id,
                page_number=page_number,
                success=True,
                output_path=str(output_path),
            )

        # 노이즈 제거 적용
        task = NoiseRemovalTask(
            document_id=document_id,
            page_number=page_number,
            input_path=input_path,
            output_path=str(self.get_output_path(document_id, page_number)),
            rule=rule,
        )

        result = apply_noise_removal(task)

        # DB 업데이트
        self.update_page_status(
            document_id, page_number,
            result.output_path if result.success else None,
            PageImageStatus.CLEANED if result.success else PageImageStatus.ERROR,
            result.error_message,
        )

        return result

    def apply_batch(
        self,
        pages: Optional[list[dict]] = None,
        cluster_id: Optional[int] = None,
        limit: Optional[int] = None,
        workers: Optional[int] = None,
    ) -> dict:
        """
        배치 노이즈 제거 적용

        Args:
            pages: 처리할 페이지 목록 (없으면 DB에서 조회)
            cluster_id: 특정 클러스터만
            limit: 최대 페이지 수
            workers: 워커 수

        Returns:
            처리 통계
        """
        if pages is None:
            pages = self.get_pages_to_process(cluster_id=cluster_id, limit=limit)

        if not pages:
            logger.info("처리할 페이지가 없습니다")
            return {"total": 0, "processed": 0, "failed": 0}

        workers = workers or self.config.workers
        total = len(pages)
        processed = 0
        failed = 0
        start_time = time.time()

        logger.info(f"배치 처리 시작: {total}개 페이지, {workers}개 워커")

        # 체크포인트 생성
        checkpoint = self.checkpoint_manager.create_checkpoint(
            stage="noise_removal",
            total_count=total,
            config=self.config.to_dict(),
        )
        batch_id = checkpoint.batch_id

        # 작업 생성
        tasks = []
        for page in pages:
            rule = self.get_noise_rule(page["cluster_id"])
            if not rule:
                continue

            tasks.append(NoiseRemovalTask(
                document_id=page["document_id"],
                page_number=page["page_number"],
                input_path=page["image_path"],
                output_path=str(self.get_output_path(page["document_id"], page["page_number"])),
                rule=rule,
            ))

        try:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                future_to_task = {
                    executor.submit(apply_noise_removal, task): task
                    for task in tasks
                }

                for future in as_completed(future_to_task):
                    task = future_to_task[future]
                    try:
                        result = future.result()

                        # DB 업데이트
                        self.update_page_status(
                            result.document_id,
                            result.page_number,
                            result.output_path if result.success else None,
                            PageImageStatus.CLEANED if result.success else PageImageStatus.ERROR,
                            result.error_message,
                        )

                        if result.success:
                            processed += 1
                        else:
                            failed += 1
                            logger.warning(
                                f"노이즈 제거 실패: {result.document_id} p{result.page_number}: {result.error_message}"
                            )

                        # 체크포인트 업데이트
                        self.checkpoint_manager.update_progress(
                            "noise_removal",
                            batch_id,
                            result.document_id,
                            result.page_number,
                        )

                        if (processed + failed) % 100 == 0:
                            elapsed = time.time() - start_time
                            speed = (processed + failed) / elapsed
                            logger.info(f"진행: {processed + failed}/{total} ({speed:.1f} pages/sec)")

                    except Exception as e:
                        failed += 1
                        logger.error(f"작업 실패: {task.document_id} p{task.page_number}: {e}")

            # 완료
            self.checkpoint_manager.set_status(
                "noise_removal", batch_id, CheckpointStatus.COMPLETED
            )

        except KeyboardInterrupt:
            logger.info("사용자 중단 - 체크포인트 저장")
            self.checkpoint_manager.set_status(
                "noise_removal", batch_id, CheckpointStatus.PAUSED
            )
            raise

        except Exception as e:
            logger.error(f"배치 처리 실패: {e}")
            self.checkpoint_manager.set_status(
                "noise_removal", batch_id, CheckpointStatus.FAILED, str(e)
            )
            raise

        elapsed = time.time() - start_time
        stats = {
            "total": total,
            "processed": processed,
            "failed": failed,
            "elapsed_sec": elapsed,
            "pages_per_sec": (processed + failed) / elapsed if elapsed > 0 else 0,
        }

        logger.info(f"배치 처리 완료: {processed} 성공, {failed} 실패, {elapsed:.1f}초")
        return stats

    def get_stats(self) -> dict:
        """통계 조회"""
        conn = self.get_connection()
        try:
            # 클러스터별 규칙 수
            rows = conn.execute("""
                SELECT cluster_id, COUNT(*) as count
                FROM image_noise_rules
                WHERE is_active = 1
                GROUP BY cluster_id
            """).fetchall()
            rules_by_cluster = {row["cluster_id"]: row["count"] for row in rows}

            # 처리된 페이지 수
            row = conn.execute("""
                SELECT COUNT(*) FROM page_images WHERE status = 'cleaned'
            """).fetchone()
            cleaned_pages = row[0]

            # 대기 중인 페이지 수
            row = conn.execute("""
                SELECT COUNT(*) FROM page_images
                WHERE status = 'generated' AND cluster_id IS NOT NULL
            """).fetchone()
            pending_pages = row[0]

            return {
                "rules_by_cluster": rules_by_cluster,
                "total_rules": sum(rules_by_cluster.values()),
                "cleaned_pages": cleaned_pages,
                "pending_pages": pending_pages,
            }
        finally:
            conn.close()

    def list_rules(self) -> list[ImageNoiseRule]:
        """모든 활성 규칙 목록"""
        conn = self.get_connection()
        try:
            rows = conn.execute("""
                SELECT r.*, c.description as cluster_desc, c.document_count
                FROM image_noise_rules r
                JOIN clusters c ON r.cluster_id = c.id
                WHERE r.is_active = 1
                ORDER BY c.document_count DESC
            """).fetchall()

            rules = []
            for row in rows:
                rules.append(ImageNoiseRule(
                    id=row["id"],
                    cluster_id=row["cluster_id"],
                    header_crop_ratio=row["header_crop_ratio"] or 0.0,
                    footer_crop_ratio=row["footer_crop_ratio"] or 0.0,
                    left_crop_ratio=row["left_crop_ratio"] or 0.0,
                    right_crop_ratio=row["right_crop_ratio"] or 0.0,
                    mask_regions=json.loads(row["mask_regions"]) if row["mask_regions"] else None,
                    grayscale=bool(row["grayscale"]),
                    denoise=bool(row["denoise"]),
                    deskew=bool(row["deskew"]),
                    binarize=bool(row["binarize"]),
                    binarize_threshold=row["binarize_threshold"] or 127,
                    description=row.get("cluster_desc"),
                    is_active=True,
                ))
            return rules
        finally:
            conn.close()


# CLI
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Image Noise Applicator")
    parser.add_argument("command", choices=["apply", "stats", "rules"], help="Command")
    parser.add_argument("--limit", "-l", type=int, help="Max pages to process")
    parser.add_argument("--cluster", "-c", type=int, help="Cluster ID filter")
    parser.add_argument("--workers", "-w", type=int, default=8, help="Number of workers")

    args = parser.parse_args()

    applicator = NoiseApplicator()

    if args.command == "apply":
        stats = applicator.apply_batch(
            cluster_id=args.cluster,
            limit=args.limit,
            workers=args.workers,
        )
        print(f"\n=== 처리 결과 ===")
        print(f"총 페이지: {stats['total']}")
        print(f"성공: {stats['processed']}")
        print(f"실패: {stats['failed']}")
        print(f"처리 시간: {stats['elapsed_sec']:.1f}초")
        print(f"속도: {stats['pages_per_sec']:.1f} pages/sec")

    elif args.command == "stats":
        stats = applicator.get_stats()
        print("\n=== Noise Applicator Statistics ===")
        print(f"Total rules: {stats['total_rules']}")
        print(f"Cleaned pages: {stats['cleaned_pages']}")
        print(f"Pending pages: {stats['pending_pages']}")
        print("\nRules by cluster:")
        for cid, count in stats["rules_by_cluster"].items():
            print(f"  Cluster {cid}: {count} rule(s)")

    elif args.command == "rules":
        rules = applicator.list_rules()
        print(f"\n=== Active Noise Rules ({len(rules)}) ===")
        for rule in rules:
            print(f"\nCluster {rule.cluster_id}: {rule.description}")
            print(f"  Header crop: {rule.header_crop_ratio:.1%}")
            print(f"  Footer crop: {rule.footer_crop_ratio:.1%}")
            if rule.mask_regions:
                print(f"  Mask regions: {len(rule.mask_regions)}")
            options = []
            if rule.grayscale:
                options.append("grayscale")
            if rule.denoise:
                options.append("denoise")
            if rule.deskew:
                options.append("deskew")
            if rule.binarize:
                options.append(f"binarize({rule.binarize_threshold})")
            if options:
                print(f"  Options: {', '.join(options)}")
