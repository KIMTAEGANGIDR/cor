# Work Log

Claude Code 세션에서 수행한 작업 기록

---

## 2026-01-21

### 주요 작업 요약

#### 하이브리드 텍스트 저장 구현 (11:00경)
- **목적**: OCR 추출 텍스트를 DB에도 저장하여 재처리 없이 조회 가능하게 함
- **변경 사항**:
  - `pages` 테이블에 `raw_text` 컬럼 추가
  - `structured_documents` 테이블 신규 생성 (구조화된 문서 메타데이터)
  - `structured_sections` 테이블 신규 생성 (Bab, Pasal, Ayat 계층 구조)
- **추가된 메서드**:
  - `get_page_text()`, `get_document_text()` - 텍스트 조회
  - `save_structured_document()`, `save_structured_section()` - 구조화 데이터 저장
  - `get_structured_document()`, `get_structured_sections()` - 구조화 데이터 조회
- **커밋**: `a245a47` feat: 하이브리드 텍스트 저장 구현

#### OCR 파이프라인 개선 (10:30~11:00)
- 앙상블 OCR 기능 추가 (`ensemble_ocr.py`)
- 내장 텍스트 vs OCR 비교 로직 추가 (v2.1)
- 병렬 처리로 GPU 활용도 향상 (v2.2)
- **커밋**: `46c634d` feat: OCR 파이프라인 개선 및 앙상블 OCR 추가

---

### 상세 로그

- **11:09:39** [Bash] `sleep 180 && python3 -c "
import sqlite3
conn = sqlite3.connect('/tmp/ocr_parallel_test_300/checkpoi...`

- **11:06:28** [Bash] `sleep 120 && python3 -c "
import sqlite3
conn = sqlite3.connect('/tmp/ocr_parallel_test_300/checkpoi...`

- **11:04:16** [Bash] `sleep 60 && python3 -c "
import sqlite3
conn = sqlite3.connect('/tmp/ocr_parallel_test_300/checkpoin...`

- **11:03:04** [Bash] `sleep 30 && python3 -c "
import sqlite3
conn = sqlite3.connect('/tmp/ocr_parallel_test_300/checkpoin...`

- **11:02:21** [Bash] `python3 -c "
import sqlite3
conn = sqlite3.connect('/tmp/ocr_parallel_test_300/checkpoints/checkpoin...`

- **11:02:11** [Bash] `nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 1 2>/dev/null | head -5`

- **11:01:51** [Bash] `ps aux | grep -E "python.*pipeline" | grep -v grep`

- **11:01:51** [Bash] `wc -l /tmp/claude/-home-claude-user-workspace/tasks/be277c8.output && tail -20 /tmp/claude/-home-cla...`

- **11:01:44** [Bash] `nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv`

- **11:01:42** [Bash] `cat /tmp/claude/-home-claude-user-workspace/tasks/be277c8.output | tail -100`

- **11:00:31** [Edit] 파일 수정: `pipeline.py`

- **10:59:44** [Bash] `nvidia-smi 2>/dev/null || echo "nvidia-smi not available"`

- **10:59:36** [Bash] `sleep 10 && cat /tmp/claude/-home-claude-user-workspace/tasks/be277c8.output`

- **10:59:20** [Bash] `cat /tmp/claude/-home-claude-user-workspace/tasks/be277c8.output 2>/dev/null || echo "출력 대기 중..."`

- **10:59:15** [Bash] `sleep 30 && tail -50 /tmp/claude/-home-claude-user-workspace/tasks/be277c8.output`

- **10:58:39** [Bash] `PYTHONPATH=. python -m peraturan.src.ocr.pipeline run --input peraturan/data/pdfs --output /tmp/ocr_...`

- **10:58:30** [Edit] 파일 수정: `pipeline.py`

- **10:58:15** [Edit] 파일 수정: `pipeline.py`

- **10:57:48** [Edit] 파일 수정: `pipeline.py`

- **10:57:38** [Edit] 파일 수정: `pipeline.py`

- **10:57:23** [Edit] 파일 수정: `ensemble_ocr.py`

- **10:57:06** [Edit] 파일 수정: `ensemble_ocr.py`

- **10:55:24** [Edit] 파일 수정: `WORKLOG.md`

- **10:54:42** [Bash] `python3 << 'EOF'
import sqlite3
from datetime import datetime
from pathlib import Path

def generate...`

- **10:54:01** [Bash] `PYTHONPATH=. python -m peraturan.src.ocr.pipeline run --input peraturan/data/pdfs --output /tmp/ocr_...`

- **10:53:34** [Edit] 파일 수정: `pipeline.py`

- **10:52:59** [Edit] 파일 수정: `pipeline.py`

- **10:52:47** [Edit] 파일 수정: `pipeline.py`

- **10:52:36** [Edit] 파일 수정: `pipeline.py`

- **10:52:29** [Edit] 파일 수정: `pipeline.py`

- **10:51:11** [Bash] `python3 << 'EOF'
import fitz
import difflib

# 샘플로 비교해보기
pdf_path = "peraturan/data/pdfs/inpres/inpr...`

- **10:50:51** [Bash] `grep -n "compare\|비교\|embedded.*ocr\|ocr.*embedded" peraturan/src/ocr/*.py 2>/dev/null || echo "비교 로...`

- **10:49:23** [Bash] `python3 << 'EOF'
import fitz
import sys
sys.path.insert(0, '.')

from peraturan.src.ocr.quality_scor...`

- **10:48:07** [Bash] `python3 << 'EOF'
import sqlite3
import json
from datetime import datetime
from pathlib import Path

...`

- **10:47:41** [Bash] `PYTHONPATH=. python -m peraturan.src.ocr.pipeline run --input peraturan/data/pdfs --output /tmp/ocr_...`

- **10:43:36** [Bash] `python3 << 'EOF'
import fitz  # PyMuPDF

pdf_path = "peraturan/data/pdfs/inpres/inpres-no-1-tahun-19...`

- **10:43:28** [Bash] `ls -la peraturan/data/pdfs/inpres/ | head -15`

- **10:43:28** [Bash] `ls -lh peraturan/data/pdfs/inpres/inpres-no-1-tahun-1980.pdf`

- **10:42:43** [Bash] `echo "=== 샘플: inpres-no-1-tahun-1980 (3페이지) ===" && head -60 /tmp/ocr_gpu_test_10/text/inpres__inpre...`

- **10:42:36** [Bash] `python3 << 'EOF'
import sqlite3
import json
from pathlib import Path

db_path = "/tmp/ocr_gpu_test_1...`

- **10:42:18** [Bash] `ls -la /tmp/ocr_gpu_test_10/text/`

- **10:42:18** [Bash] `ls -la /tmp/ocr_gpu_test_10/`

- **10:42:11** [Bash] `PYTHONPATH=. python -m peraturan.src.ocr.pipeline run --input peraturan/data/pdfs --output /tmp/ocr_...`

- **10:40:32** [Bash] `ls -la /home/claude_user/workspace/`

- **10:39:45** [Bash] `head -50 /tmp/ocr_test_output/text/*.txt 2>/dev/null | head -80`

- **10:39:45** [Bash] `ls -la /tmp/ocr_test_output/text/`

- **10:39:37** [Bash] `PYTHONPATH=. python -m peraturan.src.ocr.pipeline run --input peraturan/data/pdfs --output /tmp/ocr_...`

- **10:39:17** [Bash] `find /home/claude_user/workspace -name "*.pdf" 2>/dev/null | head -20`

- **10:39:11** [Bash] `PYTHONPATH=. python -m peraturan.src.ocr.pipeline run --help`

- **10:39:01** [Bash] `cd /home/claude_user/workspace/cor && PYTHONPATH=. python -m peraturan.src.ocr.pipeline --help`

- **10:38:24** [Bash] `ls -la`

- **10:38:00** [Bash] `ls -la peraturan/data/`

- **10:37:57** [Bash] `find peraturan -name "*index*.json" -o -name "*document*.json" 2>/dev/null | head -20`

- **10:37:51** [Bash] `python -m peraturan.src.ocr.testing.cli sample --help`

- **10:37:41** [Bash] `python -m peraturan.src.ocr.testing.cli run --run-id phase1_gpu_test --input peraturan/data/ocr_outp...`

- **10:37:35** [Bash] `python -m peraturan.src.ocr.testing.cli --help`

- **10:37:20** [Bash] `python -m peraturan.src.ocr.testing.cli run --help`

- **10:30:59** [Bash] `source .venv/bin/activate && python -c "from paddleocr import PaddleOCR; print('PaddleOCR OK')" 2>&1...`

- **10:29:05** [Edit] 파일 수정: `cli.py`

- **10:19:06** [Bash] `tail -100 /tmp/claude/-home-claude-user-workspace/tasks/be44a6d.output`

- **10:19:02** [Bash] `source .venv/bin/activate && PYTHONPATH=. python -m peraturan.src.ocr.testing.cli --db peraturan/dat...`

- **10:08:54** [Bash] `source .venv/bin/activate && PYTHONPATH=. python -m peraturan.src.ocr.testing.cli --db peraturan/dat...`

- **10:08:23** [Bash] `which tesseract || apt list --installed 2>/dev/null | grep -i tesseract || echo "Tesseract binary no...`

- **10:08:20** [Bash] `source .venv/bin/activate && pip install pytesseract`

- **10:08:10** [Bash] `source .venv/bin/activate && python -c "import pytesseract; print('Tesseract available')" 2>&1 || ec...`

- **10:08:08** [Bash] `source .venv/bin/activate && python -c "from paddleocr import PaddleOCR; print('PaddleOCR available'...`

- **10:07:54** [Edit] 파일 수정: `cli.py`

- **10:06:10** [Bash] `source .venv/bin/activate && PYTHONPATH=. python -m peraturan.src.ocr.testing.cli --db peraturan/dat...`

- **10:05:59** [Bash] `source .venv/bin/activate && PYTHONPATH=. python -m peraturan.src.ocr.testing.cli --db peraturan/dat...`

- **10:05:51** [Bash] `source .venv/bin/activate && PYTHONPATH=. python -m peraturan.src.ocr.testing.cli --db peraturan/dat...`

- **10:05:43** [Bash] `source .venv/bin/activate && PYTHONPATH=. python -m peraturan.src.ocr.testing.cli --db peraturan/dat...`

- **10:05:34** [Bash] `source .venv/bin/activate && PYTHONPATH=. python -m peraturan.src.ocr.testing.cli --db peraturan/dat...`

- **10:05:27** [Bash] `source .venv/bin/activate && PYTHONPATH=. python -m peraturan.src.ocr.testing.cli --db peraturan/dat...`

- **10:05:20** [Bash] `source .venv/bin/activate && PYTHONPATH=. python -m peraturan.src.ocr.testing.cli --help`

- **10:05:16** [Bash] `source .venv/bin/activate && pip install pillow pymupdf`

- **10:04:59** [Bash] `cd /home/claude_user/workspace/cor && source .venv/bin/activate && pip install numpy click rich`

- **10:04:40** [Bash] `ls -la /home/claude_user/workspace/cor/peraturan/src/data/ 2>/dev/null || echo "src/data dir not fou...`

- **10:04:40** [Bash] `ls -la /home/claude_user/workspace/cor/peraturan/data/ 2>/dev/null || echo "data dir not found"`

- **10:04:16** [Bash] `ls -la`

- **09:55:05** [Bash] `echo test`

