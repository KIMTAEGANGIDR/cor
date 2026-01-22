#!/bin/bash
# 자동 커밋 및 푸시 스크립트
# 매일 오후 3시에 cron으로 실행

REPO_DIR="/home/claude_user/workspace/cor"
LOG_FILE="$REPO_DIR/logs/auto_commit.log"
DATE=$(date '+%Y-%m-%d %H:%M:%S')

# 로그 디렉토리 생성
mkdir -p "$REPO_DIR/logs"

cd "$REPO_DIR" || exit 1

# 변경사항 확인
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    echo "[$DATE] 변경사항 없음" >> "$LOG_FILE"
    exit 0
fi

# 모든 변경사항 스테이징
git add -A

# 커밋 메시지 생성
COMMIT_MSG="chore: 자동 커밋 $(date '+%Y-%m-%d %H:%M')"

# 커밋
if git commit -m "$COMMIT_MSG"; then
    echo "[$DATE] 커밋 성공: $COMMIT_MSG" >> "$LOG_FILE"
else
    echo "[$DATE] 커밋 실패" >> "$LOG_FILE"
    exit 1
fi

# 푸시
if git push; then
    echo "[$DATE] 푸시 성공" >> "$LOG_FILE"
else
    echo "[$DATE] 푸시 실패" >> "$LOG_FILE"
    exit 1
fi

echo "[$DATE] 자동 커밋/푸시 완료" >> "$LOG_FILE"
