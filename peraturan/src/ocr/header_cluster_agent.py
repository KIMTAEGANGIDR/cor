"""
Header Cluster Agent

헤더 이미지 클러스터링 에이전트
- 시각적 패턴 분류
- 가설 검증 및 업데이트
- 워크로그 자동 기록

사용법:
    python -m peraturan.src.ocr.header_cluster_agent run --clusters 200
    python -m peraturan.src.ocr.header_cluster_agent status
    python -m peraturan.src.ocr.header_cluster_agent worklog
"""

import json
import time
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional
import threading

from .header_visual_clusterer import HeaderVisualClusterer, run_visual_clustering
from .header_extractor import HeaderExtractor
from .database import OCRPipelineDB

# 경로 설정
AGENT_DIR = Path(__file__).parent
PLAN_FILE = AGENT_DIR / "HEADER_CLUSTER_PLAN.md"
WORKLOG_FILE = AGENT_DIR / "HEADER_CLUSTER_WORKLOG.md"


@dataclass
class AgentState:
    """에이전트 상태"""
    status: str  # idle, extracting, clustering, analyzing, complete
    current_task: str
    progress_pct: float
    documents_processed: int
    documents_total: int
    clusters_created: int
    last_update: str
    errors: list


@dataclass
class WorklogEntry:
    """워크로그 항목"""
    timestamp: str
    action: str
    details: str
    duration_sec: float = 0
    status: str = "info"  # info, success, warning, error


class HeaderClusterAgent:
    """헤더 클러스터 에이전트"""

    def __init__(self):
        self.db = OCRPipelineDB()
        self.extractor = HeaderExtractor()
        self.clusterer = HeaderVisualClusterer()
        self.state = AgentState(
            status="idle",
            current_task="",
            progress_pct=0,
            documents_processed=0,
            documents_total=0,
            clusters_created=0,
            last_update=datetime.now().isoformat(),
            errors=[]
        )
        self._worklog_entries = []
        self._stop_logging = False
        self._log_thread = None

    def log(self, action: str, details: str, status: str = "info", duration_sec: float = 0):
        """워크로그에 기록"""
        entry = WorklogEntry(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            action=action,
            details=details,
            duration_sec=duration_sec,
            status=status
        )
        self._worklog_entries.append(entry)
        self._append_to_worklog_file(entry)
        print(f"[{entry.timestamp}] [{status.upper()}] {action}: {details}")

    def _append_to_worklog_file(self, entry: WorklogEntry):
        """워크로그 파일에 추가"""
        status_emoji = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌"
        }

        line = f"| {entry.timestamp} | {status_emoji.get(entry.status, '•')} | {entry.action} | {entry.details} |"

        if entry.duration_sec > 0:
            line += f" {entry.duration_sec:.1f}s |"
        else:
            line += " - |"

        # 파일이 없으면 헤더 추가
        if not WORKLOG_FILE.exists():
            header = """# Header Cluster Agent - 워크로그

## 작업 기록

| 시간 | 상태 | 작업 | 상세 | 소요시간 |
|------|------|------|------|----------|
"""
            with open(WORKLOG_FILE, "w", encoding="utf-8") as f:
                f.write(header)

        with open(WORKLOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _start_periodic_logging(self, interval_sec: int = 60):
        """주기적 상태 로깅 시작"""
        def log_status():
            while not self._stop_logging:
                self._log_current_status()
                time.sleep(interval_sec)

        self._stop_logging = False
        self._log_thread = threading.Thread(target=log_status, daemon=True)
        self._log_thread.start()

    def _stop_periodic_logging(self):
        """주기적 상태 로깅 중지"""
        self._stop_logging = True
        if self._log_thread:
            self._log_thread.join(timeout=5)

    def _log_current_status(self):
        """현재 상태 로깅"""
        with self.db.connection() as conn:
            pending = conn.execute("SELECT COUNT(*) FROM documents WHERE stage = 'pending'").fetchone()[0]
            extracted = conn.execute("SELECT COUNT(*) FROM documents WHERE stage = 'header_extracted'").fetchone()[0]
            clustered = conn.execute("SELECT COUNT(*) FROM headers WHERE cluster_id IS NOT NULL").fetchone()[0]

        total = pending + extracted
        pct = extracted / total * 100 if total > 0 else 0

        self.state.documents_processed = extracted
        self.state.documents_total = total
        self.state.progress_pct = pct
        self.state.last_update = datetime.now().isoformat()

        details = f"추출: {extracted:,}/{total:,} ({pct:.1f}%), 클러스터링: {clustered:,}"
        self.log("상태 업데이트", details)

    def get_extraction_status(self) -> dict:
        """헤더 추출 상태 조회"""
        with self.db.connection() as conn:
            pending = conn.execute("SELECT COUNT(*) FROM documents WHERE stage = 'pending'").fetchone()[0]
            extracted = conn.execute("SELECT COUNT(*) FROM documents WHERE stage = 'header_extracted'").fetchone()[0]
            headers = conn.execute("SELECT COUNT(*) FROM headers").fetchone()[0]
            clustered = conn.execute("SELECT COUNT(*) FROM headers WHERE cluster_id IS NOT NULL").fetchone()[0]

        total = pending + extracted
        return {
            "pending": pending,
            "extracted": extracted,
            "total": total,
            "headers": headers,
            "clustered": clustered,
            "progress_pct": extracted / total * 100 if total > 0 else 0
        }

    def run_full_pipeline(
        self,
        n_clusters: int = 200,
        workers: int = 8,
        log_interval: int = 60
    ):
        """
        전체 파이프라인 실행

        1. 헤더 추출 대기/모니터링
        2. 시각적 클러스터링
        3. 상관관계 분석
        4. 가설 검증 및 계획 업데이트
        5. 대표 샘플 추출
        """
        self.log("파이프라인 시작", f"클러스터 수: {n_clusters}, 워커: {workers}")
        self.state.status = "running"

        # 주기적 로깅 시작
        self._start_periodic_logging(log_interval)

        try:
            # Phase 1: 헤더 추출 상태 확인
            self.state.current_task = "헤더 추출 확인"
            status = self.get_extraction_status()
            self.log("Phase 1", f"헤더 추출 상태 확인 - {status['extracted']:,}/{status['total']:,}")

            if status['pending'] > 0:
                self.log("대기", f"헤더 추출 진행 중 ({status['pending']:,}개 대기)")

                # 추출 완료 대기
                while status['pending'] > 0:
                    time.sleep(120)  # 2분마다 확인
                    status = self.get_extraction_status()

                self.log("Phase 1 완료", f"헤더 추출 완료 - {status['extracted']:,}개", "success")

            # Phase 2: 시각적 클러스터링
            self.state.current_task = "시각적 클러스터링"
            self.log("Phase 2", f"시각적 클러스터링 시작 ({n_clusters}개 클러스터)")

            start_time = time.time()
            result = run_visual_clustering(n_clusters=n_clusters, workers=workers)
            duration = time.time() - start_time

            if "error" in result:
                self.log("Phase 2 실패", result["error"], "error")
                raise Exception(result["error"])

            self.state.clusters_created = result["n_clusters"]
            self.log("Phase 2 완료", f"{result['n_clusters']}개 클러스터 생성", "success", duration)

            # Phase 3: 상관관계 분석
            self.state.current_task = "상관관계 분석"
            self.log("Phase 3", "상관관계 분석 시작")

            start_time = time.time()
            analysis = self.clusterer.analyze_cluster_correlations()
            duration = time.time() - start_time

            if "error" in analysis:
                self.log("Phase 3 실패", analysis["error"], "error")
            else:
                jenis_corr = analysis["correlation_summary"]["jenis_correlation"]
                tahun_corr = analysis["correlation_summary"]["tahun_correlation"]
                self.log("Phase 3 완료",
                    f"Jenis 상관: {jenis_corr['percentage']}%, Tahun 상관: {tahun_corr['percentage']}%",
                    "success", duration)

                # Phase 4: 가설 검증 및 계획 업데이트
                self._update_plan_with_results(analysis)

            # Phase 5: 대표 샘플 추출
            self.state.current_task = "대표 샘플 추출"
            self.log("Phase 5", "대표 샘플 추출 시작")

            output_dir = AGENT_DIR.parent.parent / "data" / "cluster_samples"
            sample_result = self.clusterer.export_representative_samples(output_dir)

            if "error" not in sample_result:
                self.log("Phase 5 완료",
                    f"{sample_result['exported_samples']}개 샘플 추출 → {output_dir}",
                    "success")

            # Phase 6: 보고서 생성
            self.state.current_task = "보고서 생성"
            report_path = output_dir / "analysis_report.md"
            self.clusterer.generate_analysis_report(report_path)
            self.log("보고서 생성", str(report_path), "success")

            # 완료
            self.state.status = "complete"
            self.state.current_task = "완료"
            self.log("파이프라인 완료", "모든 단계 완료", "success")

        except Exception as e:
            self.state.status = "error"
            self.state.errors.append(str(e))
            self.log("오류 발생", str(e), "error")
            raise

        finally:
            self._stop_periodic_logging()

    def _update_plan_with_results(self, analysis: dict):
        """분석 결과로 계획 파일 업데이트"""
        if not PLAN_FILE.exists():
            return

        jenis_corr = analysis["correlation_summary"]["jenis_correlation"]
        tahun_corr = analysis["correlation_summary"]["tahun_correlation"]

        # 가설 검증 결과
        h1_status = "지지됨" if jenis_corr["percentage"] >= 80 else "부분 지지" if jenis_corr["percentage"] >= 50 else "기각됨"
        h2_status = "지지됨" if tahun_corr["percentage"] >= 70 else "부분 지지" if tahun_corr["percentage"] >= 50 else "기각됨"

        content = PLAN_FILE.read_text(encoding="utf-8")

        # H1 상태 업데이트
        content = content.replace(
            "- [ ] 미검증\n- [ ] 지지됨\n- [ ] 기각됨\n- [ ] 부분 지지\n\n**검증 결과**: (자동 업데이트)",
            f"- [x] {h1_status}\n\n**검증 결과**: Jenis 순수도 {jenis_corr['percentage']}% ({jenis_corr['high_purity_clusters']}개 클러스터)",
            1
        )

        # H2 상태 업데이트 (두 번째 occurrence)
        parts = content.split("### H2:", 1)
        if len(parts) == 2:
            h2_section = parts[1]
            h2_section = h2_section.replace(
                "- [ ] 미검증\n- [ ] 지지됨\n- [ ] 기각됨\n- [ ] 부분 지지\n\n**검증 결과**: (자동 업데이트)",
                f"- [x] {h2_status}\n\n**검증 결과**: Tahun 5년 이내 {tahun_corr['percentage']}% ({tahun_corr['high_purity_clusters']}개 클러스터)",
                1
            )
            content = parts[0] + "### H2:" + h2_section

        # 성공 지표 업데이트
        content = content.replace(
            "| 법령 유형 순수도 | ≥ 80% | - |",
            f"| 법령 유형 순수도 | ≥ 80% | {jenis_corr['percentage']}% |"
        )
        content = content.replace(
            "| 연도 5년 이내 비율 | ≥ 70% | - |",
            f"| 연도 5년 이내 비율 | ≥ 70% | {tahun_corr['percentage']}% |"
        )
        content = content.replace(
            "| 클러스터당 평균 문서 | 100-300 | - |",
            f"| 클러스터당 평균 문서 | 100-300 | {analysis['total_documents'] // analysis['total_clusters']} |"
        )

        # 마지막 업데이트 시간
        content = content.replace(
            "**마지막 업데이트**: (자동 갱신)",
            f"**마지막 업데이트**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # 변경 이력 추가
        today = datetime.now().strftime("%Y-%m-%d")
        history_line = f"| {today} | 가설 검증 완료 | 분석 결과 반영 |"
        content = content.replace(
            "| 2026-01-24 | 초기 계획 수립 | 프로젝트 시작 |",
            f"| 2026-01-24 | 초기 계획 수립 | 프로젝트 시작 |\n{history_line}"
        )

        PLAN_FILE.write_text(content, encoding="utf-8")
        self.log("계획 업데이트", f"가설 검증 결과 반영 (H1: {h1_status}, H2: {h2_status})")

    def print_status(self):
        """현재 상태 출력"""
        status = self.get_extraction_status()

        print(f"\n{'='*60}")
        print("Header Cluster Agent - 상태")
        print(f"{'='*60}")
        print(f"에이전트 상태: {self.state.status}")
        print(f"현재 작업: {self.state.current_task or '-'}")
        print()
        print("[헤더 추출]")
        print(f"  진행률: {status['progress_pct']:.1f}%")
        print(f"  완료: {status['extracted']:,} / 전체: {status['total']:,}")
        print(f"  대기: {status['pending']:,}")
        print()
        print("[클러스터링]")
        print(f"  클러스터 수: {self.state.clusters_created or status.get('clustered', 0) or '-'}")
        print(f"  클러스터링된 문서: {status['clustered']:,}")
        print()
        print(f"마지막 업데이트: {self.state.last_update}")

    def print_worklog(self, limit: int = 20):
        """워크로그 출력"""
        if not WORKLOG_FILE.exists():
            print("워크로그가 없습니다.")
            return

        content = WORKLOG_FILE.read_text(encoding="utf-8")
        lines = content.strip().split("\n")

        # 마지막 N개 항목 출력
        header_lines = lines[:5]  # 헤더 (제목 + 테이블 헤더)
        data_lines = lines[5:]

        print("\n".join(header_lines))
        print("\n".join(data_lines[-limit:]))


# CLI
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Header Cluster Agent")
    parser.add_argument("command", choices=["run", "status", "worklog", "monitor"],
                       help="명령어")
    parser.add_argument("--clusters", "-c", type=int, default=200,
                       help="클러스터 수")
    parser.add_argument("--workers", "-w", type=int, default=8,
                       help="병렬 워커 수")
    parser.add_argument("--interval", "-i", type=int, default=60,
                       help="로깅 간격 (초)")
    parser.add_argument("--limit", "-l", type=int, default=20,
                       help="워크로그 표시 개수")

    args = parser.parse_args()
    agent = HeaderClusterAgent()

    if args.command == "run":
        agent.run_full_pipeline(
            n_clusters=args.clusters,
            workers=args.workers,
            log_interval=args.interval
        )

    elif args.command == "status":
        agent.print_status()

    elif args.command == "worklog":
        agent.print_worklog(limit=args.limit)

    elif args.command == "monitor":
        # 실시간 모니터링
        print("실시간 모니터링 시작 (Ctrl+C로 종료)")
        try:
            while True:
                agent.print_status()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n모니터링 종료")
