"""
ILIS OCR Pipeline - Header Visual Clusterer

이미지 기반 헤더 클러스터링 (OCR 없음, 빠른 속도)
- 이미지 해시 (pHash) 기반 유사도 계산
- 히스토그램 기반 색상 분포 분석
- KMeans 클러스터링

사용법:
    python -m peraturan.src.ocr.header_visual_clusterer cluster --limit 1000
    python -m peraturan.src.ocr.header_visual_clusterer status
"""

import json
import hashlib
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

import numpy as np
from PIL import Image
from sklearn.cluster import KMeans, MiniBatchKMeans
from sklearn.preprocessing import StandardScaler

from .database import OCRPipelineDB

# 기본 설정
DEFAULT_N_CLUSTERS = 50  # 시각적 패턴 수 (텍스트보다 적을 것으로 예상)
DEFAULT_HEADERS_DIR = Path(__file__).parent.parent.parent / "data" / "headers"


@dataclass
class ImageFeatures:
    """이미지 특징"""
    document_id: str
    image_path: str
    phash: str  # 지각적 해시
    histogram: list  # 색상 히스토그램
    avg_brightness: float
    aspect_ratio: float
    success: bool
    error: Optional[str] = None


def compute_phash(image: Image.Image, hash_size: int = 8) -> str:
    """
    지각적 해시 (pHash) 계산

    DCT 기반 해시로 이미지의 시각적 특징을 64비트 해시로 압축
    """
    # 그레이스케일 + 리사이즈
    img = image.convert("L").resize((hash_size * 4, hash_size * 4), Image.Resampling.LANCZOS)
    pixels = np.array(img, dtype=np.float64)

    # DCT (간단한 구현)
    dct = np.zeros((hash_size, hash_size))
    for i in range(hash_size):
        for j in range(hash_size):
            sum_val = 0
            for x in range(hash_size * 4):
                for y in range(hash_size * 4):
                    sum_val += pixels[x, y] * \
                        np.cos(np.pi * i * (2 * x + 1) / (2 * hash_size * 4)) * \
                        np.cos(np.pi * j * (2 * y + 1) / (2 * hash_size * 4))
                dct[i, j] = sum_val

    # 저주파 성분만 사용 (왼쪽 상단 8x8)
    dct_low = dct[:hash_size, :hash_size]

    # 중앙값 기준 이진화
    median = np.median(dct_low)
    diff = dct_low > median

    # 해시 문자열로 변환
    return ''.join(['1' if b else '0' for b in diff.flatten()])


def compute_dhash(image: Image.Image, hash_size: int = 8) -> str:
    """
    차이 해시 (dHash) 계산 - pHash보다 빠름

    인접 픽셀 차이 기반
    """
    # 그레이스케일 + 리사이즈
    img = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = np.array(img)

    # 수평 차이
    diff = pixels[:, 1:] > pixels[:, :-1]

    return ''.join(['1' if b else '0' for b in diff.flatten()])


def compute_histogram(image: Image.Image, bins: int = 16) -> list:
    """
    색상 히스토그램 계산

    RGB 각 채널별 히스토그램을 연결
    """
    img = image.convert("RGB")

    # 리사이즈 (속도 향상)
    img = img.resize((64, 64), Image.Resampling.LANCZOS)
    pixels = np.array(img)

    histograms = []
    for channel in range(3):
        hist, _ = np.histogram(pixels[:, :, channel], bins=bins, range=(0, 256))
        hist = hist / hist.sum()  # 정규화
        histograms.extend(hist)

    return histograms


def extract_features(image_path: str, document_id: str) -> ImageFeatures:
    """
    단일 이미지에서 특징 추출
    """
    try:
        img = Image.open(image_path)

        # 기본 정보
        width, height = img.size
        aspect_ratio = width / height

        # 평균 밝기
        gray = img.convert("L")
        avg_brightness = np.array(gray).mean() / 255.0

        # 해시 (dHash - 빠름)
        phash = compute_dhash(img)

        # 히스토그램
        histogram = compute_histogram(img)

        return ImageFeatures(
            document_id=document_id,
            image_path=image_path,
            phash=phash,
            histogram=histogram,
            avg_brightness=avg_brightness,
            aspect_ratio=aspect_ratio,
            success=True
        )

    except Exception as e:
        return ImageFeatures(
            document_id=document_id,
            image_path=image_path,
            phash="",
            histogram=[],
            avg_brightness=0,
            aspect_ratio=0,
            success=False,
            error=str(e)
        )


def hamming_distance(hash1: str, hash2: str) -> int:
    """해밍 거리 계산"""
    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))


class HeaderVisualClusterer:
    """시각적 헤더 클러스터링"""

    def __init__(
        self,
        db: Optional[OCRPipelineDB] = None,
        n_clusters: int = DEFAULT_N_CLUSTERS,
        headers_dir: Optional[Path] = None
    ):
        self.db = db or OCRPipelineDB()
        self.n_clusters = n_clusters
        self.headers_dir = headers_dir or DEFAULT_HEADERS_DIR

    def extract_all_features(
        self,
        limit: Optional[int] = None,
        workers: int = 8
    ) -> list[ImageFeatures]:
        """
        모든 헤더 이미지에서 특징 추출 (병렬 처리)
        """
        # 헤더 이미지 목록 조회
        with self.db.connection() as conn:
            query = """
                SELECT document_id, image_path FROM headers
                WHERE status = 'extracted'
            """
            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query).fetchall()

        if not rows:
            return []

        print(f"[*] Extracting features from {len(rows)} images ({workers} workers)...")

        features = []
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(extract_features, row["image_path"], row["document_id"]): row
                for row in rows
            }

            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                features.append(result)

                if i % 1000 == 0:
                    elapsed = time.time() - start_time
                    rate = i / elapsed
                    print(f"  [{i}/{len(rows)}] {rate:.0f} img/sec")

        elapsed = time.time() - start_time
        success_count = sum(1 for f in features if f.success)
        print(f"[*] Done! {success_count}/{len(features)} in {elapsed:.1f}s ({len(features)/elapsed:.0f} img/sec)")

        return features

    def run_clustering(
        self,
        features: list[ImageFeatures],
        use_histogram: bool = True,
        use_hash: bool = True
    ) -> dict:
        """
        클러스터링 실행

        Args:
            features: 이미지 특징 리스트
            use_histogram: 히스토그램 특징 사용
            use_hash: 해시 특징 사용
        """
        # 성공한 것만 필터링
        valid_features = [f for f in features if f.success]

        if not valid_features:
            return {"error": "No valid features"}

        print(f"[*] Clustering {len(valid_features)} images...")

        # 특징 벡터 구성
        vectors = []
        for f in valid_features:
            vec = []

            # 히스토그램 (48차원: 16 bins x 3 channels)
            if use_histogram and f.histogram:
                vec.extend(f.histogram)

            # 해시를 숫자 벡터로 (64차원)
            if use_hash and f.phash:
                vec.extend([int(b) for b in f.phash])

            # 기타 특징
            vec.append(f.avg_brightness)
            vec.append(f.aspect_ratio)

            vectors.append(vec)

        X = np.array(vectors)

        # 정규화
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # 클러스터 수 결정
        n_clusters = min(self.n_clusters, len(valid_features))
        print(f"[*] Using {n_clusters} clusters")

        # MiniBatchKMeans (대규모 데이터에 빠름)
        if len(valid_features) > 5000:
            kmeans = MiniBatchKMeans(
                n_clusters=n_clusters,
                random_state=42,
                batch_size=1000,
                n_init=3
            )
        else:
            kmeans = KMeans(
                n_clusters=n_clusters,
                random_state=42,
                n_init=10
            )

        labels = kmeans.fit_predict(X_scaled)

        # 결과 저장
        document_ids = [f.document_id for f in valid_features]

        with self.db.connection() as conn:
            # 기존 클러스터 삭제
            conn.execute("DELETE FROM clusters")

            # 클러스터별 통계
            cluster_stats = {}
            for doc_id, label, feat in zip(document_ids, labels, valid_features):
                label = int(label)
                if label not in cluster_stats:
                    cluster_stats[label] = {
                        "count": 0,
                        "samples": [],
                        "brightness_sum": 0,
                    }
                cluster_stats[label]["count"] += 1
                cluster_stats[label]["brightness_sum"] += feat.avg_brightness
                if len(cluster_stats[label]["samples"]) < 5:
                    cluster_stats[label]["samples"].append(doc_id)

                # 헤더에 클러스터 ID 업데이트
                conn.execute(
                    "UPDATE headers SET cluster_id = ? WHERE document_id = ?",
                    (label, doc_id)
                )

            # 클러스터 테이블에 저장
            for cluster_id, stats in cluster_stats.items():
                avg_brightness = stats["brightness_sum"] / stats["count"]
                brightness_desc = "밝음" if avg_brightness > 0.7 else "보통" if avg_brightness > 0.4 else "어두움"

                conn.execute("""
                    INSERT INTO clusters (id, name, description, document_count, sample_documents, status)
                    VALUES (?, ?, ?, ?, ?, 'draft')
                """, (
                    cluster_id,
                    f"visual_{cluster_id:03d}",
                    f"시각적 패턴 그룹 ({brightness_desc}, {stats['count']}개)",
                    stats["count"],
                    json.dumps(stats["samples"]),
                ))

            # 문서 stage 업데이트
            conn.execute("""
                UPDATE documents SET stage = 'clustered'
                WHERE id IN (
                    SELECT document_id FROM headers WHERE cluster_id IS NOT NULL
                )
            """)

        return {
            "total_documents": len(valid_features),
            "n_clusters": n_clusters,
            "cluster_sizes": {k: v["count"] for k, v in cluster_stats.items()},
            "inertia": float(kmeans.inertia_)
        }

    def get_status(self) -> dict:
        """상태 조회"""
        with self.db.connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM headers WHERE status = 'extracted'").fetchone()[0]
            clustered = conn.execute("SELECT COUNT(*) FROM headers WHERE cluster_id IS NOT NULL").fetchone()[0]
            n_clusters = conn.execute("SELECT COUNT(DISTINCT cluster_id) FROM headers WHERE cluster_id IS NOT NULL").fetchone()[0]

        return {
            "total_headers": total,
            "clustered": clustered,
            "unclustered": total - clustered,
            "n_clusters": n_clusters
        }

    def get_cluster_summary(self) -> list:
        """클러스터 요약"""
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT id, name, description, document_count, sample_documents
                FROM clusters
                ORDER BY document_count DESC
            """).fetchall()

        return [dict(row) for row in rows]

    def analyze_cluster_correlations(self) -> dict:
        """
        클러스터와 메타데이터 간의 상관관계 분석

        분석 항목:
        - 연대별 분포 (tahun)
        - 법령 유형별 분포 (jenis)
        - 클러스터 순수도 (purity) - 얼마나 동질적인가

        Returns:
            상세 분석 결과
        """
        from collections import Counter

        with self.db.connection() as conn:
            # 클러스터별 문서 메타데이터 조회
            rows = conn.execute("""
                SELECT
                    h.cluster_id,
                    d.jenis,
                    d.tahun,
                    d.id as document_id
                FROM headers h
                JOIN documents d ON h.document_id = d.id
                WHERE h.cluster_id IS NOT NULL
            """).fetchall()

        if not rows:
            return {"error": "No clustered documents"}

        # 클러스터별 분석
        cluster_data = {}
        for row in rows:
            cid = row["cluster_id"]
            if cid not in cluster_data:
                cluster_data[cid] = {
                    "jenis": [],
                    "tahun": [],
                    "documents": []
                }
            cluster_data[cid]["jenis"].append(row["jenis"])
            cluster_data[cid]["tahun"].append(row["tahun"])
            cluster_data[cid]["documents"].append(row["document_id"])

        # 분석 결과
        analysis = {
            "total_clusters": len(cluster_data),
            "total_documents": len(rows),
            "clusters": [],
            "global_stats": {
                "jenis_distribution": Counter(r["jenis"] for r in rows),
                "tahun_distribution": Counter(r["tahun"] for r in rows),
            },
            "correlation_summary": {}
        }

        # 클러스터별 상세 분석
        high_purity_jenis = 0  # jenis 순수도 높은 클러스터
        high_purity_tahun = 0  # tahun 순수도 높은 클러스터

        for cid, data in sorted(cluster_data.items()):
            jenis_counter = Counter(data["jenis"])
            tahun_counter = Counter(data["tahun"])
            total = len(data["documents"])

            # 주요 jenis와 tahun
            top_jenis = jenis_counter.most_common(1)[0] if jenis_counter else (None, 0)
            top_tahun = tahun_counter.most_common(1)[0] if tahun_counter else (None, 0)

            # 순수도 계산 (가장 많은 항목의 비율)
            jenis_purity = top_jenis[1] / total if total > 0 else 0
            tahun_purity = top_tahun[1] / total if total > 0 else 0

            # 연대 범위 계산
            years = [t for t in data["tahun"] if t]
            year_range = (min(years), max(years)) if years else (None, None)
            year_span = year_range[1] - year_range[0] if all(year_range) else 0

            cluster_info = {
                "cluster_id": cid,
                "document_count": total,
                "jenis": {
                    "distribution": dict(jenis_counter.most_common(5)),
                    "top": top_jenis[0],
                    "top_count": top_jenis[1],
                    "purity": round(jenis_purity, 3),
                    "unique_count": len(jenis_counter)
                },
                "tahun": {
                    "distribution": dict(tahun_counter.most_common(10)),
                    "top": top_tahun[0],
                    "top_count": top_tahun[1],
                    "purity": round(tahun_purity, 3),
                    "range": year_range,
                    "span": year_span,
                    "unique_count": len(tahun_counter)
                },
                "sample_documents": data["documents"][:5]
            }

            analysis["clusters"].append(cluster_info)

            # 높은 순수도 카운트
            if jenis_purity >= 0.8:
                high_purity_jenis += 1
            if tahun_purity >= 0.5 or year_span <= 5:
                high_purity_tahun += 1

        # 상관관계 요약
        analysis["correlation_summary"] = {
            "jenis_correlation": {
                "high_purity_clusters": high_purity_jenis,
                "percentage": round(high_purity_jenis / len(cluster_data) * 100, 1),
                "interpretation": "높음" if high_purity_jenis / len(cluster_data) > 0.5 else "중간" if high_purity_jenis / len(cluster_data) > 0.3 else "낮음"
            },
            "tahun_correlation": {
                "high_purity_clusters": high_purity_tahun,
                "percentage": round(high_purity_tahun / len(cluster_data) * 100, 1),
                "interpretation": "높음" if high_purity_tahun / len(cluster_data) > 0.5 else "중간" if high_purity_tahun / len(cluster_data) > 0.3 else "낮음"
            }
        }

        return analysis

    def generate_analysis_report(self, output_path: Path) -> str:
        """
        상세 분석 보고서 생성 (Markdown)
        """
        analysis = self.analyze_cluster_correlations()

        if "error" in analysis:
            return ""

        lines = [
            "# 헤더 클러스터 분석 보고서",
            "",
            f"생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 1. 개요",
            "",
            f"- **총 클러스터 수**: {analysis['total_clusters']}",
            f"- **총 문서 수**: {analysis['total_documents']}",
            f"- **클러스터당 평균 문서**: {analysis['total_documents'] / analysis['total_clusters']:.1f}",
            "",
            "## 2. 상관관계 요약",
            "",
            "### 2.1 법령 유형(Jenis)과의 상관관계",
            "",
            f"- **높은 순수도(≥80%) 클러스터**: {analysis['correlation_summary']['jenis_correlation']['high_purity_clusters']}개 ({analysis['correlation_summary']['jenis_correlation']['percentage']}%)",
            f"- **해석**: {analysis['correlation_summary']['jenis_correlation']['interpretation']}",
            "",
            "**의미**: 순수도가 높다면, 시각적 패턴이 법령 유형과 강하게 연관됨을 의미합니다.",
            "예를 들어, UU(법률)와 PP(정부규정)가 다른 헤더 디자인을 사용한다면 자연스럽게 다른 클러스터로 분리됩니다.",
            "",
            "### 2.2 연도(Tahun)와의 상관관계",
            "",
            f"- **높은 순수도(≥50% 또는 5년 이내) 클러스터**: {analysis['correlation_summary']['tahun_correlation']['high_purity_clusters']}개 ({analysis['correlation_summary']['tahun_correlation']['percentage']}%)",
            f"- **해석**: {analysis['correlation_summary']['tahun_correlation']['interpretation']}",
            "",
            "**의미**: 연도와 상관관계가 높다면, 시기별로 문서 포맷이 변경되었음을 의미합니다.",
            "정부 기관의 CI 변경, 인쇄 기술 변화, 또는 스캔 시기에 따른 품질 차이일 수 있습니다.",
            "",
            "## 3. 전체 분포",
            "",
            "### 3.1 법령 유형별 분포",
            "",
        ]

        for jenis, count in sorted(analysis["global_stats"]["jenis_distribution"].items(), key=lambda x: -x[1])[:10]:
            pct = count / analysis["total_documents"] * 100
            lines.append(f"- **{jenis}**: {count}개 ({pct:.1f}%)")

        lines.extend([
            "",
            "### 3.2 연도별 분포 (상위 10개)",
            "",
        ])

        for tahun, count in sorted(analysis["global_stats"]["tahun_distribution"].items(), key=lambda x: -x[1])[:10]:
            pct = count / analysis["total_documents"] * 100
            lines.append(f"- **{tahun}**: {count}개 ({pct:.1f}%)")

        lines.extend([
            "",
            "## 4. 클러스터별 상세 분석",
            "",
        ])

        # 상위 20개 클러스터만 상세 분석
        for cluster in sorted(analysis["clusters"], key=lambda x: -x["document_count"])[:20]:
            cid = cluster["cluster_id"]
            count = cluster["document_count"]

            lines.extend([
                f"### Cluster {cid} ({count}개 문서)",
                "",
                f"**주요 법령 유형**: {cluster['jenis']['top']} ({cluster['jenis']['purity']*100:.0f}% 순수도)",
                "",
                "법령 유형 분포:",
            ])

            for jenis, cnt in cluster["jenis"]["distribution"].items():
                lines.append(f"  - {jenis}: {cnt}개")

            lines.extend([
                "",
                f"**연도 범위**: {cluster['tahun']['range'][0]} ~ {cluster['tahun']['range'][1]} ({cluster['tahun']['span']}년 스팬)",
                "",
                f"**샘플 문서**: {', '.join(cluster['sample_documents'][:3])}",
                "",
                "---",
                "",
            ])

        # 인사이트 섹션
        lines.extend([
            "## 5. 인사이트 및 권장사항",
            "",
            "### 5.1 클러스터링 기준 해석",
            "",
            "현재 클러스터링은 다음 시각적 특징을 기반으로 합니다:",
            "- **dHash (차이 해시)**: 이미지의 전체적인 구조/윤곽",
            "- **색상 히스토그램**: RGB 색상 분포",
            "- **평균 밝기**: 문서의 전체 밝기 (스캔 품질 관련)",
            "- **종횡비**: 헤더 영역의 비율",
            "",
            "### 5.2 LLM OCR 전략 권장사항",
            "",
        ])

        # 상관관계에 따른 권장사항
        jenis_corr = analysis['correlation_summary']['jenis_correlation']['interpretation']
        tahun_corr = analysis['correlation_summary']['tahun_correlation']['interpretation']

        if jenis_corr == "높음":
            lines.append("- **법령 유형별 프롬프트 최적화**: 각 jenis에 맞는 OCR 프롬프트를 별도로 설계하세요.")
        if tahun_corr == "높음":
            lines.append("- **시기별 처리 전략**: 오래된 문서(저품질 스캔)와 최신 문서를 다르게 처리하세요.")

        lines.extend([
            "",
            "### 5.3 다음 단계",
            "",
            "1. 각 클러스터의 대표 샘플을 LLM에 제공",
            "2. 클러스터별 최적 OCR 전략/프롬프트 생성",
            "3. 실제 OCR 시 문서가 속한 클러스터의 전략 적용",
            "",
        ])

        report = "\n".join(lines)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)

        return str(output_path)

    def export_representative_samples(
        self,
        output_dir: Path,
        sample_per_cluster: int = 1
    ) -> dict:
        """
        각 클러스터의 대표 샘플 이미지 추출

        Args:
            output_dir: 출력 디렉토리
            sample_per_cluster: 클러스터당 샘플 수

        Returns:
            추출 결과 통계
        """
        import shutil

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        with self.db.connection() as conn:
            # 클러스터별 샘플 조회
            clusters = conn.execute("""
                SELECT id, sample_documents FROM clusters
                ORDER BY document_count DESC
            """).fetchall()

        if not clusters:
            return {"error": "No clusters found"}

        exported = 0
        manifest = []

        for cluster in clusters:
            cluster_id = cluster["id"]
            samples = json.loads(cluster["sample_documents"] or "[]")

            if not samples:
                continue

            # 샘플 이미지 복사
            for i, doc_id in enumerate(samples[:sample_per_cluster]):
                with self.db.connection() as conn:
                    row = conn.execute(
                        "SELECT image_path FROM headers WHERE document_id = ?",
                        (doc_id,)
                    ).fetchone()

                if row and row["image_path"]:
                    src_path = Path(row["image_path"])
                    if src_path.exists():
                        # 클러스터ID_샘플번호_문서ID.jpg
                        dst_name = f"cluster_{cluster_id:03d}_{i:02d}_{doc_id}.jpg"
                        dst_path = output_dir / dst_name
                        shutil.copy2(src_path, dst_path)
                        exported += 1

                        manifest.append({
                            "cluster_id": cluster_id,
                            "document_id": doc_id,
                            "filename": dst_name,
                            "original_path": str(src_path)
                        })

        # 매니페스트 저장
        manifest_path = output_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        return {
            "total_clusters": len(clusters),
            "exported_samples": exported,
            "output_dir": str(output_dir),
            "manifest_path": str(manifest_path)
        }

    def generate_cluster_preview_html(self, output_path: Path, samples_per_cluster: int = 3) -> str:
        """
        클러스터 미리보기 HTML 생성

        각 클러스터의 샘플 이미지를 보여주는 HTML 페이지 생성
        """
        import base64

        with self.db.connection() as conn:
            clusters = conn.execute("""
                SELECT c.id, c.name, c.description, c.document_count, c.sample_documents
                FROM clusters c
                ORDER BY c.document_count DESC
            """).fetchall()

        if not clusters:
            return ""

        total_clusters = len(clusters)
        html_parts = [f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Header Clusters Preview</title>
    <style>
        body {{ font-family: sans-serif; margin: 20px; background: #f5f5f5; }}
        .cluster {{ background: white; margin: 20px 0; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .cluster-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }}
        .cluster-title {{ font-size: 18px; font-weight: bold; }}
        .cluster-count {{ color: #666; }}
        .samples {{ display: flex; gap: 10px; flex-wrap: wrap; }}
        .sample img {{ max-width: 300px; max-height: 150px; border: 1px solid #ddd; }}
        .sample {{ text-align: center; font-size: 12px; color: #666; }}
    </style>
</head>
<body>
    <h1>Header Clusters Preview</h1>
    <p>총 {total_clusters} 클러스터</p>
"""]

        for cluster in clusters[:50]:  # 상위 50개만
            cluster_id = cluster["id"]
            samples = json.loads(cluster["sample_documents"] or "[]")[:samples_per_cluster]

            html_parts.append(f"""
    <div class="cluster">
        <div class="cluster-header">
            <span class="cluster-title">Cluster {cluster_id}: {cluster['name']}</span>
            <span class="cluster-count">{cluster['document_count']} documents</span>
        </div>
        <p>{cluster['description']}</p>
        <div class="samples">
""")

            for doc_id in samples:
                with self.db.connection() as conn:
                    row = conn.execute(
                        "SELECT image_path FROM headers WHERE document_id = ?",
                        (doc_id,)
                    ).fetchone()

                if row and row["image_path"]:
                    img_path = Path(row["image_path"])
                    if img_path.exists():
                        # 이미지를 base64로 인코딩
                        with open(img_path, "rb") as f:
                            img_data = base64.b64encode(f.read()).decode()
                        html_parts.append(f"""
            <div class="sample">
                <img src="data:image/jpeg;base64,{img_data}" alt="{doc_id}">
                <div>{doc_id}</div>
            </div>
""")

            html_parts.append("        </div>\n    </div>")

        html_parts.append("</body></html>")

        html_content = "\n".join(html_parts)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return str(output_path)


def run_visual_clustering(
    limit: Optional[int] = None,
    n_clusters: int = DEFAULT_N_CLUSTERS,
    workers: int = 8
) -> dict:
    """시각적 클러스터링 실행"""
    from rich.console import Console

    console = Console()
    clusterer = HeaderVisualClusterer(n_clusters=n_clusters)

    # 상태 확인
    status = clusterer.get_status()
    console.print(f"[cyan]총 헤더: {status['total_headers']}[/cyan]")

    if status['total_headers'] == 0:
        console.print("[yellow]헤더 이미지가 없습니다. 먼저 헤더를 추출하세요.[/yellow]")
        return {"error": "No headers"}

    # 특징 추출
    console.print("\n[cyan]1단계: 특징 추출[/cyan]")
    features = clusterer.extract_all_features(limit=limit, workers=workers)

    if not features:
        console.print("[red]특징 추출 실패[/red]")
        return {"error": "Feature extraction failed"}

    # 클러스터링
    console.print("\n[cyan]2단계: 클러스터링[/cyan]")
    result = clusterer.run_clustering(features)

    if "error" in result:
        console.print(f"[red]오류: {result['error']}[/red]")
        return result

    console.print(f"\n[green]완료![/green]")
    console.print(f"총 문서: {result['total_documents']}")
    console.print(f"클러스터 수: {result['n_clusters']}")

    # 상위 10개 클러스터 출력
    console.print("\n상위 10개 클러스터:")
    sorted_clusters = sorted(result["cluster_sizes"].items(), key=lambda x: x[1], reverse=True)[:10]
    for cluster_id, count in sorted_clusters:
        console.print(f"  Cluster {cluster_id}: {count} documents")

    return result


# CLI
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Header Visual Clustering")
    parser.add_argument("command", choices=["cluster", "status", "export", "preview", "analyze"],
                       help="Command to run")
    parser.add_argument("--limit", "-l", type=int, help="Maximum images to process")
    parser.add_argument("--clusters", "-c", type=int, default=DEFAULT_N_CLUSTERS,
                       help="Number of clusters")
    parser.add_argument("--workers", "-w", type=int, default=8,
                       help="Number of parallel workers")
    parser.add_argument("--output", "-o", type=str, default="/tmp/header_samples",
                       help="Output directory for export")

    args = parser.parse_args()

    if args.command == "cluster":
        run_visual_clustering(
            limit=args.limit,
            n_clusters=args.clusters,
            workers=args.workers
        )

    elif args.command == "status":
        clusterer = HeaderVisualClusterer()

        status = clusterer.get_status()
        print(f"\n=== Visual Clustering Status ===")
        print(f"Total headers: {status['total_headers']}")
        print(f"Clustered: {status['clustered']}")
        print(f"Unclustered: {status['unclustered']}")
        print(f"Clusters: {status['n_clusters']}")

        clusters = clusterer.get_cluster_summary()
        if clusters:
            print(f"\n=== Top Clusters ===")
            for c in clusters[:10]:
                print(f"  {c['name']}: {c['document_count']} docs - {c['description']}")

    elif args.command == "export":
        # 대표 샘플 추출
        clusterer = HeaderVisualClusterer()
        result = clusterer.export_representative_samples(Path(args.output))

        if "error" in result:
            print(f"오류: {result['error']}")
        else:
            print(f"\n=== 대표 샘플 추출 완료 ===")
            print(f"클러스터 수: {result['total_clusters']}")
            print(f"추출된 샘플: {result['exported_samples']}")
            print(f"출력 디렉토리: {result['output_dir']}")
            print(f"매니페스트: {result['manifest_path']}")

    elif args.command == "preview":
        # HTML 미리보기 생성
        clusterer = HeaderVisualClusterer()
        output_path = Path(args.output) / "preview.html"
        result = clusterer.generate_cluster_preview_html(output_path)

        if result:
            print(f"\n=== 미리보기 생성 완료 ===")
            print(f"HTML 파일: {result}")
            print(f"브라우저에서 열어서 확인하세요.")
        else:
            print("오류: 클러스터가 없습니다.")

    elif args.command == "analyze":
        # 상관관계 분석 및 보고서 생성
        clusterer = HeaderVisualClusterer()

        print("[*] 클러스터 상관관계 분석 중...")
        analysis = clusterer.analyze_cluster_correlations()

        if "error" in analysis:
            print(f"오류: {analysis['error']}")
        else:
            # 콘솔 요약 출력
            print(f"\n{'='*60}")
            print("클러스터 상관관계 분석 결과")
            print(f"{'='*60}")
            print(f"총 클러스터: {analysis['total_clusters']}")
            print(f"총 문서: {analysis['total_documents']}")

            print(f"\n[법령 유형(Jenis) 상관관계]")
            jc = analysis['correlation_summary']['jenis_correlation']
            print(f"  높은 순수도 클러스터: {jc['high_purity_clusters']}개 ({jc['percentage']}%)")
            print(f"  해석: {jc['interpretation']}")

            print(f"\n[연도(Tahun) 상관관계]")
            tc = analysis['correlation_summary']['tahun_correlation']
            print(f"  높은 순수도 클러스터: {tc['high_purity_clusters']}개 ({tc['percentage']}%)")
            print(f"  해석: {tc['interpretation']}")

            # 보고서 생성
            report_path = Path(args.output) / "analysis_report.md"
            result = clusterer.generate_analysis_report(report_path)

            if result:
                print(f"\n[*] 상세 보고서 생성: {result}")

            # JSON 저장
            json_path = Path(args.output) / "analysis.json"
            json_path.parent.mkdir(parents=True, exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as f:
                # clusters의 sample_documents를 제한하여 저장
                save_analysis = analysis.copy()
                for c in save_analysis.get("clusters", []):
                    c["sample_documents"] = c["sample_documents"][:5]
                json.dump(save_analysis, f, ensure_ascii=False, indent=2, default=str)
            print(f"[*] JSON 데이터 저장: {json_path}")
