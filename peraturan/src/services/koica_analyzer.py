"""KOICA pre-analysis data analyzer for Indonesian legal document system.

This module provides comprehensive analysis of peraturan.go.id and BPK data
for the KOICA project feasibility study.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.utils.logging import get_logger

logger = get_logger("koica_analyzer")

# Korean translation map for document types
JENIS_KOREAN = {
    "UNDANG-UNDANG": "법률 (UU)",
    "PERATURAN PEMERINTAH": "정부령 (PP)",
    "PERATURAN PRESIDEN": "대통령령 (Perpres)",
    "PERATURAN MENTERI": "장관령 (Permen)",
    "PERATURAN BADAN/LEMBAGA": "기관규정",
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "긴급정부령 (Perppu)",
    "PERATURAN DAERAH": "지방조례 (Perda)",
    "KEPUTUSAN PRESIDEN": "대통령결정 (Keppres)",
}

# Mapping from BPK bentuk to peraturan jenis (for comparison)
BENTUK_JENIS_MAP = {
    "Undang-undang (UU)": "UNDANG-UNDANG",
    "Peraturan Pemerintah (PP)": "PERATURAN PEMERINTAH",
    "Peraturan Presiden (Perpres)": "PERATURAN PRESIDEN",
    "Peraturan Pemerintah Pengganti Undang-Undang (Perppu)": "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG",
    "Keputusan Presiden (Keppres)": "KEPUTUSAN PRESIDEN",
}

# Status values considered as "Berlaku" (active)
BERLAKU_KEYWORDS = ["Berlaku", "berlaku"]
TIDAK_BERLAKU_KEYWORDS = ["Tidak Berlaku", "tidak berlaku", "Dicabut", "dicabut"]


@dataclass
class AnalysisResult:
    """Container for analysis results."""

    analysis_date: str = field(default_factory=lambda: datetime.now().isoformat())
    peraturan_total: int = 0
    bpk_total: int = 0
    results: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "analysis_date": self.analysis_date,
            "peraturan_total": self.peraturan_total,
            "bpk_total": self.bpk_total,
            **self.results,
        }


class KoicaAnalyzer:
    """Analyzer for KOICA pre-analysis data report.

    Provides comprehensive analysis of both peraturan.go.id and BPK databases
    for the KOICA Indonesian Legal Information System project.
    """

    def __init__(
        self,
        peraturan_db: Path,
        bpk_db: Optional[Path] = None,
    ):
        """Initialize analyzer with database paths.

        Args:
            peraturan_db: Path to peraturan.go.id SQLite database
            bpk_db: Path to BPK SQLite database (optional)
        """
        self.peraturan_db = peraturan_db
        self.bpk_db = bpk_db

        if not peraturan_db.exists():
            raise FileNotFoundError(f"Peraturan database not found: {peraturan_db}")

        if bpk_db and not bpk_db.exists():
            logger.warning(f"BPK database not found: {bpk_db}")
            self.bpk_db = None

    def _get_peraturan_connection(self) -> sqlite3.Connection:
        """Get connection to peraturan database."""
        conn = sqlite3.connect(str(self.peraturan_db))
        conn.row_factory = sqlite3.Row
        return conn

    def _get_bpk_connection(self) -> Optional[sqlite3.Connection]:
        """Get connection to BPK database."""
        if not self.bpk_db:
            return None
        conn = sqlite3.connect(str(self.bpk_db))
        conn.row_factory = sqlite3.Row
        return conn

    def analyze_document_types(self) -> dict:
        """Analyze document type distribution.

        Returns:
            Dictionary with type distribution analysis
        """
        logger.info("Analyzing document type distribution...")

        conn = self._get_peraturan_connection()
        cursor = conn.cursor()

        # Get total and breakdown by jenis
        cursor.execute("""
            SELECT
                jenis,
                COUNT(*) as total,
                SUM(CASE WHEN status LIKE '%Berlaku%' AND status NOT LIKE '%Tidak%' THEN 1 ELSE 0 END) as berlaku,
                SUM(CASE WHEN status LIKE '%Tidak Berlaku%' OR status LIKE '%Dicabut%' THEN 1 ELSE 0 END) as tidak_berlaku
            FROM peraturan
            GROUP BY jenis
            ORDER BY total DESC
        """)

        rows = cursor.fetchall()
        total = sum(row["total"] for row in rows)

        types_data = []
        for row in rows:
            pct = (row["total"] / total * 100) if total > 0 else 0
            berlaku_rate = (row["berlaku"] / row["total"] * 100) if row["total"] > 0 else 0
            types_data.append({
                "jenis": row["jenis"],
                "jenis_korean": JENIS_KOREAN.get(row["jenis"], row["jenis"]),
                "total": row["total"],
                "percentage": round(pct, 2),
                "berlaku": row["berlaku"],
                "tidak_berlaku": row["tidak_berlaku"],
                "berlaku_rate": round(berlaku_rate, 1),
            })

        conn.close()

        return {
            "total_documents": total,
            "type_count": len(types_data),
            "types": types_data,
        }

    def analyze_year_distribution(self) -> dict:
        """Analyze year distribution of documents.

        Returns:
            Dictionary with year distribution analysis
        """
        logger.info("Analyzing year distribution...")

        conn = self._get_peraturan_connection()
        cursor = conn.cursor()

        # Get distribution by year
        cursor.execute("""
            SELECT
                tahun,
                COUNT(*) as total,
                SUM(CASE WHEN status LIKE '%Berlaku%' AND status NOT LIKE '%Tidak%' THEN 1 ELSE 0 END) as berlaku
            FROM peraturan
            WHERE tahun IS NOT NULL
            GROUP BY tahun
            ORDER BY tahun ASC
        """)

        rows = cursor.fetchall()

        # Create year data
        yearly_data = []
        for row in rows:
            yearly_data.append({
                "year": row["tahun"],
                "total": row["total"],
                "berlaku": row["berlaku"],
            })

        # Create decade summary
        decade_summary = {}
        for row in rows:
            decade = (row["tahun"] // 10) * 10
            if decade not in decade_summary:
                decade_summary[decade] = {"total": 0, "berlaku": 0}
            decade_summary[decade]["total"] += row["total"]
            decade_summary[decade]["berlaku"] += row["berlaku"]

        # Find min/max years
        years = [row["tahun"] for row in rows if row["tahun"]]
        min_year = min(years) if years else None
        max_year = max(years) if years else None

        # Find top 5 years by document count
        sorted_by_count = sorted(rows, key=lambda x: x["total"], reverse=True)[:5]
        top_years = [{"year": r["tahun"], "total": r["total"]} for r in sorted_by_count]

        # Find missing years
        if min_year and max_year:
            all_years = set(range(min_year, max_year + 1))
            existing_years = set(years)
            missing_years = sorted(all_years - existing_years)
        else:
            missing_years = []

        conn.close()

        return {
            "year_range": {"min": min_year, "max": max_year},
            "span_years": (max_year - min_year + 1) if min_year and max_year else 0,
            "top_years": top_years,
            "missing_years": missing_years[:20],  # Limit to 20
            "missing_count": len(missing_years),
            "decade_summary": [
                {"decade": f"{d}s", "total": v["total"], "berlaku": v["berlaku"]}
                for d, v in sorted(decade_summary.items())
            ],
            "yearly_data": yearly_data,
        }

    def analyze_agency_distribution(self) -> dict:
        """Analyze agency (pemrakarsa) distribution.

        Returns:
            Dictionary with agency distribution analysis
        """
        logger.info("Analyzing agency distribution...")

        conn = self._get_peraturan_connection()
        cursor = conn.cursor()

        # Get total count
        cursor.execute("SELECT COUNT(*) FROM peraturan")
        total = cursor.fetchone()[0]

        # Get count with pemrakarsa
        cursor.execute("SELECT COUNT(*) FROM peraturan WHERE pemrakarsa IS NOT NULL AND pemrakarsa != ''")
        with_agency = cursor.fetchone()[0]

        # Get top 20 agencies
        cursor.execute("""
            SELECT
                pemrakarsa,
                COUNT(*) as total,
                SUM(CASE WHEN status LIKE '%Berlaku%' AND status NOT LIKE '%Tidak%' THEN 1 ELSE 0 END) as berlaku
            FROM peraturan
            WHERE pemrakarsa IS NOT NULL AND pemrakarsa != ''
            GROUP BY pemrakarsa
            ORDER BY total DESC
            LIMIT 20
        """)

        rows = cursor.fetchall()

        agencies_data = []
        for row in rows:
            pct = (row["total"] / total * 100) if total > 0 else 0
            berlaku_rate = (row["berlaku"] / row["total"] * 100) if row["total"] > 0 else 0
            agencies_data.append({
                "pemrakarsa": row["pemrakarsa"],
                "total": row["total"],
                "percentage": round(pct, 2),
                "berlaku": row["berlaku"],
                "berlaku_rate": round(berlaku_rate, 1),
            })

        # Get unique agency count
        cursor.execute("SELECT COUNT(DISTINCT pemrakarsa) FROM peraturan WHERE pemrakarsa IS NOT NULL AND pemrakarsa != ''")
        unique_agencies = cursor.fetchone()[0]

        conn.close()

        return {
            "total_documents": total,
            "with_agency": with_agency,
            "without_agency": total - with_agency,
            "without_agency_pct": round((total - with_agency) / total * 100, 2) if total > 0 else 0,
            "unique_agencies": unique_agencies,
            "top_20_agencies": agencies_data,
        }

    def analyze_data_completeness(self) -> dict:
        """Analyze field completeness across the database.

        Returns:
            Dictionary with field completeness analysis
        """
        logger.info("Analyzing data completeness...")

        conn = self._get_peraturan_connection()
        cursor = conn.cursor()

        # Get total count
        cursor.execute("SELECT COUNT(*) FROM peraturan")
        total = cursor.fetchone()[0]

        # Define fields to analyze
        fields = [
            "slug", "jenis", "nomor", "tahun", "tentang", "pemrakarsa",
            "tempat_penetapan", "tanggal_penetapan", "pejabat_penetapan",
            "tahun_pengundangan", "nomor_pengundangan", "nomor_tambahan",
            "tanggal_pengundangan", "pejabat_pengundangan", "status",
            "pdf_url", "local_pdf_path", "source_url"
        ]

        field_stats = []
        for field_name in fields:
            try:
                cursor.execute(f"""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN {field_name} IS NULL OR {field_name} = '' THEN 1 ELSE 0 END) as null_count,
                        COUNT(DISTINCT {field_name}) as distinct_count
                    FROM peraturan
                """)
                row = cursor.fetchone()

                null_count = row["null_count"]
                complete_count = total - null_count
                completeness = (complete_count / total * 100) if total > 0 else 0

                field_stats.append({
                    "field": field_name,
                    "total": total,
                    "complete": complete_count,
                    "null_or_empty": null_count,
                    "completeness_pct": round(completeness, 1),
                    "distinct_values": row["distinct_count"],
                })
            except sqlite3.OperationalError:
                # Field might not exist
                continue

        # Sort by completeness
        field_stats.sort(key=lambda x: x["completeness_pct"], reverse=True)

        # Calculate overall completeness
        total_possible = total * len(field_stats)
        total_filled = sum(f["complete"] for f in field_stats)
        overall_completeness = (total_filled / total_possible * 100) if total_possible > 0 else 0

        # Categorize fields
        complete_fields = [f for f in field_stats if f["completeness_pct"] >= 99]
        partial_fields = [f for f in field_stats if 50 <= f["completeness_pct"] < 99]
        sparse_fields = [f for f in field_stats if f["completeness_pct"] < 50]

        conn.close()

        return {
            "total_records": total,
            "total_fields": len(field_stats),
            "overall_completeness_pct": round(overall_completeness, 1),
            "complete_fields_count": len(complete_fields),
            "partial_fields_count": len(partial_fields),
            "sparse_fields_count": len(sparse_fields),
            "fields": field_stats,
            "summary": {
                "complete_fields": [f["field"] for f in complete_fields],
                "partial_fields": [f["field"] for f in partial_fields],
                "sparse_fields": [f["field"] for f in sparse_fields],
            },
        }

    def analyze_pdf_availability(self) -> dict:
        """Analyze PDF availability and download status.

        Returns:
            Dictionary with PDF availability analysis
        """
        logger.info("Analyzing PDF availability...")

        conn = self._get_peraturan_connection()
        cursor = conn.cursor()

        # Overall statistics
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN pdf_url IS NOT NULL AND pdf_url != '' THEN 1 ELSE 0 END) as with_url,
                SUM(CASE WHEN local_pdf_path IS NOT NULL AND local_pdf_path != '' THEN 1 ELSE 0 END) as downloaded
            FROM peraturan
        """)
        overall = cursor.fetchone()

        total = overall["total"]
        with_url = overall["with_url"]
        downloaded = overall["downloaded"]
        failed = with_url - downloaded
        no_url = total - with_url

        # By document type
        cursor.execute("""
            SELECT
                jenis,
                COUNT(*) as total,
                SUM(CASE WHEN pdf_url IS NOT NULL AND pdf_url != '' THEN 1 ELSE 0 END) as with_url,
                SUM(CASE WHEN local_pdf_path IS NOT NULL AND local_pdf_path != '' THEN 1 ELSE 0 END) as downloaded
            FROM peraturan
            GROUP BY jenis
            ORDER BY total DESC
        """)

        by_type = []
        for row in cursor.fetchall():
            type_total = row["total"]
            type_url = row["with_url"]
            type_downloaded = row["downloaded"]

            url_rate = (type_url / type_total * 100) if type_total > 0 else 0
            download_rate = (type_downloaded / type_total * 100) if type_total > 0 else 0

            by_type.append({
                "jenis": row["jenis"],
                "jenis_korean": JENIS_KOREAN.get(row["jenis"], row["jenis"]),
                "total": type_total,
                "with_url": type_url,
                "downloaded": type_downloaded,
                "no_url": type_total - type_url,
                "url_rate": round(url_rate, 1),
                "download_rate": round(download_rate, 1),
            })

        # Calculate file sizes if PDFs exist
        pdf_dir = self.peraturan_db.parent / "pdfs"
        total_size_bytes = 0
        pdf_count = 0

        if pdf_dir.exists():
            for pdf_file in pdf_dir.rglob("*.pdf"):
                try:
                    total_size_bytes += pdf_file.stat().st_size
                    pdf_count += 1
                except OSError:
                    continue

        total_size_gb = total_size_bytes / (1024 ** 3)
        avg_size_mb = (total_size_bytes / pdf_count / (1024 ** 2)) if pdf_count > 0 else 0

        conn.close()

        return {
            "total_documents": total,
            "with_url": with_url,
            "downloaded": downloaded,
            "failed_or_pending": failed,
            "no_url": no_url,
            "url_availability_pct": round(with_url / total * 100, 1) if total > 0 else 0,
            "download_completion_pct": round(downloaded / with_url * 100, 1) if with_url > 0 else 0,
            "overall_download_pct": round(downloaded / total * 100, 1) if total > 0 else 0,
            "storage": {
                "pdf_files_found": pdf_count,
                "total_size_gb": round(total_size_gb, 2),
                "avg_file_size_mb": round(avg_size_mb, 2),
            },
            "by_type": by_type,
        }

    def analyze_legal_status(self) -> dict:
        """Analyze legal status (Berlaku/Tidak Berlaku) distribution.

        Returns:
            Dictionary with legal status analysis
        """
        logger.info("Analyzing legal status distribution...")

        conn = self._get_peraturan_connection()
        cursor = conn.cursor()

        # Get total count
        cursor.execute("SELECT COUNT(*) FROM peraturan")
        total = cursor.fetchone()[0]

        # Categorize status
        cursor.execute("""
            SELECT
                CASE
                    WHEN status LIKE '%Tidak Berlaku%' OR status LIKE '%Dicabut%' THEN 'Tidak Berlaku'
                    WHEN status LIKE '%Berlaku%' THEN 'Berlaku'
                    WHEN status IS NULL OR status = '' THEN 'Unknown'
                    ELSE 'Other'
                END as status_category,
                COUNT(*) as count
            FROM peraturan
            GROUP BY status_category
            ORDER BY count DESC
        """)

        status_summary = {}
        for row in cursor.fetchall():
            status_summary[row["status_category"]] = row["count"]

        berlaku = status_summary.get("Berlaku", 0)
        tidak_berlaku = status_summary.get("Tidak Berlaku", 0)
        unknown = status_summary.get("Unknown", 0)
        other = status_summary.get("Other", 0)

        # By document type
        cursor.execute("""
            SELECT
                jenis,
                COUNT(*) as total,
                SUM(CASE WHEN status LIKE '%Berlaku%' AND status NOT LIKE '%Tidak%' THEN 1 ELSE 0 END) as berlaku,
                SUM(CASE WHEN status LIKE '%Tidak Berlaku%' OR status LIKE '%Dicabut%' THEN 1 ELSE 0 END) as tidak_berlaku,
                SUM(CASE WHEN status IS NULL OR status = '' THEN 1 ELSE 0 END) as unknown
            FROM peraturan
            GROUP BY jenis
            ORDER BY total DESC
        """)

        by_type = []
        for row in cursor.fetchall():
            type_total = row["total"]
            type_berlaku = row["berlaku"]
            berlaku_rate = (type_berlaku / type_total * 100) if type_total > 0 else 0

            by_type.append({
                "jenis": row["jenis"],
                "jenis_korean": JENIS_KOREAN.get(row["jenis"], row["jenis"]),
                "total": type_total,
                "berlaku": type_berlaku,
                "tidak_berlaku": row["tidak_berlaku"],
                "unknown": row["unknown"],
                "berlaku_rate": round(berlaku_rate, 1),
            })

        # Get unique status values count
        cursor.execute("SELECT COUNT(DISTINCT status) FROM peraturan WHERE status IS NOT NULL")
        unique_status_count = cursor.fetchone()[0]

        # Get top 10 status values
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM peraturan
            WHERE status IS NOT NULL AND status != ''
            GROUP BY status
            ORDER BY count DESC
            LIMIT 10
        """)
        top_status_values = [{"status": row["status"], "count": row["count"]} for row in cursor.fetchall()]

        conn.close()

        return {
            "total_documents": total,
            "berlaku": berlaku,
            "tidak_berlaku": tidak_berlaku,
            "unknown": unknown,
            "other": other,
            "berlaku_pct": round(berlaku / total * 100, 1) if total > 0 else 0,
            "tidak_berlaku_pct": round(tidak_berlaku / total * 100, 1) if total > 0 else 0,
            "unknown_pct": round(unknown / total * 100, 1) if total > 0 else 0,
            "unique_status_values": unique_status_count,
            "top_status_values": top_status_values,
            "by_type": by_type,
        }

    def compare_with_bpk(self) -> dict:
        """Compare peraturan.go.id data with BPK data.

        Returns:
            Dictionary with comparison analysis
        """
        logger.info("Comparing with BPK data...")

        if not self.bpk_db:
            return {
                "available": False,
                "message": "BPK database not available",
            }

        per_conn = self._get_peraturan_connection()
        bpk_conn = self._get_bpk_connection()

        if not bpk_conn:
            per_conn.close()
            return {
                "available": False,
                "message": "BPK database connection failed",
            }

        per_cursor = per_conn.cursor()
        bpk_cursor = bpk_conn.cursor()

        # Get totals
        per_cursor.execute("SELECT COUNT(*) FROM peraturan")
        per_total = per_cursor.fetchone()[0]

        bpk_cursor.execute("SELECT COUNT(*) FROM peraturan")
        bpk_total = bpk_cursor.fetchone()[0]

        # Comparison by comparable types
        comparison_results = []

        for bpk_bentuk, per_jenis in BENTUK_JENIS_MAP.items():
            # Get peraturan.go.id count
            per_cursor.execute(
                "SELECT COUNT(*) FROM peraturan WHERE jenis = ?",
                (per_jenis,)
            )
            per_count = per_cursor.fetchone()[0]

            # Get BPK count
            bpk_cursor.execute(
                "SELECT COUNT(*) FROM peraturan WHERE bentuk = ?",
                (bpk_bentuk,)
            )
            bpk_count = bpk_cursor.fetchone()[0]

            difference = per_count - bpk_count

            comparison_results.append({
                "type": per_jenis,
                "type_korean": JENIS_KOREAN.get(per_jenis, per_jenis),
                "bpk_bentuk": bpk_bentuk,
                "peraturan_count": per_count,
                "bpk_count": bpk_count,
                "difference": difference,
                "difference_pct": round(abs(difference) / max(per_count, bpk_count, 1) * 100, 1),
            })

        # Status comparison for matching types
        status_comparison = []
        for bpk_bentuk, per_jenis in BENTUK_JENIS_MAP.items():
            # Peraturan.go.id berlaku rate
            per_cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status LIKE '%Berlaku%' AND status NOT LIKE '%Tidak%' THEN 1 ELSE 0 END) as berlaku
                FROM peraturan WHERE jenis = ?
            """, (per_jenis,))
            per_row = per_cursor.fetchone()
            per_berlaku_rate = (per_row["berlaku"] / per_row["total"] * 100) if per_row["total"] > 0 else 0

            # BPK berlaku rate
            bpk_cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'Berlaku' THEN 1 ELSE 0 END) as berlaku
                FROM peraturan WHERE bentuk = ?
            """, (bpk_bentuk,))
            bpk_row = bpk_cursor.fetchone()
            bpk_berlaku_rate = (bpk_row["berlaku"] / bpk_row["total"] * 100) if bpk_row["total"] > 0 else 0

            status_comparison.append({
                "type": per_jenis,
                "type_korean": JENIS_KOREAN.get(per_jenis, per_jenis),
                "peraturan_berlaku_rate": round(per_berlaku_rate, 1),
                "bpk_berlaku_rate": round(bpk_berlaku_rate, 1),
                "rate_difference": round(abs(per_berlaku_rate - bpk_berlaku_rate), 1),
            })

        # Overall summary
        per_comparable_total = sum(r["peraturan_count"] for r in comparison_results)
        bpk_comparable_total = sum(r["bpk_count"] for r in comparison_results)

        per_conn.close()
        bpk_conn.close()

        return {
            "available": True,
            "peraturan_total": per_total,
            "bpk_total": bpk_total,
            "comparable_types_count": len(comparison_results),
            "peraturan_comparable_total": per_comparable_total,
            "bpk_comparable_total": bpk_comparable_total,
            "type_comparison": comparison_results,
            "status_comparison": status_comparison,
            "notes": [
                "BPK database includes more granular document types (126 vs 8)",
                "Only directly comparable types are included in this comparison",
                "Status classification may differ between sources",
            ],
        }

    def run_full_analysis(self) -> dict:
        """Run all analyses and return combined results.

        Returns:
            Dictionary with all analysis results
        """
        logger.info("Running full KOICA data analysis...")

        # Get totals first
        conn = self._get_peraturan_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM peraturan")
        per_total = cursor.fetchone()[0]
        conn.close()

        bpk_total = 0
        if self.bpk_db:
            bpk_conn = self._get_bpk_connection()
            if bpk_conn:
                bpk_cursor = bpk_conn.cursor()
                bpk_cursor.execute("SELECT COUNT(*) FROM peraturan")
                bpk_total = bpk_cursor.fetchone()[0]
                bpk_conn.close()

        # Run all analyses
        results = {
            "meta": {
                "analysis_date": datetime.now().isoformat(),
                "analysis_type": "KOICA Pre-analysis Data Status Report",
                "peraturan_db_path": str(self.peraturan_db),
                "bpk_db_path": str(self.bpk_db) if self.bpk_db else None,
            },
            "summary": {
                "peraturan_total": per_total,
                "bpk_total": bpk_total,
            },
            "document_types": self.analyze_document_types(),
            "year_distribution": self.analyze_year_distribution(),
            "agency_distribution": self.analyze_agency_distribution(),
            "data_completeness": self.analyze_data_completeness(),
            "pdf_availability": self.analyze_pdf_availability(),
            "legal_status": self.analyze_legal_status(),
            "bpk_comparison": self.compare_with_bpk(),
        }

        # Add key findings to summary
        results["summary"]["key_findings"] = self._generate_key_findings(results)

        logger.info("Full analysis completed")
        return results

    def _generate_key_findings(self, results: dict) -> list[str]:
        """Generate key findings from analysis results.

        Args:
            results: Full analysis results

        Returns:
            List of key finding strings
        """
        findings = []

        # Document count
        per_total = results["summary"]["peraturan_total"]
        findings.append(f"총 {per_total:,}건의 법령 데이터 (peraturan.go.id)")

        # Type distribution
        types = results["document_types"]["types"]
        if types:
            top_type = types[0]
            findings.append(
                f"최다 유형: {top_type['jenis_korean']} ({top_type['total']:,}건, {top_type['percentage']}%)"
            )

        # Year range
        year_data = results["year_distribution"]
        if year_data["year_range"]["min"] and year_data["year_range"]["max"]:
            findings.append(
                f"연도 범위: {year_data['year_range']['min']}-{year_data['year_range']['max']} "
                f"({year_data['span_years']}년간)"
            )

        # Data completeness
        completeness = results["data_completeness"]
        findings.append(
            f"데이터 완성도: {completeness['overall_completeness_pct']}% "
            f"(완전 필드 {completeness['complete_fields_count']}개)"
        )

        # PDF availability
        pdf = results["pdf_availability"]
        findings.append(
            f"PDF 확보율: {pdf['overall_download_pct']}% "
            f"({pdf['downloaded']:,}/{pdf['total_documents']:,}건)"
        )

        # Legal status
        status = results["legal_status"]
        findings.append(
            f"현행 법령: {status['berlaku_pct']}% "
            f"({status['berlaku']:,}건)"
        )

        # BPK comparison
        if results["bpk_comparison"].get("available"):
            bpk = results["bpk_comparison"]
            findings.append(
                f"BPK 데이터: {bpk['bpk_total']:,}건 (비교 가능 유형 {bpk['comparable_types_count']}개)"
            )

        return findings
