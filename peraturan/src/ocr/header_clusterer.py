"""
ILIS OCR Pipeline - Header Clusterer (STAGE 0)

헤더 이미지 OCR → 텍스트 추출 → TF-IDF → KMeans 클러스터링
"""

import json
import threading
from pathlib import Path
from typing import Optional, Generator
from dataclasses import dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

import easyocr
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans
import numpy as np

from .database import OCRPipelineDB, DEFAULT_DB_PATH
from .models import DocumentStage


# 기본 설정
DEFAULT_N_CLUSTERS = 100  # 예상 패턴 수

# Thread-local storage for EasyOCR readers (thread-safe)
# 각 스레드마다 별도의 Reader 인스턴스를 유지
_thread_local = threading.local()


def get_ocr_reader():
    """
    스레드별 EasyOCR 리더 반환 (thread-safe)

    ThreadPoolExecutor에서 여러 스레드가 동시에 OCR을 실행할 때
    각 스레드가 독립적인 Reader 인스턴스를 사용하도록 보장
    """
    if not hasattr(_thread_local, 'reader'):
        _thread_local.reader = easyocr.Reader(['en', 'id'], gpu=False, verbose=False)
    return _thread_local.reader


@dataclass
class OCRResult:
    """OCR 결과"""
    document_id: str
    success: bool
    text: Optional[str] = None
    lines: Optional[list] = None
    confidence: Optional[float] = None
    error: Optional[str] = None
    duration_ms: int = 0


class HeaderOCR:
    """헤더 이미지 OCR 처리기"""

    def __init__(self):
        # Note: reader를 인스턴스에 캐싱하지 않음
        # ThreadPoolExecutor에서 사용 시 thread-local reader 사용을 위해
        # process_image()에서 매번 get_ocr_reader() 호출
        pass

    def process_image(self, image_path: str) -> tuple[bool, Optional[str], Optional[list], Optional[float]]:
        """
        헤더 이미지 OCR 처리

        Returns:
            (성공여부, 전체텍스트, 라인목록, 평균신뢰도)
        """
        try:
            # 매 호출 시 thread-local reader 획득 (thread-safe)
            reader = get_ocr_reader()
            result = reader.readtext(image_path)

            if not result:
                return False, None, None, None

            lines = []
            confidences = []

            for detection in result:
                if len(detection) >= 3:
                    text = detection[1]
                    conf = detection[2]
                    lines.append(text)
                    confidences.append(conf)

            if not lines:
                return False, None, None, None

            full_text = "\n".join(lines)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0

            return True, full_text, lines, avg_confidence

        except Exception as e:
            return False, None, None, None


class HeaderClusterer:
    """헤더 클러스터링"""

    def __init__(
        self,
        db: Optional[OCRPipelineDB] = None,
        n_clusters: int = DEFAULT_N_CLUSTERS
    ):
        self.db = db or OCRPipelineDB()
        self.n_clusters = n_clusters
        self.ocr = HeaderOCR()

    def ocr_single(self, document_id: str) -> OCRResult:
        """단일 문서 헤더 OCR"""
        start_time = time.time()

        with self.db.connection() as conn:
            # 헤더 정보 조회
            row = conn.execute(
                "SELECT image_path FROM headers WHERE document_id = ?",
                (document_id,)
            ).fetchone()

            if not row or not row["image_path"]:
                return OCRResult(
                    document_id=document_id,
                    success=False,
                    error="Header image not found"
                )

            image_path = row["image_path"]

            # OCR 실행
            success, text, lines, confidence = self.ocr.process_image(image_path)

            duration_ms = int((time.time() - start_time) * 1000)

            if success:
                # DB 업데이트
                conn.execute("""
                    UPDATE headers SET
                        raw_text = ?,
                        lines = ?,
                        ocr_confidence = ?,
                        status = 'ocr_completed'
                    WHERE document_id = ?
                """, (
                    text,
                    json.dumps(lines, ensure_ascii=False),
                    confidence,
                    document_id
                ))

                return OCRResult(
                    document_id=document_id,
                    success=True,
                    text=text,
                    lines=lines,
                    confidence=confidence,
                    duration_ms=duration_ms
                )
            else:
                return OCRResult(
                    document_id=document_id,
                    success=False,
                    error="OCR failed",
                    duration_ms=duration_ms
                )

    def ocr_batch(
        self,
        limit: Optional[int] = None,
        workers: int = 4
    ) -> Generator[OCRResult, None, None]:
        """배치 OCR 처리"""
        with self.db.connection() as conn:
            query = """
                SELECT h.document_id
                FROM headers h
                WHERE h.status = 'extracted'
                AND h.raw_text IS NULL
            """
            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query).fetchall()
            document_ids = [row["document_id"] for row in rows]

        if not document_ids:
            return

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self.ocr_single, doc_id): doc_id
                for doc_id in document_ids
            }

            for future in as_completed(futures):
                yield future.result()

    def run_clustering(self) -> dict:
        """
        TF-IDF + KMeans 클러스터링 실행

        Returns:
            클러스터링 결과 통계
        """
        with self.db.connection() as conn:
            # OCR 완료된 헤더 텍스트 조회
            rows = conn.execute("""
                SELECT h.document_id, h.raw_text, d.jenis
                FROM headers h
                JOIN documents d ON h.document_id = d.id
                WHERE h.raw_text IS NOT NULL
                AND h.raw_text != ''
            """).fetchall()

        if not rows:
            return {"error": "No OCR results found"}

        document_ids = [row["document_id"] for row in rows]
        texts = [row["raw_text"] for row in rows]
        jenis_list = [row["jenis"] for row in rows]

        print(f"[*] Clustering {len(texts)} documents...")

        # TF-IDF 벡터화
        vectorizer = TfidfVectorizer(
            max_features=1000,
            stop_words=None,
            ngram_range=(1, 2)
        )
        X = vectorizer.fit_transform(texts)

        # 클러스터 수 결정 (문서 수의 제곱근 또는 최대 100)
        n_clusters = min(self.n_clusters, int(np.sqrt(len(texts))), len(texts))
        print(f"[*] Using {n_clusters} clusters")

        # KMeans 클러스터링
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(X)

        # 결과 저장
        with self.db.connection() as conn:
            # 기존 클러스터 삭제
            conn.execute("DELETE FROM clusters")

            # 클러스터별 통계
            cluster_stats = {}
            for i, (doc_id, label, jenis) in enumerate(zip(document_ids, labels, jenis_list)):
                label = int(label)
                if label not in cluster_stats:
                    cluster_stats[label] = {
                        "count": 0,
                        "samples": [],
                        "jenis": {},
                        "texts": []
                    }
                cluster_stats[label]["count"] += 1
                if len(cluster_stats[label]["samples"]) < 5:
                    cluster_stats[label]["samples"].append(doc_id)
                cluster_stats[label]["texts"].append(texts[i][:200])
                cluster_stats[label]["jenis"][jenis] = cluster_stats[label]["jenis"].get(jenis, 0) + 1

                # 헤더에 클러스터 ID 업데이트
                conn.execute(
                    "UPDATE headers SET cluster_id = ? WHERE document_id = ?",
                    (label, doc_id)
                )

            # 클러스터 테이블에 저장
            for cluster_id, stats in cluster_stats.items():
                # 대표 텍스트 (가장 많은 jenis의 첫 번째 샘플)
                representative_text = stats["texts"][0] if stats["texts"] else ""

                # 주요 법령 유형
                main_jenis = max(stats["jenis"], key=stats["jenis"].get) if stats["jenis"] else ""

                conn.execute("""
                    INSERT INTO clusters (id, name, description, document_count, sample_documents, representative_text, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'draft')
                """, (
                    cluster_id,
                    f"cluster_{cluster_id:03d}",
                    f"Main type: {main_jenis}",
                    stats["count"],
                    json.dumps(stats["samples"]),
                    representative_text
                ))

            # 문서 stage 업데이트
            conn.execute("""
                UPDATE documents SET stage = 'clustered'
                WHERE id IN (
                    SELECT document_id FROM headers WHERE cluster_id IS NOT NULL
                )
            """)

        return {
            "total_documents": len(texts),
            "n_clusters": n_clusters,
            "cluster_sizes": {k: v["count"] for k, v in cluster_stats.items()}
        }

    def get_ocr_status(self) -> dict:
        """OCR 상태 조회"""
        with self.db.connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM headers WHERE status = 'extracted'").fetchone()[0]
            ocr_done = conn.execute("SELECT COUNT(*) FROM headers WHERE raw_text IS NOT NULL").fetchone()[0]

        return {
            "total_headers": total,
            "ocr_completed": ocr_done,
            "ocr_pending": total - ocr_done
        }

    def get_cluster_summary(self) -> list:
        """클러스터 요약 조회"""
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT id, name, description, document_count, sample_documents, status
                FROM clusters
                ORDER BY document_count DESC
            """).fetchall()

        return [dict(row) for row in rows]


def run_header_ocr(
    limit: Optional[int] = None,
    workers: int = 4,
    verbose: bool = True
) -> dict:
    """헤더 OCR 실행"""
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    from rich.console import Console

    console = Console()
    clusterer = HeaderClusterer()

    status = clusterer.get_ocr_status()
    pending = status["ocr_pending"]

    if limit:
        pending = min(pending, limit)

    if pending == 0:
        console.print("[yellow]No pending OCR tasks[/yellow]")
        return {"total": 0, "success": 0, "failed": 0}

    console.print(f"[cyan]Processing {pending} headers with {workers} workers...[/cyan]")

    stats = {"total": 0, "success": 0, "failed": 0}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("OCR processing...", total=pending)

        for result in clusterer.ocr_batch(limit=limit, workers=workers):
            stats["total"] += 1

            if result.success:
                stats["success"] += 1
                if verbose:
                    progress.console.print(
                        f"  [green]✓[/green] {result.document_id} "
                        f"(conf: {result.confidence:.2f}, {result.duration_ms}ms)"
                    )
            else:
                stats["failed"] += 1
                if verbose:
                    progress.console.print(
                        f"  [red]✗[/red] {result.document_id}: {result.error}"
                    )

            progress.update(task, advance=1)

    console.print(f"\n[green]Done![/green] Success: {stats['success']}, Failed: {stats['failed']}")

    return stats


def run_clustering(n_clusters: int = DEFAULT_N_CLUSTERS) -> dict:
    """클러스터링 실행"""
    from rich.console import Console

    console = Console()
    clusterer = HeaderClusterer(n_clusters=n_clusters)

    console.print("[cyan]Running clustering...[/cyan]")
    result = clusterer.run_clustering()

    if "error" in result:
        console.print(f"[red]Error: {result['error']}[/red]")
        return result

    console.print(f"\n[green]Clustering complete![/green]")
    console.print(f"Total documents: {result['total_documents']}")
    console.print(f"Number of clusters: {result['n_clusters']}")

    # 상위 10개 클러스터 출력
    console.print("\nTop 10 clusters by size:")
    sorted_clusters = sorted(result["cluster_sizes"].items(), key=lambda x: x[1], reverse=True)[:10]
    for cluster_id, count in sorted_clusters:
        console.print(f"  Cluster {cluster_id}: {count} documents")

    return result


# CLI 인터페이스
if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Header OCR and Clustering")
    parser.add_argument("command", choices=["ocr", "cluster", "status"],
                       help="Command to run")
    parser.add_argument("--limit", "-l", type=int, help="Maximum documents to process")
    parser.add_argument("--workers", "-w", type=int, default=4,
                       help="Number of parallel workers")
    parser.add_argument("--clusters", "-c", type=int, default=DEFAULT_N_CLUSTERS,
                       help="Number of clusters")
    parser.add_argument("--quiet", "-q", action="store_true",
                       help="Suppress verbose output")

    args = parser.parse_args()

    if args.command == "ocr":
        run_header_ocr(
            limit=args.limit,
            workers=args.workers,
            verbose=not args.quiet
        )

    elif args.command == "cluster":
        run_clustering(n_clusters=args.clusters)

    elif args.command == "status":
        clusterer = HeaderClusterer()

        ocr_status = clusterer.get_ocr_status()
        print("\n=== Header OCR Status ===")
        print(f"Total headers: {ocr_status['total_headers']}")
        print(f"OCR completed: {ocr_status['ocr_completed']}")
        print(f"OCR pending: {ocr_status['ocr_pending']}")

        clusters = clusterer.get_cluster_summary()
        if clusters:
            print(f"\n=== Clusters: {len(clusters)} ===")
            for c in clusters[:10]:
                print(f"  {c['name']}: {c['document_count']} docs - {c['description']}")
