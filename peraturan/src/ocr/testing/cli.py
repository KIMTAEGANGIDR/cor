"""
OCR Testing CLI

테스트 실행을 위한 CLI 명령어

명령어:
- sample: 샘플 생성
- run: 테스트 실행
- report: 리포트 생성
- compare: 실행 비교
- validate: 검증 실행
- calibrate: 임계값 보정
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import click
from rich.console import Console
from rich.progress import Progress, TaskID
from rich.table import Table

from .sampler import StratifiedSampler, SampleResult, PHASE_CONFIG
from .metrics import MetricsCalculator, DocumentMetrics, AggregateMetrics
from .reporter import Reporter, ReportFormat, ReportConfig
from .validator import Validator, ValidationThresholds

# Default paths
DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "ocr_pipeline.db"
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "ocr_output"

console = Console()


@click.group()
@click.option('--db', type=click.Path(exists=False), default=None,
              help='Database path (default: peraturan/data/ocr_pipeline.db)')
@click.option('--output', type=click.Path(), default=None,
              help='Output directory (default: peraturan/data/ocr_output)')
@click.pass_context
def cli(ctx, db, output):
    """OCR Testing and Validation CLI"""
    ctx.ensure_object(dict)
    ctx.obj['db_path'] = Path(db) if db else DEFAULT_DB_PATH
    ctx.obj['output_dir'] = Path(output) if output else DEFAULT_OUTPUT_DIR


@cli.command()
@click.option('--phase', '-p', type=click.Choice(['phase1', 'phase2', 'phase3']),
              default='phase1', help='Test phase')
@click.option('--size', '-s', type=int, default=None,
              help='Override sample size')
@click.option('--seed', type=int, default=42,
              help='Random seed for reproducibility')
@click.option('--run-id', '-r', type=str, default=None,
              help='Custom run ID (auto-generated if not provided)')
@click.pass_context
def sample(ctx, phase, size, seed, run_id):
    """Generate stratified samples for testing"""
    db_path = ctx.obj['db_path']
    output_dir = ctx.obj['output_dir']

    if not db_path.exists():
        console.print(f"[red]Error: Database not found: {db_path}[/red]")
        sys.exit(1)

    console.print(f"[cyan]Generating samples for {phase}...[/cyan]")

    try:
        sampler = StratifiedSampler(db_path)

        # Show current distribution
        dist = sampler.get_distribution()
        console.print("\n[bold]Current Document Distribution:[/bold]")
        console.print(f"  By category: {dist['by_category']}")
        console.print(f"  By era: {dist['by_era']}")

        # Generate samples
        result = sampler.sample(
            phase=phase,
            run_id=run_id,
            seed=seed,
            custom_size=size,
        )

        # Save samples
        output_dir.mkdir(parents=True, exist_ok=True)
        run_dir = output_dir / "runs" / result.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        samples_path = run_dir / "samples.json"
        result.save(samples_path)

        # Save config
        config_path = run_dir / "config.json"
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({
                "run_id": result.run_id,
                "phase": phase,
                "size": len(result.samples),
                "seed": seed,
                "created_at": result.created_at,
                "phase_config": PHASE_CONFIG[phase],
            }, f, indent=2)

        # Print summary
        console.print(f"\n[green]Samples generated successfully![/green]")
        console.print(f"  Run ID: {result.run_id}")
        console.print(f"  Total samples: {len(result.samples)}")
        console.print(f"  By category: {result.by_category}")
        console.print(f"  By era: {result.by_era}")
        console.print(f"\n  Saved to: {samples_path}")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.option('--run-id', '-r', type=str, required=True,
              help='Run ID to execute')
@click.option('--input', '-i', type=click.Path(exists=True), default=None,
              help='Samples JSON file (default: auto-detect from run-id)')
@click.option('--workers', '-w', type=int, default=1,
              help='Number of parallel workers')
@click.option('--dry-run', is_flag=True,
              help='Dry run without processing')
@click.pass_context
def run(ctx, run_id, input, workers, dry_run):
    """Run OCR test on samples"""
    output_dir = ctx.obj['output_dir']

    # Determine input path
    if input:
        samples_path = Path(input)
    else:
        samples_path = output_dir / "runs" / run_id / "samples.json"

    if not samples_path.exists():
        console.print(f"[red]Error: Samples file not found: {samples_path}[/red]")
        console.print(f"[yellow]Hint: Generate samples first with 'sample --run-id {run_id}'[/yellow]")
        sys.exit(1)

    # Load samples
    sample_result = SampleResult.load(samples_path)
    samples = sample_result.samples

    console.print(f"[cyan]Running OCR test: {run_id}[/cyan]")
    console.print(f"  Phase: {sample_result.phase}")
    console.print(f"  Samples: {len(samples)}")

    if dry_run:
        console.print("\n[yellow]Dry run mode - no processing will occur[/yellow]")
        console.print("\nSample documents:")
        for i, s in enumerate(samples[:5]):
            console.print(f"  {i+1}. {s.get('id', 'unknown')} ({s.get('category', 'unknown')})")
        if len(samples) > 5:
            console.print(f"  ... and {len(samples) - 5} more")
        return

    # Setup output directories
    run_dir = output_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "results").mkdir(exist_ok=True)
    (output_dir / "text").mkdir(exist_ok=True)
    (output_dir / "manual_review").mkdir(exist_ok=True)

    # Import pipeline (late import to avoid circular dependencies)
    try:
        from ..pipeline import OCRPipeline, PageStatus, ProcessingStrategy
    except ImportError:
        console.print("[red]Error: OCRPipeline not available[/red]")
        console.print("[yellow]Make sure the pipeline module is installed[/yellow]")
        sys.exit(1)

    # Initialize metrics calculator
    calc = MetricsCalculator()
    doc_metrics_list = []

    # Results file (JSONL for streaming)
    results_path = run_dir / "results.jsonl"

    # Initialize OCR Pipeline
    pipeline = OCRPipeline(
        input_dir=output_dir / "pdfs_temp",  # Dummy, we process individual files
        output_dir=run_dir / "output",
        use_gpu=True,  # Use GPU for OCR
    )

    # Process samples
    start_time = time.time()

    with Progress() as progress:
        task = progress.add_task("[green]Processing...", total=len(samples))

        for sample in samples:
            doc_id = sample.get("id", "unknown")
            pdf_path = sample.get("pdf_path")

            if not pdf_path or not Path(pdf_path).exists():
                console.print(f"[yellow]Skipping {doc_id}: PDF not found[/yellow]")
                progress.update(task, advance=1)
                continue

            try:
                # Process document with real pipeline
                doc_start = time.time()
                doc_result = pipeline.process_document(Path(pdf_path))
                processing_time_ms = int((time.time() - doc_start) * 1000)

                if doc_result is None:
                    console.print(f"[yellow]Skipping {doc_id}: Already processed[/yellow]")
                    progress.update(task, advance=1)
                    continue

                # Convert pipeline results to page_results format
                page_results = []
                for page in doc_result.pages:
                    page_results.append({
                        "page_num": page.page_num,
                        "quality_score": page.quality_score,
                        "strategy": page.strategy.value if hasattr(page.strategy, 'value') else str(page.strategy),
                        "ocr_confidence": page.ocr_confidence,
                        "word_count": len(page.text.split()) if page.text else 0,
                        "char_count": len(page.text) if page.text else 0,
                        "broken_ratio": 0.0,  # Would need quality_scorer details
                        "needs_manual_review": page.status == PageStatus.MANUAL_REVIEW,
                    })

                doc_metrics = calc.calculate_document_metrics(
                    doc_id=doc_id,
                    category=sample.get("category", "other"),
                    year=sample.get("year"),
                    page_results=page_results,
                    processing_time_ms=processing_time_ms,
                )

                doc_metrics_list.append(doc_metrics)

                # Write to JSONL
                with open(results_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(doc_metrics.to_summary(), ensure_ascii=False) + '\n')

            except Exception as e:
                console.print(f"[red]Error processing {doc_id}: {e}[/red]")
                import traceback
                traceback.print_exc()

            progress.update(task, advance=1)

    # Calculate aggregate metrics
    elapsed_time = time.time() - start_time
    aggregate = calc.aggregate(run_id, sample_result.phase, doc_metrics_list)

    # Save aggregate
    agg_path = run_dir / "aggregate.json"
    with open(agg_path, 'w', encoding='utf-8') as f:
        json.dump(aggregate.to_dict(), f, ensure_ascii=False, indent=2, default=str)

    # Print summary
    console.print(f"\n[green]Test completed![/green]")
    console.print(f"  Processed: {aggregate.processed}/{aggregate.total_samples}")
    console.print(f"  Failed: {aggregate.failed}")
    console.print(f"  Avg Quality: {aggregate.avg_quality_score:.4f}")
    console.print(f"  Manual Review: {aggregate.manual_review_count} ({aggregate.manual_review_ratio*100:.1f}%)")
    console.print(f"  Elapsed: {elapsed_time:.1f}s")
    console.print(f"\n  Results: {results_path}")


def _process_document_mock(doc_id: str, pdf_path: str) -> List[dict]:
    """Mock document processing (replace with actual pipeline)"""
    # This is a placeholder - in real implementation, use OCRPipeline
    import random
    random.seed(hash(doc_id) % 2**32)

    num_pages = random.randint(3, 15)
    page_results = []

    for i in range(num_pages):
        quality = random.uniform(0.5, 0.98)
        strategy = "text" if quality > 0.92 else "ocr"

        page_results.append({
            "page_num": i,
            "quality_score": quality,
            "strategy": strategy,
            "ocr_confidence": random.uniform(0.6, 0.95) if strategy == "ocr" else 0,
            "word_count": random.randint(100, 500),
            "char_count": random.randint(500, 3000),
            "broken_ratio": random.uniform(0, 0.05),
            "needs_manual_review": quality < 0.5,
        })

    return page_results


@cli.command()
@click.option('--run-id', '-r', type=str, required=True,
              help='Run ID to generate report for')
@click.option('--format', '-f', type=click.Choice(['json', 'md', 'all']),
              default='all', help='Report format')
@click.option('--include-pages', is_flag=True,
              help='Include page-level details in JSON report')
@click.pass_context
def report(ctx, run_id, format, include_pages):
    """Generate test report"""
    output_dir = ctx.obj['output_dir']
    run_dir = output_dir / "runs" / run_id

    # Load aggregate metrics
    agg_path = run_dir / "aggregate.json"
    if not agg_path.exists():
        console.print(f"[red]Error: Aggregate metrics not found: {agg_path}[/red]")
        console.print(f"[yellow]Hint: Run test first with 'run --run-id {run_id}'[/yellow]")
        sys.exit(1)

    with open(agg_path, 'r', encoding='utf-8') as f:
        agg_data = json.load(f)

    aggregate = AggregateMetrics(**{k: v for k, v in agg_data.items()
                                    if k in AggregateMetrics.__dataclass_fields__})

    # Load document metrics from JSONL
    results_path = run_dir / "results.jsonl"
    doc_metrics_list = []

    if results_path.exists():
        with open(results_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    dm = DocumentMetrics(
                        doc_id=data["doc_id"],
                        category=data["category"],
                        year=data["year"],
                        avg_quality_score=data["avg_quality_score"],
                        total_pages=data["total_pages"],
                        pages_text=data["pages_text"],
                        pages_ocr=data["pages_ocr"],
                        pages_manual=data["pages_manual"],
                        status=data["status"],
                        processing_time_ms=data["processing_time_ms"],
                    )
                    doc_metrics_list.append(dm)

    # Generate reports
    reporter = Reporter(output_dir)
    config = ReportConfig(include_page_details=include_pages)

    formats_list = [ReportFormat.ALL] if format == 'all' else [ReportFormat(format)]
    paths = reporter.generate(run_id, aggregate, doc_metrics_list, formats_list, config)

    console.print(f"\n[green]Reports generated![/green]")
    for fmt, path in paths.items():
        console.print(f"  {fmt}: {path}")


@cli.command()
@click.option('--runs', '-r', type=str, required=True,
              help='Comma-separated run IDs to compare')
@click.pass_context
def compare(ctx, runs):
    """Compare multiple test runs"""
    output_dir = ctx.obj['output_dir']

    run_ids = [r.strip() for r in runs.split(',')]
    if len(run_ids) < 2:
        console.print("[red]Error: Need at least 2 runs to compare[/red]")
        sys.exit(1)

    aggregates = []

    for run_id in run_ids:
        agg_path = output_dir / "runs" / run_id / "aggregate.json"
        if not agg_path.exists():
            console.print(f"[red]Error: Aggregate not found for {run_id}[/red]")
            sys.exit(1)

        with open(agg_path, 'r', encoding='utf-8') as f:
            agg_data = json.load(f)

        aggregate = AggregateMetrics(**{k: v for k, v in agg_data.items()
                                        if k in AggregateMetrics.__dataclass_fields__})
        aggregates.append(aggregate)

    # Generate comparison
    reporter = Reporter(output_dir)
    path = reporter.compare_runs(run_ids, aggregates)

    console.print(f"\n[green]Comparison report generated![/green]")
    console.print(f"  {path}")

    # Print summary table
    table = Table(title="Run Comparison")
    table.add_column("Metric")
    for run_id in run_ids:
        table.add_column(run_id)

    metrics = [
        ("Samples", "total_samples"),
        ("Processed", "processed"),
        ("Avg Quality", "avg_quality_score"),
        ("Manual Review %", "manual_review_ratio"),
    ]

    for label, attr in metrics:
        row = [label]
        for agg in aggregates:
            val = getattr(agg, attr, 0)
            if isinstance(val, float):
                if "ratio" in attr:
                    row.append(f"{val*100:.1f}%")
                else:
                    row.append(f"{val:.4f}")
            else:
                row.append(str(val))
        table.add_row(*row)

    console.print(table)


@cli.command()
@click.option('--run-id', '-r', type=str, required=True,
              help='Run ID to validate')
@click.option('--thresholds', '-t', type=click.Path(exists=True), default=None,
              help='Custom thresholds JSON file')
@click.pass_context
def validate(ctx, run_id, thresholds):
    """Validate test results and detect issues"""
    output_dir = ctx.obj['output_dir']
    run_dir = output_dir / "runs" / run_id

    # Load thresholds
    if thresholds:
        thresh = ValidationThresholds.load(Path(thresholds))
    else:
        thresh = ValidationThresholds()

    # Load document metrics
    results_path = run_dir / "results.jsonl"
    if not results_path.exists():
        console.print(f"[red]Error: Results not found: {results_path}[/red]")
        sys.exit(1)

    doc_metrics_list = []
    with open(results_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                dm = DocumentMetrics(
                    doc_id=data["doc_id"],
                    category=data["category"],
                    year=data["year"],
                    avg_quality_score=data["avg_quality_score"],
                    total_pages=data["total_pages"],
                    pages_text=data["pages_text"],
                    pages_ocr=data["pages_ocr"],
                    pages_manual=data["pages_manual"],
                    status=data["status"],
                )
                doc_metrics_list.append(dm)

    # Validate
    validator = Validator(thresh)
    result = validator.validate(run_id, doc_metrics_list)

    # Save validation result
    validation_path = run_dir / "validation.json"
    result.save(validation_path)

    # Print summary
    console.print(f"\n[cyan]Validation Results: {run_id}[/cyan]")
    console.print(f"  Total documents: {result.total_documents}")
    console.print(f"  Documents with issues: {result.documents_with_issues}")
    console.print(f"  Total issues: {result.total_issues}")

    console.print(f"\n[bold]By Severity:[/bold]")
    for severity, count in result.issues_by_severity.items():
        color = {"critical": "red", "high": "yellow", "medium": "cyan", "low": "dim"}.get(severity, "white")
        console.print(f"  [{color}]{severity}: {count}[/{color}]")

    console.print(f"\n[bold]By Type:[/bold]")
    for issue_type, count in result.issues_by_type.items():
        console.print(f"  {issue_type}: {count}")

    console.print(f"\n  Saved to: {validation_path}")


@cli.command()
@click.option('--run-id', '-r', type=str, required=True,
              help='Run ID to use for calibration')
@click.option('--target-success', type=float, default=0.95,
              help='Target success rate')
@click.option('--target-manual-review', type=float, default=0.03,
              help='Target manual review rate')
@click.option('--output', '-o', type=click.Path(), default=None,
              help='Output path for calibrated thresholds')
@click.pass_context
def calibrate(ctx, run_id, target_success, target_manual_review, output):
    """Calibrate thresholds based on pilot results"""
    output_dir = ctx.obj['output_dir']
    run_dir = output_dir / "runs" / run_id

    # Load document metrics
    results_path = run_dir / "results.jsonl"
    if not results_path.exists():
        console.print(f"[red]Error: Results not found: {results_path}[/red]")
        sys.exit(1)

    doc_metrics_list = []
    with open(results_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                dm = DocumentMetrics(
                    doc_id=data["doc_id"],
                    category=data["category"],
                    year=data["year"],
                    avg_quality_score=data["avg_quality_score"],
                )
                doc_metrics_list.append(dm)

    # Calibrate
    original = ValidationThresholds()
    validator = Validator(original)

    calibrated = validator.calibrate_thresholds(
        doc_metrics_list,
        target_success_rate=target_success,
        target_manual_review_rate=target_manual_review,
    )

    # Generate report
    cal_report = validator.generate_calibration_report(original, calibrated)

    # Save calibrated thresholds
    output_path = Path(output) if output else run_dir / "calibration.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(cal_report, f, indent=2)

    # Save thresholds separately
    thresholds_path = run_dir / "thresholds.json"
    calibrated.save(thresholds_path)

    # Print results
    console.print(f"\n[cyan]Calibration Results[/cyan]")
    console.print(f"  Target success rate: {target_success*100:.0f}%")
    console.print(f"  Target manual review: {target_manual_review*100:.0f}%")

    console.print(f"\n[bold]Threshold Changes:[/bold]")
    for key, change in cal_report.get("changes", {}).items():
        console.print(f"  {key}: {change['original']:.4f} -> {change['calibrated']:.4f} ({change['delta']:+.4f})")

    if not cal_report.get("changes"):
        console.print("  [dim]No changes needed[/dim]")

    console.print(f"\n  Calibration report: {output_path}")
    console.print(f"  Thresholds: {thresholds_path}")


@cli.command()
@click.pass_context
def status(ctx):
    """Show testing status and available runs"""
    output_dir = ctx.obj['output_dir']
    runs_dir = output_dir / "runs"

    if not runs_dir.exists():
        console.print("[yellow]No test runs found[/yellow]")
        return

    console.print("[cyan]Available Test Runs:[/cyan]\n")

    table = Table()
    table.add_column("Run ID")
    table.add_column("Phase")
    table.add_column("Samples")
    table.add_column("Status")
    table.add_column("Avg Quality")

    for run_dir in sorted(runs_dir.iterdir()):
        if run_dir.is_dir():
            run_id = run_dir.name

            # Check for config
            config_path = run_dir / "config.json"
            phase = "unknown"
            samples = "-"

            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    phase = config.get("phase", "unknown")
                    samples = str(config.get("size", "-"))

            # Check for aggregate
            agg_path = run_dir / "aggregate.json"
            status = "[yellow]sampled[/yellow]"
            avg_quality = "-"

            if agg_path.exists():
                with open(agg_path, 'r') as f:
                    agg = json.load(f)
                    avg_quality = f"{agg.get('avg_quality_score', 0):.4f}"
                    status = "[green]completed[/green]"

            # Check for validation
            validation_path = run_dir / "validation.json"
            if validation_path.exists():
                status = "[blue]validated[/blue]"

            table.add_row(run_id, phase, samples, status, avg_quality)

    console.print(table)


def main():
    """Main entry point"""
    cli(obj={})


if __name__ == "__main__":
    main()
