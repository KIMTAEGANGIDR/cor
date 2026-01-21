"""
계층화 샘플링 모듈 (Stratified Sampler)

문서 유형 및 연대별 비례 샘플링 지원

문서 분포:
| 유형 | 수량 | Phase 1 (100) | Phase 2 (500) | Phase 3 (1000) |
|------|------|---------------|---------------|----------------|
| pp | 3,918 | 26 | 125 | 245 |
| keppres | 3,201 | 20 | 100 | 200 |
| perpres | 2,510 | 16 | 80 | 157 |
| uu | 1,881 | 12 | 60 | 118 |
| other | 1,690 | 12 | 55 | 106 |
| permen | 1,472 | 11 | 47 | 92 |
| inpres | 265 | 2 | 10 | 17 |
| perppu | 49 | 1 | 5 | 10 |
| tapmpr | 34 | 1 | 5 | 7 |
| perban | 5 | 2 | 3 | 5 |
| penpres | 1 | 1 | 1 | 1 |
"""

import json
import random
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

# Document type mapping from jenis to category
JENIS_TO_CATEGORY = {
    "PERATURAN PEMERINTAH": "pp",
    "PP": "pp",
    "KEPUTUSAN PRESIDEN": "keppres",
    "KEPPRES": "keppres",
    "PERATURAN PRESIDEN": "perpres",
    "PERPRES": "perpres",
    "UNDANG-UNDANG": "uu",
    "UU": "uu",
    "PERATURAN MENTERI": "permen",
    "PERMEN": "permen",
    "INSTRUKSI PRESIDEN": "inpres",
    "INPRES": "inpres",
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "perppu",
    "PERPPU": "perppu",
    "KETETAPAN MPR": "tapmpr",
    "TAPMPR": "tapmpr",
    "PERATURAN BADAN/LEMBAGA": "perban",
    "PERBAN": "perban",
    "PENETAPAN PRESIDEN": "penpres",
    "PENPRES": "penpres",
}

# Phase configurations
PHASE_CONFIG = {
    "phase1": {
        "size": 100,
        "description": "Pilot - pipeline verification, threshold calibration",
        "distribution": {
            "pp": 26, "keppres": 20, "perpres": 16, "uu": 12,
            "other": 12, "permen": 11, "inpres": 2, "perppu": 1,
            "tapmpr": 1, "perban": 2, "penpres": 1
        }
    },
    "phase2": {
        "size": 500,
        "description": "Validation - edge case discovery",
        "distribution": {
            "pp": 125, "keppres": 100, "perpres": 80, "uu": 60,
            "other": 55, "permen": 47, "inpres": 10, "perppu": 5,
            "tapmpr": 5, "perban": 3, "penpres": 1
        }
    },
    "phase3": {
        "size": 1000,
        "description": "Pre-production - final verification",
        "distribution": {
            "pp": 245, "keppres": 200, "perpres": 157, "uu": 118,
            "other": 106, "permen": 92, "inpres": 17, "perppu": 10,
            "tapmpr": 7, "perban": 5, "penpres": 1
        }
    }
}

# Era distribution percentages
ERA_DISTRIBUTION = {
    "pre-1970": 0.10,  # Old scans, expected low quality
    "1970-1999": 0.20,
    "2000+": 0.70,     # Digital PDFs, expected high quality
}


@dataclass
class SampleConfig:
    """Sampling configuration"""
    phase: str
    size: int
    seed: int = 42
    era_weighted: bool = True
    exclude_processed: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SampleResult:
    """Sampling result"""
    run_id: str
    phase: str
    samples: List[dict]
    config: SampleConfig
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # Distribution stats
    by_category: Dict[str, int] = field(default_factory=dict)
    by_era: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "phase": self.phase,
            "total_samples": len(self.samples),
            "config": self.config.to_dict(),
            "created_at": self.created_at,
            "distribution": {
                "by_category": self.by_category,
                "by_era": self.by_era,
            },
            "samples": self.samples,
        }

    def save(self, path: Path):
        """Save to JSON file"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path) -> "SampleResult":
        """Load from JSON file"""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        config = SampleConfig(**data["config"])
        result = cls(
            run_id=data["run_id"],
            phase=data["phase"],
            samples=data["samples"],
            config=config,
            created_at=data["created_at"],
            by_category=data["distribution"]["by_category"],
            by_era=data["distribution"]["by_era"],
        )
        return result


class StratifiedSampler:
    """
    계층화 샘플러

    문서 유형 및 연대별 비례 샘플링을 수행

    사용법:
        sampler = StratifiedSampler(db_path)
        result = sampler.sample("phase1")
        result.save("samples.json")
    """

    def __init__(self, db_path: Path):
        """
        Args:
            db_path: OCR pipeline database path
        """
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _classify_jenis(self, jenis: str) -> str:
        """Classify document jenis to category"""
        jenis_upper = jenis.upper()
        for key, category in JENIS_TO_CATEGORY.items():
            if key in jenis_upper:
                return category
        return "other"

    def _classify_era(self, year: Optional[int]) -> str:
        """Classify year to era"""
        if year is None:
            return "unknown"
        if year < 1970:
            return "pre-1970"
        elif year < 2000:
            return "1970-1999"
        else:
            return "2000+"

    def get_distribution(self) -> Dict[str, Dict[str, int]]:
        """
        Get current document distribution

        Returns:
            {"by_category": {...}, "by_era": {...}}
        """
        conn = self._get_connection()

        # By category
        rows = conn.execute(
            "SELECT jenis, COUNT(*) as cnt FROM documents GROUP BY jenis"
        ).fetchall()

        by_category = {}
        for row in rows:
            category = self._classify_jenis(row["jenis"])
            by_category[category] = by_category.get(category, 0) + row["cnt"]

        # By era
        rows = conn.execute(
            "SELECT tahun, COUNT(*) as cnt FROM documents GROUP BY tahun"
        ).fetchall()

        by_era = {"pre-1970": 0, "1970-1999": 0, "2000+": 0, "unknown": 0}
        for row in rows:
            era = self._classify_era(row["tahun"])
            by_era[era] = by_era.get(era, 0) + row["cnt"]

        conn.close()

        return {"by_category": by_category, "by_era": by_era}

    def sample(
        self,
        phase: str,
        run_id: Optional[str] = None,
        seed: Optional[int] = None,
        custom_size: Optional[int] = None,
    ) -> SampleResult:
        """
        Perform stratified sampling

        Args:
            phase: Phase name (phase1, phase2, phase3)
            run_id: Optional run ID (auto-generated if not provided)
            seed: Random seed for reproducibility
            custom_size: Override phase size

        Returns:
            SampleResult containing sampled documents
        """
        if phase not in PHASE_CONFIG:
            raise ValueError(f"Unknown phase: {phase}. Use: {list(PHASE_CONFIG.keys())}")

        phase_config = PHASE_CONFIG[phase]
        size = custom_size or phase_config["size"]
        distribution = phase_config["distribution"]

        # Generate run ID if not provided
        if run_id is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_id = f"{phase}_{timestamp}"

        # Set random seed
        seed = seed or 42
        random.seed(seed)

        config = SampleConfig(
            phase=phase,
            size=size,
            seed=seed,
        )

        conn = self._get_connection()

        # Sample documents by category
        all_samples = []
        by_category = {}

        for category, target_count in distribution.items():
            # Scale if custom_size is provided
            if custom_size:
                scale_factor = custom_size / phase_config["size"]
                target_count = max(1, int(target_count * scale_factor))

            samples = self._sample_category(conn, category, target_count)
            all_samples.extend(samples)
            by_category[category] = len(samples)

        conn.close()

        # Shuffle to randomize processing order
        random.shuffle(all_samples)

        # Calculate era distribution
        by_era = {"pre-1970": 0, "1970-1999": 0, "2000+": 0, "unknown": 0}
        for sample in all_samples:
            era = self._classify_era(sample.get("year"))
            by_era[era] += 1

        result = SampleResult(
            run_id=run_id,
            phase=phase,
            samples=all_samples,
            config=config,
            by_category=by_category,
            by_era=by_era,
        )

        return result

    def _sample_category(
        self,
        conn: sqlite3.Connection,
        category: str,
        count: int,
    ) -> List[dict]:
        """Sample documents from a category with era weighting"""

        # Build WHERE clause for category
        if category == "other":
            # Get all known categories
            known_jenis = list(JENIS_TO_CATEGORY.keys())
            placeholders = ",".join(["?" for _ in known_jenis])
            where_clause = f"jenis NOT IN ({placeholders})"
            params = known_jenis
        else:
            # Get jenis patterns for this category
            matching_jenis = [k for k, v in JENIS_TO_CATEGORY.items() if v == category]
            if not matching_jenis:
                return []
            like_clauses = " OR ".join([f"UPPER(jenis) LIKE ?" for _ in matching_jenis])
            where_clause = f"({like_clauses})"
            params = [f"%{j}%" for j in matching_jenis]

        # Get all documents in category
        query = f"""
            SELECT id, jenis, tahun, tentang, pdf_path
            FROM documents
            WHERE {where_clause} AND pdf_path IS NOT NULL
        """
        rows = conn.execute(query, params).fetchall()

        if not rows:
            return []

        # Group by era for weighted sampling
        by_era = {"pre-1970": [], "1970-1999": [], "2000+": [], "unknown": []}
        for row in rows:
            doc = dict(row)
            doc["category"] = category
            doc["year"] = doc.pop("tahun")
            era = self._classify_era(doc["year"])
            by_era[era].append(doc)

        # Sample with era weighting
        samples = []
        remaining = count

        for era, weight in ERA_DISTRIBUTION.items():
            era_docs = by_era.get(era, [])
            if not era_docs:
                continue

            era_target = int(count * weight)
            era_sample = min(era_target, len(era_docs), remaining)

            if era_sample > 0:
                selected = random.sample(era_docs, era_sample)
                samples.extend(selected)
                remaining -= len(selected)

        # Fill remaining from any era
        if remaining > 0:
            all_remaining = []
            for era, docs in by_era.items():
                for doc in docs:
                    if doc not in samples:
                        all_remaining.append(doc)

            if all_remaining:
                extra = random.sample(all_remaining, min(remaining, len(all_remaining)))
                samples.extend(extra)

        return samples


# CLI test
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m peraturan.src.ocr.testing.sampler <db_path> [phase]")
        sys.exit(1)

    db_path = Path(sys.argv[1])
    phase = sys.argv[2] if len(sys.argv) > 2 else "phase1"

    sampler = StratifiedSampler(db_path)

    print("=== Document Distribution ===")
    dist = sampler.get_distribution()
    print(f"By category: {dist['by_category']}")
    print(f"By era: {dist['by_era']}")

    print(f"\n=== Sampling {phase} ===")
    result = sampler.sample(phase)
    print(f"Run ID: {result.run_id}")
    print(f"Total samples: {len(result.samples)}")
    print(f"By category: {result.by_category}")
    print(f"By era: {result.by_era}")

    # Save sample
    output_path = Path(f"samples_{result.run_id}.json")
    result.save(output_path)
    print(f"\nSaved to: {output_path}")
