#!/usr/bin/env python3
"""
OCR 결과 비교 도구

두 OCR 파이프라인 결과를 비교하여 개선 여부를 측정합니다.

사용법:
    python scripts/compare_ocr_results.py /tmp/baseline_test /tmp/improved_output

출력:
    - 품질 점수 변화
    - PASS/WARNING/FAIL 비율 변화
    - 교정된 단어 수 변화
    - 연도별 분석
"""

import json
import argparse
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict


@dataclass
class DocumentResult:
    """개별 문서 결과"""
    filename: str
    quality_score: float = 0.0
    grade: str = ""
    year: Optional[int] = None
    corrections_made: int = 0
    total_words: int = 0
    valid_words: int = 0
    legal_terms_found: int = 0


@dataclass
class ComparisonStats:
    """비교 통계"""
    total_documents: int = 0
    pass_count: int = 0
    warning_count: int = 0
    fail_count: int = 0
    avg_score: float = 0.0

    # 연도별 통계
    by_era: dict = field(default_factory=dict)


def load_results(output_dir: Path) -> dict[str, DocumentResult]:
    """
    출력 디렉토리에서 결과 로드

    디렉토리 구조:
        output_dir/
        ├── approved/
        │   ├── doc1.txt
        │   └── doc1.json
        ├── llm_queue/
        └── human_queue/
    """
    results = {}

    # approved, llm_queue, human_queue 디렉토리 탐색
    for subdir in ["approved", "llm_queue", "human_queue"]:
        subdir_path = output_dir / subdir
        if not subdir_path.exists():
            continue

        for json_file in subdir_path.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                filename = json_file.stem
                result = DocumentResult(
                    filename=filename,
                    quality_score=data.get("quality_score", 0.0),
                    grade=data.get("grade", "unknown"),
                    year=extract_year(filename, data),
                    corrections_made=data.get("corrections_made", 0),
                    total_words=data.get("total_words", 0),
                    valid_words=data.get("valid_words", 0),
                    legal_terms_found=data.get("legal_terms_found", 0),
                )
                results[filename] = result
            except (json.JSONDecodeError, KeyError) as e:
                print(f"경고: {json_file} 파싱 실패 - {e}", file=sys.stderr)

    return results


def extract_year(filename: str, data: dict) -> Optional[int]:
    """파일명이나 데이터에서 연도 추출"""
    # 데이터에서 연도 확인
    if "year" in data:
        return data["year"]

    # 파일명에서 연도 추출 시도 (예: UU_1_2024, PP_10_1953)
    import re
    patterns = [
        r"_(\d{4})$",        # 끝에 연도
        r"_(\d{4})[_\.]",    # 중간에 연도
        r"TAHUN[_\s]?(\d{4})", # TAHUN 1953
    ]
    for pattern in patterns:
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
    return None


def categorize_era(year: Optional[int]) -> str:
    """연도를 시대로 분류"""
    if year is None:
        return "unknown"
    elif year < 1970:
        return "1950-1969"
    elif year < 1990:
        return "1970-1989"
    elif year < 2010:
        return "1990-2009"
    else:
        return "2010-present"


def calculate_stats(results: dict[str, DocumentResult]) -> ComparisonStats:
    """결과에서 통계 계산"""
    if not results:
        return ComparisonStats()

    stats = ComparisonStats()
    stats.total_documents = len(results)

    scores = []
    by_era = defaultdict(lambda: {"count": 0, "pass": 0, "warning": 0, "fail": 0, "scores": []})

    for doc in results.values():
        scores.append(doc.quality_score)

        if doc.grade == "pass":
            stats.pass_count += 1
        elif doc.grade == "warning":
            stats.warning_count += 1
        elif doc.grade == "fail":
            stats.fail_count += 1

        # 연도별 통계
        era = categorize_era(doc.year)
        by_era[era]["count"] += 1
        by_era[era]["scores"].append(doc.quality_score)
        if doc.grade == "pass":
            by_era[era]["pass"] += 1
        elif doc.grade == "warning":
            by_era[era]["warning"] += 1
        else:
            by_era[era]["fail"] += 1

    stats.avg_score = sum(scores) / len(scores) if scores else 0.0

    # 연도별 평균 계산
    for era, era_data in by_era.items():
        era_data["avg_score"] = (
            sum(era_data["scores"]) / len(era_data["scores"])
            if era_data["scores"] else 0.0
        )
        era_data["pass_rate"] = (
            era_data["pass"] / era_data["count"] * 100
            if era_data["count"] else 0.0
        )
        del era_data["scores"]  # 원시 데이터 제거

    stats.by_era = dict(by_era)
    return stats


def compare_results(
    baseline_results: dict[str, DocumentResult],
    improved_results: dict[str, DocumentResult],
) -> dict:
    """두 결과 세트 비교"""
    baseline_stats = calculate_stats(baseline_results)
    improved_stats = calculate_stats(improved_results)

    # 공통 문서만 비교
    common_docs = set(baseline_results.keys()) & set(improved_results.keys())
    only_baseline = set(baseline_results.keys()) - set(improved_results.keys())
    only_improved = set(improved_results.keys()) - set(baseline_results.keys())

    # 문서별 비교
    improvements = []
    regressions = []
    unchanged = []

    for filename in common_docs:
        baseline = baseline_results[filename]
        improved = improved_results[filename]

        diff = improved.quality_score - baseline.quality_score
        if diff > 0.5:  # 0.5% 이상 개선
            improvements.append({
                "filename": filename,
                "baseline_score": baseline.quality_score,
                "improved_score": improved.quality_score,
                "diff": diff,
                "baseline_grade": baseline.grade,
                "improved_grade": improved.grade,
            })
        elif diff < -0.5:  # 0.5% 이상 하락
            regressions.append({
                "filename": filename,
                "baseline_score": baseline.quality_score,
                "improved_score": improved.quality_score,
                "diff": diff,
                "baseline_grade": baseline.grade,
                "improved_grade": improved.grade,
            })
        else:
            unchanged.append(filename)

    return {
        "baseline_stats": baseline_stats,
        "improved_stats": improved_stats,
        "common_documents": len(common_docs),
        "only_in_baseline": len(only_baseline),
        "only_in_improved": len(only_improved),
        "improvements": sorted(improvements, key=lambda x: -x["diff"]),
        "regressions": sorted(regressions, key=lambda x: x["diff"]),
        "unchanged_count": len(unchanged),
    }


def print_comparison_report(comparison: dict):
    """비교 리포트 출력"""
    baseline = comparison["baseline_stats"]
    improved = comparison["improved_stats"]

    print("=" * 70)
    print("OCR 파이프라인 결과 비교")
    print("=" * 70)

    # 전체 통계
    print("\n[전체 통계]")
    print(f"{'지표':<25} {'Baseline':>15} {'Improved':>15} {'변화':>15}")
    print("-" * 70)

    score_diff = improved.avg_score - baseline.avg_score
    print(f"{'평균 점수':<25} {baseline.avg_score:>14.1f}% {improved.avg_score:>14.1f}% {score_diff:>+14.1f}%")

    pass_rate_b = baseline.pass_count / baseline.total_documents * 100 if baseline.total_documents else 0
    pass_rate_i = improved.pass_count / improved.total_documents * 100 if improved.total_documents else 0
    print(f"{'PASS율':<25} {pass_rate_b:>14.1f}% {pass_rate_i:>14.1f}% {pass_rate_i - pass_rate_b:>+14.1f}%")

    warning_rate_b = baseline.warning_count / baseline.total_documents * 100 if baseline.total_documents else 0
    warning_rate_i = improved.warning_count / improved.total_documents * 100 if improved.total_documents else 0
    print(f"{'WARNING율':<25} {warning_rate_b:>14.1f}% {warning_rate_i:>14.1f}% {warning_rate_i - warning_rate_b:>+14.1f}%")

    fail_rate_b = baseline.fail_count / baseline.total_documents * 100 if baseline.total_documents else 0
    fail_rate_i = improved.fail_count / improved.total_documents * 100 if improved.total_documents else 0
    print(f"{'FAIL율':<25} {fail_rate_b:>14.1f}% {fail_rate_i:>14.1f}% {fail_rate_i - fail_rate_b:>+14.1f}%")

    # 연도별 통계
    print("\n[연도별 통계]")
    eras = ["1950-1969", "1970-1989", "1990-2009", "2010-present", "unknown"]
    print(f"{'시대':<15} {'Baseline 점수':>15} {'Improved 점수':>15} {'Baseline PASS율':>18} {'Improved PASS율':>18}")
    print("-" * 85)

    for era in eras:
        b_era = baseline.by_era.get(era, {"avg_score": 0, "pass_rate": 0, "count": 0})
        i_era = improved.by_era.get(era, {"avg_score": 0, "pass_rate": 0, "count": 0})

        if b_era["count"] > 0 or i_era["count"] > 0:
            print(f"{era:<15} {b_era['avg_score']:>14.1f}% {i_era['avg_score']:>14.1f}% {b_era['pass_rate']:>17.1f}% {i_era['pass_rate']:>17.1f}%")

    # 개선된 문서
    print("\n[개선된 문서 상위 10개]")
    if comparison["improvements"]:
        print(f"{'파일명':<40} {'Before':>10} {'After':>10} {'변화':>10}")
        print("-" * 70)
        for item in comparison["improvements"][:10]:
            print(f"{item['filename'][:38]:<40} {item['baseline_score']:>9.1f}% {item['improved_score']:>9.1f}% {item['diff']:>+9.1f}%")
    else:
        print("  (없음)")

    # 회귀된 문서
    print("\n[회귀된 문서 (주의)]")
    if comparison["regressions"]:
        print(f"{'파일명':<40} {'Before':>10} {'After':>10} {'변화':>10}")
        print("-" * 70)
        for item in comparison["regressions"][:10]:
            print(f"{item['filename'][:38]:<40} {item['baseline_score']:>9.1f}% {item['improved_score']:>9.1f}% {item['diff']:>+9.1f}%")
    else:
        print("  (없음) - 회귀 없음!")

    # 요약
    print("\n[요약]")
    print(f"  총 문서: {comparison['common_documents']}")
    print(f"  개선: {len(comparison['improvements'])}개")
    print(f"  회귀: {len(comparison['regressions'])}개")
    print(f"  변화 없음: {comparison['unchanged_count']}개")

    if comparison["only_in_baseline"]:
        print(f"  Baseline에만 존재: {comparison['only_in_baseline']}개")
    if comparison["only_in_improved"]:
        print(f"  Improved에만 존재: {comparison['only_in_improved']}개")

    print("=" * 70)

    # 개선 목표 달성 여부
    print("\n[개선 목표 달성 여부]")
    improvements = comparison["improvements"]
    regressions = comparison["regressions"]

    # 1950-70년대 PASS율 향상 (목표: +10%)
    b_old = baseline.by_era.get("1950-1969", {})
    i_old = improved.by_era.get("1950-1969", {})
    if b_old.get("count", 0) > 0 and i_old.get("count", 0) > 0:
        old_pass_diff = i_old["pass_rate"] - b_old["pass_rate"]
        status = "✅" if old_pass_diff >= 10 else "❌"
        print(f"  {status} 1950-70년대 PASS율 +10%: {old_pass_diff:+.1f}%")

    # 전체 자동 승인율 향상 (목표: +5%)
    total_pass_diff = pass_rate_i - pass_rate_b
    status = "✅" if total_pass_diff >= 5 else "❌"
    print(f"  {status} 전체 자동 승인율 +5%: {total_pass_diff:+.1f}%")

    # 인간 검토 감소 (목표: -20%)
    if fail_rate_b > 0:
        fail_reduction = (fail_rate_b - fail_rate_i) / fail_rate_b * 100
        status = "✅" if fail_reduction >= 20 else "❌"
        print(f"  {status} 인간 검토 -20%: {fail_reduction:+.1f}%")

    # 회귀 없음
    status = "✅" if len(regressions) == 0 else "⚠️"
    print(f"  {status} 회귀 없음: {len(regressions)}건")


def export_json(comparison: dict, output_path: Path):
    """비교 결과를 JSON으로 내보내기"""
    # dataclass를 dict로 변환
    def to_dict(obj):
        if hasattr(obj, "__dict__"):
            return {k: to_dict(v) for k, v in obj.__dict__.items()}
        elif isinstance(obj, dict):
            return {k: to_dict(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [to_dict(v) for v in obj]
        else:
            return obj

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(to_dict(comparison), f, ensure_ascii=False, indent=2)

    print(f"\n결과가 {output_path}에 저장되었습니다.")


def main():
    parser = argparse.ArgumentParser(
        description="OCR 파이프라인 결과 비교 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
    python scripts/compare_ocr_results.py /tmp/baseline_test /tmp/improved_output
    python scripts/compare_ocr_results.py baseline/ improved/ --output comparison.json
        """,
    )
    parser.add_argument(
        "baseline_dir",
        type=Path,
        help="Baseline 결과 디렉토리",
    )
    parser.add_argument(
        "improved_dir",
        type=Path,
        help="Improved 결과 디렉토리",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="JSON 출력 파일 (선택)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="상세 출력",
    )

    args = parser.parse_args()

    # 디렉토리 확인
    if not args.baseline_dir.exists():
        print(f"오류: Baseline 디렉토리가 존재하지 않습니다: {args.baseline_dir}", file=sys.stderr)
        sys.exit(1)
    if not args.improved_dir.exists():
        print(f"오류: Improved 디렉토리가 존재하지 않습니다: {args.improved_dir}", file=sys.stderr)
        sys.exit(1)

    # 결과 로드
    print(f"Baseline 로드 중: {args.baseline_dir}")
    baseline_results = load_results(args.baseline_dir)
    print(f"  → {len(baseline_results)}개 문서 로드")

    print(f"Improved 로드 중: {args.improved_dir}")
    improved_results = load_results(args.improved_dir)
    print(f"  → {len(improved_results)}개 문서 로드")

    if not baseline_results:
        print("오류: Baseline 결과가 없습니다.", file=sys.stderr)
        sys.exit(1)
    if not improved_results:
        print("오류: Improved 결과가 없습니다.", file=sys.stderr)
        sys.exit(1)

    # 비교
    comparison = compare_results(baseline_results, improved_results)

    # 리포트 출력
    print_comparison_report(comparison)

    # JSON 출력
    if args.output:
        export_json(comparison, args.output)


if __name__ == "__main__":
    main()
