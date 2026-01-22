#!/usr/bin/env python3
"""
자동 커밋 데몬
매일 오후 3시에 변경사항을 커밋하고 푸시합니다.

실행: nohup python3 auto_commit_daemon.py &
"""

import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_DIR = Path("/home/claude_user/workspace/cor")
LOG_FILE = REPO_DIR / "logs" / "auto_commit.log"
TARGET_HOUR = 15  # 오후 3시
TARGET_MINUTE = 0


def log(message: str):
    """로그 기록"""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {message}\n")
    print(f"[{timestamp}] {message}")


def has_changes() -> bool:
    """변경사항 확인"""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True
    )
    return bool(result.stdout.strip())


def auto_commit_push():
    """커밋 및 푸시 수행"""
    if not has_changes():
        log("변경사항 없음 - 스킵")
        return

    # git add
    subprocess.run(["git", "add", "-A"], cwd=REPO_DIR, check=True)

    # commit
    commit_msg = f"chore: 자동 커밋 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=REPO_DIR,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        log(f"커밋 실패: {result.stderr}")
        return

    log(f"커밋 완료: {commit_msg}")

    # push
    result = subprocess.run(
        ["git", "push"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        log(f"푸시 실패: {result.stderr}")
        return

    log("푸시 완료")


def get_seconds_until_target() -> float:
    """다음 실행 시간까지 남은 초 계산"""
    now = datetime.now()
    target = now.replace(hour=TARGET_HOUR, minute=TARGET_MINUTE, second=0, microsecond=0)

    if now >= target:
        target += timedelta(days=1)

    return (target - now).total_seconds()


def main():
    log(f"자동 커밋 데몬 시작 (매일 {TARGET_HOUR}:{TARGET_MINUTE:02d})")

    while True:
        wait_seconds = get_seconds_until_target()
        next_run = datetime.now() + timedelta(seconds=wait_seconds)
        log(f"다음 실행: {next_run.strftime('%Y-%m-%d %H:%M:%S')} ({wait_seconds/3600:.1f}시간 후)")

        time.sleep(wait_seconds)

        try:
            auto_commit_push()
        except Exception as e:
            log(f"오류 발생: {e}")


if __name__ == "__main__":
    main()
