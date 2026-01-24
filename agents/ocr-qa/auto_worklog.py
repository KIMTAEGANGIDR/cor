#!/usr/bin/env python3
"""
OCR-QA Agent 자동 워크로그 갱신 스크립트

1분마다 파이프라인 상태를 확인하고 워크로그를 업데이트합니다.

사용법:
    python auto_worklog.py              # 포그라운드 실행
    python auto_worklog.py --daemon     # 백그라운드 실행
    python auto_worklog.py --once       # 한 번만 실행
"""

import os
import sys
import time
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# 경로 설정
AGENT_DIR = Path(__file__).parent
PROJECT_ROOT = AGENT_DIR.parent.parent
WORKLOG_PATH = AGENT_DIR / "WORKLOG.md"
DB_PATH = PROJECT_ROOT / "peraturan" / "data" / "ocr_pipeline.db"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"

# 갱신 주기 (초)
UPDATE_INTERVAL = 60


@dataclass
class PipelineStatus:
    """파이프라인 상태"""
    timestamp: str = ""

    # DB 상태
    documents_count: int = 0
    headers_count: int = 0
    clusters_count: int = 0
    ocr_results_count: int = 0

    # GPU 상태
    gpu_available: bool = False
    gpu_memory_used: str = ""
    gpu_memory_total: str = ""
    gpu_utilization: str = ""

    # 디스크 상태
    disk_used: str = ""
    disk_total: str = ""
    disk_percent: str = ""

    # 프로세스 상태
    running_processes: list = field(default_factory=list)

    # 에러
    errors: list = field(default_factory=list)


def get_db_status() -> dict:
    """DB 상태 조회"""
    result = {
        "documents": 0,
        "headers": 0,
        "clusters": 0,
        "ocr_results": 0,
        "pattern_rules": 0,
    }

    if not DB_PATH.exists():
        return result

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
        cur = conn.cursor()

        tables = ["documents", "headers", "clusters", "ocr_results", "pattern_rules"]
        for table in tables:
            try:
                count = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                result[table] = count
            except:
                pass

        conn.close()
    except Exception as e:
        result["error"] = str(e)

    return result


def get_gpu_status() -> dict:
    """GPU 상태 조회 (nvidia-smi)"""
    result = {
        "available": False,
        "memory_used": "",
        "memory_total": "",
        "utilization": "",
    }

    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            text=True,
            timeout=5,
        ).strip()

        parts = output.split(", ")
        if len(parts) >= 3:
            result["available"] = True
            result["memory_used"] = f"{parts[0]} MiB"
            result["memory_total"] = f"{parts[1]} MiB"
            result["utilization"] = f"{parts[2]}%"
    except:
        pass

    return result


def get_disk_status() -> dict:
    """디스크 상태 조회"""
    result = {
        "used": "",
        "total": "",
        "percent": "",
    }

    try:
        output = subprocess.check_output(
            ["df", "-h", str(PROJECT_ROOT)],
            text=True,
            timeout=5,
        )
        lines = output.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5:
                result["total"] = parts[1]
                result["used"] = parts[2]
                result["percent"] = parts[4]
    except:
        pass

    return result


def get_running_processes() -> list:
    """OCR 관련 실행 중 프로세스"""
    processes = []

    try:
        output = subprocess.check_output(
            ["pgrep", "-af", "python.*ocr|surya"],
            text=True,
            timeout=5,
        )
        for line in output.strip().split("\n"):
            if line:
                processes.append(line[:80])
    except:
        pass

    return processes


def collect_status() -> PipelineStatus:
    """전체 상태 수집"""
    status = PipelineStatus()
    status.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S KST")

    # DB 상태
    db = get_db_status()
    status.documents_count = db.get("documents", 0)
    status.headers_count = db.get("headers", 0)
    status.clusters_count = db.get("clusters", 0)
    status.ocr_results_count = db.get("ocr_results", 0)
    if "error" in db:
        status.errors.append(f"DB 오류: {db['error']}")

    # GPU 상태
    gpu = get_gpu_status()
    status.gpu_available = gpu["available"]
    status.gpu_memory_used = gpu["memory_used"]
    status.gpu_memory_total = gpu["memory_total"]
    status.gpu_utilization = gpu["utilization"]

    # 디스크 상태
    disk = get_disk_status()
    status.disk_used = disk["used"]
    status.disk_total = disk["total"]
    status.disk_percent = disk["percent"]

    # 프로세스 상태
    status.running_processes = get_running_processes()

    return status


def generate_status_section(status: PipelineStatus) -> str:
    """상태 섹션 마크다운 생성"""
    gpu_status = "✅ 사용 가능" if status.gpu_available else "❌ 사용 불가"

    return f"""## 실시간 상태 (자동 갱신: {status.timestamp})

### DB 상태
| 테이블 | 레코드 |
|--------|--------|
| documents | {status.documents_count:,} |
| headers | {status.headers_count:,} |
| clusters | {status.clusters_count:,} |
| ocr_results | {status.ocr_results_count:,} |

### GPU
- 상태: {gpu_status}
- 메모리: {status.gpu_memory_used} / {status.gpu_memory_total}
- 사용률: {status.gpu_utilization}

### 디스크
- 사용량: {status.disk_used} / {status.disk_total} ({status.disk_percent})

### 실행 중 프로세스
{chr(10).join(f'- `{p}`' for p in status.running_processes) if status.running_processes else '- (없음)'}

"""


def update_worklog(status: PipelineStatus):
    """워크로그 파일 업데이트"""
    if not WORKLOG_PATH.exists():
        print(f"워크로그 파일 없음: {WORKLOG_PATH}")
        return

    content = WORKLOG_PATH.read_text(encoding="utf-8")

    # "## 실시간 상태" 섹션 찾아서 교체
    marker_start = "## 실시간 상태"
    marker_end = "\n## "  # 다음 섹션 시작

    new_section = generate_status_section(status)

    if marker_start in content:
        # 기존 섹션 교체
        start_idx = content.find(marker_start)
        end_idx = content.find(marker_end, start_idx + len(marker_start))

        if end_idx == -1:
            # 마지막 섹션인 경우
            content = content[:start_idx] + new_section
        else:
            content = content[:start_idx] + new_section + content[end_idx:]
    else:
        # 새 섹션 추가 (첫 번째 "---" 다음에)
        divider = "\n---\n"
        idx = content.find(divider)
        if idx != -1:
            insert_point = idx + len(divider)
            content = content[:insert_point] + "\n" + new_section + content[insert_point:]
        else:
            content += "\n\n" + new_section

    WORKLOG_PATH.write_text(content, encoding="utf-8")
    print(f"[{status.timestamp}] 워크로그 업데이트됨")


def run_once():
    """한 번 실행"""
    status = collect_status()
    update_worklog(status)

    # 상태 출력
    print(f"\n{'=' * 50}")
    print(f"OCR-QA 상태 ({status.timestamp})")
    print(f"{'=' * 50}")
    print(f"DB: documents={status.documents_count}, headers={status.headers_count}")
    print(f"GPU: {status.gpu_memory_used}/{status.gpu_memory_total} ({status.gpu_utilization})")
    print(f"디스크: {status.disk_used}/{status.disk_total} ({status.disk_percent})")
    if status.errors:
        print(f"에러: {status.errors}")


def run_daemon():
    """데몬 모드 (백그라운드)"""
    print(f"OCR-QA 워크로그 자동 갱신 시작 (주기: {UPDATE_INTERVAL}초)")
    print(f"워크로그: {WORKLOG_PATH}")
    print("Ctrl+C로 종료")

    try:
        while True:
            try:
                status = collect_status()
                update_worklog(status)
            except Exception as e:
                print(f"오류: {e}")

            time.sleep(UPDATE_INTERVAL)
    except KeyboardInterrupt:
        print("\n종료됨")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="OCR-QA 자동 워크로그")
    parser.add_argument("--daemon", action="store_true", help="백그라운드 실행")
    parser.add_argument("--once", action="store_true", help="한 번만 실행")
    args = parser.parse_args()

    if args.once:
        run_once()
    elif args.daemon:
        run_daemon()
    else:
        # 기본: 데몬 모드
        run_daemon()


if __name__ == "__main__":
    main()
