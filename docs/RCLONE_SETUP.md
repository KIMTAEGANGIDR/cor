# Google Drive 폴더 다운로드 가이드

## 상황

- Google Drive 데스크톱 앱의 "스트리밍" 모드로 인해 PDF 파일들이 클라우드에만 존재
- 로컬에 실제 파일 없음 (아이콘만 보임)
- 크롤링 프로젝트를 동기화 없이 로컬에서 계속 진행하려면 파일을 직접 다운로드해야 함

## 해결책: rclone

### 1. 설치

```bash
brew install rclone
```

### 2. Google Drive 연결 설정

```bash
rclone config
```

설정 단계:
1. `n` (new remote)
2. 이름 입력: `gdrive`
3. 스토리지 타입: `drive` (Google Drive 번호 선택)
4. client_id: 엔터 (기본값)
5. client_secret: 엔터 (기본값)
6. scope: `1` (Full access)
7. root_folder_id: 엔터
8. service_account_file: 엔터
9. Edit advanced config: `n`
10. Use auto config: `y` → 브라우저에서 Google 로그인
11. Configure as team drive: `n`
12. `y` (확인)
13. `q` (종료)

### 3. 폴더 목록 확인

```bash
# 루트 폴더 목록
rclone lsd gdrive:

# 특정 폴더 내용 확인
rclone ls gdrive:"폴더명"
```

### 4. 다운로드

```bash
# 전체 폴더 다운로드
rclone copy gdrive:"원본폴더경로" /Users/kim/로컬경로 --progress

# PDF만 다운로드
rclone copy gdrive:"원본폴더경로" /Users/kim/로컬경로 --include "*.pdf" --progress

# 드라이런 (실제 다운로드 없이 확인)
rclone copy gdrive:"원본폴더경로" /Users/kim/로컬경로 --dry-run
```

### 5. 유용한 옵션

| 옵션 | 설명 |
|------|------|
| `--progress` | 진행률 표시 |
| `--dry-run` | 테스트 (실제 다운로드 안함) |
| `--include "*.pdf"` | 특정 확장자만 |
| `--exclude "*.tmp"` | 특정 확장자 제외 |
| `--transfers 4` | 동시 다운로드 수 (기본 4) |
| `-v` | 상세 로그 |

### 참고

- `copy`: 단방향 복사 (로컬에 없는 파일만 다운로드)
- `sync`: 동기화 (로컬에서 삭제된 파일도 반영 - 주의!)
- 중단 후 다시 실행하면 이어받기 됨
