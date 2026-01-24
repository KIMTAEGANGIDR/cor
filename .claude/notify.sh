#!/bin/bash
# 티벳 종소리 알림 스크립트

SOUND_FILE="$HOME/.claude/tibetan-bell.wav"

# 종소리 파일이 없으면 다운로드
if [ ! -f "$SOUND_FILE" ]; then
    mkdir -p "$HOME/.claude"
    # 무료 종소리 다운로드 (대체 URL 사용)
    curl -sL "https://freesound.org/data/previews/411/411089_5121236-lq.mp3" -o "$HOME/.claude/tibetan-bell.mp3" 2>/dev/null
    SOUND_FILE="$HOME/.claude/tibetan-bell.mp3"
fi

# 재생 (사용 가능한 플레이어 시도)
if command -v paplay &>/dev/null; then
    paplay "$SOUND_FILE" 2>/dev/null &
elif command -v aplay &>/dev/null; then
    aplay "$SOUND_FILE" 2>/dev/null &
elif command -v mpv &>/dev/null; then
    mpv --no-video "$SOUND_FILE" 2>/dev/null &
elif command -v ffplay &>/dev/null; then
    ffplay -nodisp -autoexit "$SOUND_FILE" 2>/dev/null &
else
    # 터미널 벨 폴백
    echo -e '\a'
fi
