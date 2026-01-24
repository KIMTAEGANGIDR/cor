#!/usr/bin/env python3
"""
에이전트 작업 로그 자동 기록 시스템
- 1분마다 현재 상태를 상세히 기록
- KOICA 정합성 체크 진행률 추적
- 실행 중인 프로세스 모니터링
"""

import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

# 설정
WORKLOG_PATH = Path("/home/tylor/cor/logs/agent_worklog.jsonl")
WORKLOG_MD_PATH = Path("/home/tylor/cor/logs/agent_worklog.md")
DB_PATH = Path("/home/tylor/cor/peraturan/data/db/peraturan.db")
LOG_DIR = Path("/home/tylor/cor/logs")

# KOICA 기준
KOICA_TARGETS = {
    'UUD': 1,
    'TAP MPR': 41,
    'UU': 1902,
    'UUDRT': 177,
    'UUDS': 1,
    'PERPPU': 217,
    'PP': 4939,
    'PERPRES': 2580,
    'PENPRES': 76,
    'PERMEN': 18880,
    'PERBAN': 6242,
    'TERJEMAH': 368,
}

JENIS_TO_CODE = {
    'UNDANG-UNDANG': 'UU',
    'PERATURAN PEMERINTAH': 'PP',
    'PERATURAN PRESIDEN': 'PERPRES',
    'PERATURAN MENTERI': 'PERMEN',
    'PERATURAN BADAN/LEMBAGA': 'PERBAN',
    'PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG': 'PERPPU',
    'KETETAPAN MAJELIS PERMUSYAWARATAN RAKYAT': 'TAP MPR',
    'PENETAPAN PRESIDEN': 'PENPRES',
}


def get_db_stats() -> dict:
    """DB 통계 조회"""
    if not DB_PATH.exists():
        return {"error": "DB not found"}

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    stats = {"jenis_counts": {}, "total": 0, "koica_status": {}}

    try:
        # jenis별 통계
        cur.execute("SELECT jenis, COUNT(*) FROM peraturan GROUP BY jenis")
        for jenis, count in cur.fetchall():
            code = JENIS_TO_CODE.get(jenis, jenis)
            stats["jenis_counts"][code] = count

        # 특수 유형 (slug 패턴)
        cur.execute("SELECT COUNT(*) FROM peraturan WHERE slug LIKE 'uudrt-%'")
        stats["jenis_counts"]["UUDRT"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM peraturan WHERE slug LIKE 'uud-1945%' OR jenis = 'UUD'")
        stats["jenis_counts"]["UUD"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM peraturan WHERE slug LIKE 'terjemah-%'")
        stats["jenis_counts"]["TERJEMAH"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM peraturan")
        stats["total"] = cur.fetchone()[0]

        # KOICA 정합성 상태
        for code, target in KOICA_TARGETS.items():
            current = stats["jenis_counts"].get(code, 0)
            diff = current - target
            status = "OK" if diff >= 0 else ("WARN" if diff >= -10 else "MISSING")
            stats["koica_status"][code] = {
                "target": target,
                "current": current,
                "diff": diff,
                "status": status
            }

    except Exception as e:
        stats["error"] = str(e)
    finally:
        conn.close()

    return stats


def get_running_processes() -> list[dict]:
    """실행 중인 관련 프로세스 조회"""
    processes = []

    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=10
        )

        keywords = ["python", "peraturan", "crawl", "download", "ocr", "surya", "pengundangan"]

        for line in result.stdout.split("\n"):
            if any(kw in line.lower() for kw in keywords):
                if "ps aux" in line or "grep" in line:
                    continue
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    processes.append({
                        "user": parts[0],
                        "pid": parts[1],
                        "cpu": parts[2],
                        "mem": parts[3],
                        "command": parts[10][:100]
                    })
    except Exception as e:
        processes.append({"error": str(e)})

    return processes


def get_log_tails() -> dict:
    """최근 로그 내용"""
    logs = {}
    log_files = [
        "pengundangan_update.log",
        "surya_header_uu.log",
        "agent_worklog.log"
    ]

    for log_file in log_files:
        log_path = LOG_DIR / log_file
        if log_path.exists():
            try:
                result = subprocess.run(
                    ["tail", "-5", str(log_path)],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                logs[log_file] = result.stdout.strip().split("\n")
            except Exception as e:
                logs[log_file] = [f"Error: {e}"]

    return logs


def get_disk_status() -> dict:
    """디스크 상태"""
    try:
        result = subprocess.run(
            ["df", "-h", "/home/tylor"],
            capture_output=True,
            text=True,
            timeout=5
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            return {
                "total": parts[1],
                "used": parts[2],
                "available": parts[3],
                "use_percent": parts[4]
            }
    except Exception as e:
        return {"error": str(e)}
    return {}


def get_gpu_status() -> Optional[dict]:
    """GPU 상태"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 4:
                return {
                    "name": parts[0],
                    "memory_used_mb": int(parts[1]),
                    "memory_total_mb": int(parts[2]),
                    "utilization_percent": int(parts[3])
                }
    except Exception:
        pass
    return None


def create_worklog_entry() -> dict:
    """작업 로그 엔트리 생성"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "db_stats": get_db_stats(),
        "processes": get_running_processes(),
        "disk": get_disk_status(),
        "gpu": get_gpu_status(),
        "log_tails": get_log_tails()
    }
    return entry


def write_jsonl(entry: dict):
    """JSONL 파일에 추가"""
    WORKLOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(WORKLOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def update_markdown_summary(entry: dict):
    """마크다운 요약 업데이트"""
    ts = entry["timestamp"]
    db = entry["db_stats"]
    disk = entry["disk"]
    gpu = entry["gpu"]
    processes = entry["processes"]

    # KOICA 정합성 테이블
    koica_lines = []
    koica_status = db.get("koica_status", {})
    total_missing = 0

    for code in KOICA_TARGETS.keys():
        status = koica_status.get(code, {})
        target = status.get("target", 0)
        current = status.get("current", 0)
        diff = status.get("diff", 0)
        st = status.get("status", "?")

        icon = "✅" if st == "OK" else ("⚠️" if st == "WARN" else "❌")
        koica_lines.append(f"| {code} | {target} | {current} | {diff:+d} | {icon} |")

        if diff < 0:
            total_missing += abs(diff)

    # 프로세스 테이블
    proc_lines = []
    for p in processes[:10]:
        if "error" not in p:
            cmd = p.get("command", "")[:50]
            proc_lines.append(f"| {p.get('pid', '')} | {p.get('cpu', '')}% | {p.get('mem', '')}% | {cmd}... |")

    md_content = f"""# 에이전트 작업 로그

> 마지막 업데이트: {ts}

## KOICA 정합성 현황

| 약어 | 기준 | 현재 | 차이 | 상태 |
|------|------|------|------|------|
{chr(10).join(koica_lines)}

**총 누락: {total_missing}건**

## 시스템 상태

### 디스크
- 사용: {disk.get('used', '?')} / {disk.get('total', '?')} ({disk.get('use_percent', '?')})
- 여유: {disk.get('available', '?')}

### GPU
- 모델: {gpu.get('name', 'N/A') if gpu else 'N/A'}
- 메모리: {gpu.get('memory_used_mb', '?')} / {gpu.get('memory_total_mb', '?')} MiB
- 사용률: {gpu.get('utilization_percent', '?')}%

## 실행 중인 프로세스

| PID | CPU | MEM | Command |
|-----|-----|-----|---------|
{chr(10).join(proc_lines) if proc_lines else "| - | - | - | (없음) |"}

## 최근 로그

"""

    for log_name, lines in entry.get("log_tails", {}).items():
        md_content += f"### {log_name}\n```\n"
        md_content += "\n".join(lines[-3:])
        md_content += "\n```\n\n"

    with open(WORKLOG_MD_PATH, "w", encoding="utf-8") as f:
        f.write(md_content)


def main():
    """메인 실행"""
    print(f"[{datetime.now().isoformat()}] 작업 로그 기록 중...")

    entry = create_worklog_entry()
    write_jsonl(entry)
    update_markdown_summary(entry)

    # 간단한 콘솔 출력
    db_total = entry["db_stats"].get("total", 0)
    koica_status = entry["db_stats"].get("koica_status", {})
    missing = sum(abs(s.get("diff", 0)) for s in koica_status.values() if s.get("diff", 0) < 0)

    print(f"  DB 총: {db_total}건 | KOICA 누락: {missing}건")
    print(f"  저장: {WORKLOG_PATH}")


if __name__ == "__main__":
    main()
