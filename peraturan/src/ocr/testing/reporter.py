"""
리포트 생성 모듈 (Reporter)

테스트 결과를 JSON/Markdown 형식으로 리포트 생성

출력 포맷:
- {run_id}_report.json - 원시 메트릭
- {run_id}_report.md - 사람이 읽는 요약
- {run_id}_issues.json - 문제 문서 목록
"""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Any

from .metrics import AggregateMetrics, DocumentMetrics, QualityTier


class ReportFormat(Enum):
    """Report output format"""
    JSON = "json"
    MARKDOWN = "md"
    ALL = "all"


@dataclass
class ReportConfig:
    """Report generation configuration"""
    include_page_details: bool = False
    include_issues_only: bool = False
    quality_threshold: float = 0.75  # Below this = issue
    max_issues_shown: int = 50


class Reporter:
    """
    리포트 생성기

    테스트 결과를 다양한 형식으로 출력

    사용법:
        reporter = Reporter(output_dir)
        reporter.generate(run_id, aggregate_metrics, doc_metrics_list)
    """

    def __init__(self, output_dir: Path):
        """
        Args:
            output_dir: Report output directory
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        run_id: str,
        aggregate: AggregateMetrics,
        doc_metrics: List[DocumentMetrics],
        formats: List[ReportFormat] = None,
        config: ReportConfig = None,
    ) -> Dict[str, Path]:
        """
        Generate reports in specified formats

        Args:
            run_id: Test run ID
            aggregate: Aggregate metrics
            doc_metrics: List of document metrics
            formats: Output formats (default: all)
            config: Report configuration

        Returns:
            Dict of format -> file path
        """
        formats = formats or [ReportFormat.ALL]
        config = config or ReportConfig()

        # Create run directory
        run_dir = self.output_dir / "runs" / run_id / "report"
        run_dir.mkdir(parents=True, exist_ok=True)

        output_paths = {}

        if ReportFormat.JSON in formats or ReportFormat.ALL in formats:
            path = self._generate_json(run_dir, run_id, aggregate, doc_metrics, config)
            output_paths["json"] = path

        if ReportFormat.MARKDOWN in formats or ReportFormat.ALL in formats:
            path = self._generate_markdown(run_dir, run_id, aggregate, doc_metrics, config)
            output_paths["markdown"] = path

        # Always generate issues file
        issues_path = self._generate_issues(run_dir, run_id, doc_metrics, config)
        output_paths["issues"] = issues_path

        return output_paths

    def _generate_json(
        self,
        run_dir: Path,
        run_id: str,
        aggregate: AggregateMetrics,
        doc_metrics: List[DocumentMetrics],
        config: ReportConfig,
    ) -> Path:
        """Generate JSON report"""
        report = {
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "summary": aggregate.to_dict(),
            "documents": [],
        }

        for dm in doc_metrics:
            if config.include_page_details:
                report["documents"].append(dm.to_dict())
            else:
                report["documents"].append(dm.to_summary())

        path = run_dir / f"{run_id}_report.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)

        return path

    def _generate_markdown(
        self,
        run_dir: Path,
        run_id: str,
        aggregate: AggregateMetrics,
        doc_metrics: List[DocumentMetrics],
        config: ReportConfig,
    ) -> Path:
        """Generate Markdown report"""
        agg = aggregate

        lines = []
        lines.append(f"# OCR Test Report: {run_id}")
        lines.append("")
        lines.append(f"**Phase**: {agg.phase}")
        lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # Summary section
        lines.append("## Summary")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total Samples | {agg.total_samples} |")
        lines.append(f"| Processed | {agg.processed} |")
        lines.append(f"| Failed | {agg.failed} |")
        lines.append(f"| Avg Quality Score | {agg.avg_quality_score:.4f} |")
        lines.append(f"| Median Quality Score | {agg.median_quality_score:.4f} |")
        lines.append(f"| Min Quality Score | {agg.min_quality_score:.4f} |")
        lines.append(f"| Max Quality Score | {agg.max_quality_score:.4f} |")
        lines.append(f"| Manual Review Needed | {agg.manual_review_count} ({agg.manual_review_ratio*100:.1f}%) |")
        lines.append("")

        # Quality distribution
        lines.append("## Quality Distribution")
        lines.append("")
        lines.append(f"| Tier | Count | Percentage |")
        lines.append(f"|------|-------|------------|")
        for tier, count in agg.quality_distribution.items():
            pct = count / agg.total_samples * 100 if agg.total_samples > 0 else 0
            lines.append(f"| {tier.capitalize()} | {count} | {pct:.1f}% |")
        lines.append("")

        # Strategy distribution
        lines.append("## Strategy Distribution (Pages)")
        lines.append("")
        total_pages = sum(agg.strategy_distribution.values())
        lines.append(f"| Strategy | Count | Percentage |")
        lines.append(f"|----------|-------|------------|")
        for strategy, count in agg.strategy_distribution.items():
            pct = count / total_pages * 100 if total_pages > 0 else 0
            lines.append(f"| {strategy.upper()} | {count} | {pct:.1f}% |")
        lines.append("")

        # By category
        lines.append("## Quality by Category")
        lines.append("")
        lines.append(f"| Category | Count | Avg Quality | Manual Review |")
        lines.append(f"|----------|-------|-------------|---------------|")
        for cat, data in sorted(agg.by_category.items()):
            lines.append(f"| {cat} | {data['count']} | {data['avg_quality']:.4f} | {data['manual_review']} |")
        lines.append("")

        # By era
        lines.append("## Quality by Era")
        lines.append("")
        lines.append(f"| Era | Count | Avg Quality | Manual Review |")
        lines.append(f"|-----|-------|-------------|---------------|")
        era_order = ["pre-1970", "1970-1999", "2000+", "unknown"]
        for era in era_order:
            if era in agg.by_era:
                data = agg.by_era[era]
                lines.append(f"| {era} | {data['count']} | {data['avg_quality']:.4f} | {data['manual_review']} |")
        lines.append("")

        # Issues section (limited)
        issues = [dm for dm in doc_metrics if dm.avg_quality_score < config.quality_threshold or dm.status == "failed"]
        if issues:
            lines.append("## Issues Detected")
            lines.append("")
            lines.append(f"Total issues: {len(issues)}")
            lines.append("")

            # Show top issues
            issues_to_show = issues[:config.max_issues_shown]
            lines.append(f"| Document ID | Category | Year | Quality | Status |")
            lines.append(f"|-------------|----------|------|---------|--------|")
            for dm in sorted(issues_to_show, key=lambda x: x.avg_quality_score):
                lines.append(f"| {dm.doc_id[:40]} | {dm.category} | {dm.year or 'N/A'} | {dm.avg_quality_score:.4f} | {dm.status} |")

            if len(issues) > config.max_issues_shown:
                lines.append("")
                lines.append(f"*...and {len(issues) - config.max_issues_shown} more issues. See issues.json for full list.*")
        lines.append("")

        # Success criteria
        lines.append("## Success Criteria Check")
        lines.append("")

        success_rate = agg.processed / agg.total_samples if agg.total_samples > 0 else 0
        avg_quality = agg.avg_quality_score
        manual_review_pct = agg.manual_review_ratio * 100

        phase_criteria = {
            "phase1": {"success_rate": 0.95, "avg_quality": 0.75},
            "phase2": {"success_rate": 0.95, "avg_quality": 0.78, "manual_review_pct": 5.0},
            "phase3": {"success_rate": 0.97, "avg_quality": 0.80, "manual_review_pct": 3.0},
        }

        criteria = phase_criteria.get(agg.phase, phase_criteria["phase1"])

        lines.append(f"| Criterion | Target | Actual | Pass |")
        lines.append(f"|-----------|--------|--------|------|")

        success_pass = "Yes" if success_rate >= criteria["success_rate"] else "No"
        lines.append(f"| Success Rate | >= {criteria['success_rate']*100:.0f}% | {success_rate*100:.1f}% | {success_pass} |")

        quality_pass = "Yes" if avg_quality >= criteria["avg_quality"] else "No"
        lines.append(f"| Avg Quality | >= {criteria['avg_quality']:.2f} | {avg_quality:.4f} | {quality_pass} |")

        if "manual_review_pct" in criteria:
            mr_pass = "Yes" if manual_review_pct <= criteria["manual_review_pct"] else "No"
            lines.append(f"| Manual Review | <= {criteria['manual_review_pct']:.0f}% | {manual_review_pct:.1f}% | {mr_pass} |")

        lines.append("")

        # Recommendations
        lines.append("## Recommendations")
        lines.append("")

        if avg_quality < 0.75:
            lines.append("- **Quality below target**: Consider adjusting OCR parameters or quality thresholds")
        if manual_review_pct > 5:
            lines.append("- **High manual review rate**: Investigate common failure patterns")
        if agg.by_era.get("pre-1970", {}).get("avg_quality", 1.0) < 0.6:
            lines.append("- **Pre-1970 documents have low quality**: May need specialized processing")

        # Era-specific issues
        for era, data in agg.by_era.items():
            if data.get("avg_quality", 1.0) < 0.7:
                lines.append(f"- Documents from **{era}** show low average quality ({data['avg_quality']:.2f})")

        if not issues:
            lines.append("- No critical issues detected. Proceed to next phase.")

        lines.append("")
        lines.append("---")
        lines.append(f"*Report generated by OCR Testing Module v0.1.0*")

        path = run_dir / f"{run_id}_report.md"
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        return path

    def _generate_issues(
        self,
        run_dir: Path,
        run_id: str,
        doc_metrics: List[DocumentMetrics],
        config: ReportConfig,
    ) -> Path:
        """Generate issues JSON file"""
        issues = []

        for dm in doc_metrics:
            issue_reasons = []

            if dm.status == "failed":
                issue_reasons.append("processing_failed")
            if dm.avg_quality_score < config.quality_threshold:
                issue_reasons.append("low_quality")
            if dm.pages_manual > 0:
                issue_reasons.append("needs_manual_review")
            if dm.broken_char_ratio > 0.10:
                issue_reasons.append("high_broken_char_ratio")

            if issue_reasons:
                issues.append({
                    "doc_id": dm.doc_id,
                    "category": dm.category,
                    "year": dm.year,
                    "avg_quality_score": round(dm.avg_quality_score, 4),
                    "quality_tier": dm.quality_tier().value,
                    "status": dm.status,
                    "pages_manual": dm.pages_manual,
                    "total_pages": dm.total_pages,
                    "broken_char_ratio": round(dm.broken_char_ratio, 4),
                    "issue_reasons": issue_reasons,
                })

        # Sort by severity (quality score ascending)
        issues.sort(key=lambda x: x["avg_quality_score"])

        report = {
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "total_issues": len(issues),
            "issues": issues,
        }

        path = run_dir / f"{run_id}_issues.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        return path

    def compare_runs(
        self,
        run_ids: List[str],
        aggregates: List[AggregateMetrics],
    ) -> Path:
        """
        Generate comparison report between runs

        Args:
            run_ids: List of run IDs to compare
            aggregates: List of corresponding aggregate metrics

        Returns:
            Path to comparison report
        """
        lines = []
        lines.append("# OCR Test Run Comparison")
        lines.append("")
        lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # Summary table
        lines.append("## Summary Comparison")
        lines.append("")

        header = "| Metric |"
        divider = "|--------|"
        for run_id in run_ids:
            header += f" {run_id} |"
            divider += "--------|"

        lines.append(header)
        lines.append(divider)

        # Add rows for each metric
        metrics = [
            ("Total Samples", "total_samples"),
            ("Processed", "processed"),
            ("Failed", "failed"),
            ("Avg Quality", "avg_quality_score"),
            ("Median Quality", "median_quality_score"),
            ("Manual Review %", "manual_review_ratio"),
        ]

        for label, attr in metrics:
            row = f"| {label} |"
            for agg in aggregates:
                val = getattr(agg, attr, 0)
                if isinstance(val, float):
                    if "ratio" in attr.lower():
                        row += f" {val*100:.1f}% |"
                    else:
                        row += f" {val:.4f} |"
                else:
                    row += f" {val} |"
            lines.append(row)

        lines.append("")

        # Quality improvement
        if len(aggregates) >= 2:
            lines.append("## Quality Trend")
            lines.append("")

            first = aggregates[0]
            last = aggregates[-1]

            diff = last.avg_quality_score - first.avg_quality_score
            trend = "improved" if diff > 0 else "declined" if diff < 0 else "unchanged"

            lines.append(f"Quality from {run_ids[0]} to {run_ids[-1]}: **{trend}** ({diff:+.4f})")
            lines.append("")

            mr_diff = last.manual_review_ratio - first.manual_review_ratio
            mr_trend = "improved" if mr_diff < 0 else "worsened" if mr_diff > 0 else "unchanged"
            lines.append(f"Manual review rate: **{mr_trend}** ({mr_diff*100:+.1f}%)")

        lines.append("")
        lines.append("---")
        lines.append(f"*Comparison report generated by OCR Testing Module*")

        path = self.output_dir / f"comparison_{'_'.join(run_ids)}.md"
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        return path


# CLI test
if __name__ == "__main__":
    from .metrics import MetricsCalculator, DocumentMetrics

    # Create test data
    calc = MetricsCalculator()

    doc_metrics_list = []
    for i in range(10):
        page_results = [
            {"page_num": j, "quality_score": 0.7 + (i + j) * 0.02, "strategy": "text" if j % 2 == 0 else "ocr"}
            for j in range(5)
        ]
        dm = calc.calculate_document_metrics(
            doc_id=f"test_doc_{i}",
            category="uu" if i < 5 else "pp",
            year=2000 + i,
            page_results=page_results,
            processing_time_ms=1000 + i * 100,
        )
        doc_metrics_list.append(dm)

    agg = calc.aggregate("test_run", "phase1", doc_metrics_list)

    # Generate report
    reporter = Reporter(Path("/tmp/ocr_reports"))
    paths = reporter.generate("test_run", agg, doc_metrics_list)

    print("Generated reports:")
    for fmt, path in paths.items():
        print(f"  {fmt}: {path}")
