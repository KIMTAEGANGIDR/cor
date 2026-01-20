"""
인도네시아 법령 PDF 텍스트 전처리 모듈
- 워터마크 제거
- 페이지 헤더/푸터 제거
- OCR 오류 수정
- 줄바꿈 정규화
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class PreprocessResult:
    """전처리 결과"""
    original: str
    cleaned: str
    changes: list[str]  # 적용된 변경 사항


class LawTextPreprocessor:
    """법령 텍스트 전처리기"""

    # 워터마크 패턴
    WATERMARKS = [
        r'www\.djpp\.depkumham\.go\.id',
        r'www\.peraturan\.go\.id',
        r'ditjen Peraturan Perundang-undangan',
        r'jdih\.kemenkeu\.go\.id',
        r'Direktorat Jenderal Peraturan Perundang-undangan',
    ]

    # 페이지 헤더/푸터 패턴
    PAGE_HEADERS = [
        r'^PRESIDEN\s*$',
        r'^PRESIDEN\s+REPUBLIK\s+INDONESIA\s*$',
        r'^REPUBLIK\s+INDONESIA\s*$',
        r'^PRESTDEN\s*$',  # OCR 오류
        r'^REPUBLTK\s+INDONESIA\s*$',  # OCR 오류
        r'^SK\s*No\s*[\d\s]+[A-Z]?\s*$',  # SK No 250003 A
        r'^-\s*\d+\s*-\s*$',  # 페이지 번호 (- 1 -, - 2 -)
        r'^\d{1,3}\s*$',  # 단독 페이지 번호 (1-999)
        r'^\.{3,}\s*$',  # ... (페이지 구분)
        r'^…+\s*$',  # … (ellipsis)
        r'^\.\.\.\s*$',  # ...
        r'^Pasal\s+\d+\s*\.\.\.\s*$',  # Pasal 1 ...
        r'^[A-Z]\.\s*Pasal\s+\d+\s*\.\.\.\s*$',  # A. Pasal 1 ...
    ]

    # OCR 오류 수정 맵
    OCR_FIXES = {
        # 숫자/문자 혼동 (순서 중요)
        r'(\d)O(\d)': r'\g<1>0\g<2>',  # 2O09 → 2009
        r'O(\d{3})': r'0\1',  # O123 → 0123
        r'(\d{3})O': r'\g<1>0',  # 123O → 1230
        r'\bl(\d)': r'1\1',  # l9 → 19 (소문자 L, 단어 경계)
        r'(\d)l\b': r'\g<1>1',  # 9l → 91

        # 일반적인 OCR 오류
        r'PRESTDEN': 'PRESIDEN',
        r'REPUBLTK': 'REPUBLIK',
        r'INDONES[1I]A': 'INDONESIA',
        r'kmbaran': 'Lembaran',
        r'tentartg': 'tentang',
        r'ssfegian': 'sebagian',
        r'pefanjian': 'perjanjian',
        r'izrn': 'izin',
        r'ruP': 'IUP',

        # 구두점 오류
        r'UNDANG\.UNDANG': 'UNDANG-UNDANG',
        r'UNDANG\s*-\s*UNDANG': 'UNDANG-UNDANG',
        r'Pasall': 'Pasal I',  # Pasall... → Pasal I

        # 공백 오류
        r'\s*:\s*': ': ',
        r'\s*;\s*': '; ',
    }

    def __init__(self, year: Optional[int] = None):
        self.year = year
        self.changes = []

    def preprocess(self, text: str) -> PreprocessResult:
        """전체 전처리 수행"""
        self.changes = []
        original = text

        # 1. 워터마크 제거
        text = self._remove_watermarks(text)

        # 2. 페이지 헤더/푸터 제거
        text = self._remove_page_headers(text)

        # 3. OCR 오류 수정
        text = self._fix_ocr_errors(text)

        # 4. 줄바꿈 정규화
        text = self._normalize_line_breaks(text)

        # 5. 공백 정규화
        text = self._normalize_whitespace(text)

        return PreprocessResult(
            original=original,
            cleaned=text,
            changes=self.changes
        )

    def _remove_watermarks(self, text: str) -> str:
        """워터마크 제거"""
        for pattern in self.WATERMARKS:
            if re.search(pattern, text, re.IGNORECASE):
                text = re.sub(pattern, '', text, flags=re.IGNORECASE)
                self.changes.append(f"워터마크 제거: {pattern[:30]}")
        return text

    def _remove_page_headers(self, text: str) -> str:
        """페이지 헤더/푸터 제거"""
        lines = text.split('\n')
        cleaned_lines = []

        for line in lines:
            stripped = line.strip()
            is_header = False

            for pattern in self.PAGE_HEADERS:
                if re.match(pattern, stripped, re.IGNORECASE | re.MULTILINE):
                    is_header = True
                    self.changes.append(f"헤더 제거: {stripped[:40]}")
                    break

            if not is_header:
                cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def _fix_ocr_errors(self, text: str) -> str:
        """OCR 오류 수정"""
        for pattern, replacement in self.OCR_FIXES.items():
            if re.search(pattern, text):
                count = len(re.findall(pattern, text))
                text = re.sub(pattern, replacement, text)
                self.changes.append(f"OCR 수정: {pattern[:20]} ({count}건)")
        return text

    def _normalize_line_breaks(self, text: str) -> str:
        """줄바꿈 정규화 - 문장 중간 줄바꿈 연결"""
        lines = text.split('\n')
        result = []
        buffer = ""

        for line in lines:
            stripped = line.strip()

            if not stripped:
                # 빈 줄: 버퍼 비우고 빈 줄 추가
                if buffer:
                    result.append(buffer)
                    buffer = ""
                result.append("")
                continue

            # 구조적 요소 시작인 경우 (새 줄 시작)
            if self._is_structural_start(stripped):
                if buffer:
                    result.append(buffer)
                    buffer = ""
                buffer = stripped
                continue

            # 이전 줄이 문장 중간에서 끊긴 경우 연결
            if buffer and self._should_join(buffer, stripped):
                # 하이픈 연결 (단어 중간 줄바꿈)
                if buffer.endswith('-'):
                    buffer = buffer[:-1] + stripped
                else:
                    buffer = buffer + ' ' + stripped
            else:
                if buffer:
                    result.append(buffer)
                buffer = stripped

        if buffer:
            result.append(buffer)

        original_lines = len(text.split('\n'))
        new_lines = len(result)
        if original_lines != new_lines:
            self.changes.append(f"줄바꿈 정규화: {original_lines} → {new_lines}줄")

        return '\n'.join(result)

    def _is_structural_start(self, line: str) -> bool:
        """구조적 요소 시작 여부 확인"""
        patterns = [
            r'^BAB\s+[IVXLCDM]+',  # BAB I, BAB II, ...
            r'^Pasal\s+\d+',  # Pasal 1, Pasal 2, ...
            r'^Pasal\s+[IVXLCDM]+',  # Pasal I, Pasal II (구 법령)
            r'^Bagian\s+',  # Bagian Kesatu, ...
            r'^Paragraf\s+',  # Paragraf 1, ...
            r'^\(\d+\)',  # (1), (2), ... (Ayat)
            r'^[a-z]\.\s',  # a. , b. , c.  ... (Huruf, 공백 필수)
            r'^\d+\.\s',  # 1. , 2. , 3.  ... (Angka, 공백 필수)
            r'^\d+[a-z]\.\s',  # 6a. , 6b.  ... (하위 항목)
            r'^Menimbang',
            r'^Mengingat',
            r'^Menetapkan',
            r'^MEMUTUSKAN',
            r'^DENGAN RAHMAT',
            r'^PENJELASAN',
            r'^I\.\s+UMUM',
            r'^II\.\s+PASAL',
            r'^UNDANG-UNDANG\s+TENTANG',  # 법률 제목
            r'^UNDANG-UNDANG\s+NOMOR',  # 법률 번호
        ]
        for pattern in patterns:
            if re.match(pattern, line, re.IGNORECASE):
                return True
        return False

    def _should_join(self, prev: str, curr: str) -> bool:
        """두 줄을 연결해야 하는지 확인"""
        # 이전 줄이 문장 종결 부호로 끝나면 연결 안 함
        if prev.rstrip().endswith(('.', ';', ':', '!', '?')):
            # 단, 약어는 예외
            if not re.search(r'\b(No|Pasal|dst|dll|dsb)\.$', prev):
                return False

        # 현재 줄이 대문자로 시작하는 구조 요소면 연결 안 함
        if re.match(r'^[A-Z]{2,}', curr):
            return False

        # 현재 줄이 번호로 시작하면 연결 안 함
        if re.match(r'^[\d\(\)a-z]\.?\s', curr):
            return False

        # 이전 줄이 하이픈으로 끝나면 연결 (단어 분리)
        if prev.endswith('-'):
            return True

        # 이전 줄이 소문자/쉼표로 끝나면 연결
        if prev.rstrip()[-1:].islower() or prev.rstrip().endswith(','):
            return True

        return False

    def _normalize_whitespace(self, text: str) -> str:
        """공백 정규화"""
        # 연속 공백을 단일 공백으로
        text = re.sub(r'[^\S\n]+', ' ', text)

        # 줄 시작/끝 공백 제거
        lines = [line.strip() for line in text.split('\n')]

        # 연속 빈 줄을 최대 2줄로
        result = []
        blank_count = 0
        for line in lines:
            if not line:
                blank_count += 1
                if blank_count <= 2:
                    result.append(line)
            else:
                blank_count = 0
                result.append(line)

        return '\n'.join(result)


def get_preprocessor(year: Optional[int] = None) -> LawTextPreprocessor:
    """연도에 맞는 전처리기 반환"""
    return LawTextPreprocessor(year=year)


# 테스트용
if __name__ == '__main__':
    sample = """
www.djpp.depkumham.go.id
ditjen Peraturan Perundang-undangan

PRESIDEN
REPUBLIK INDONESIA

UNDANG-UNDANG TENTANG PERUBAHAN ATAS
UNDANG.UNDANG NOMOR 4 TAHUN 2OO9 TENTANG
PERTAMBANGAN MINERAL DAN BATUBARA.

SK No250002A

Pasal 1
Dalam Undang-Undang ini yang dimaksud dengan
Pertambangan adalah kegiatan dalam rangka
pengelolaan dan pengusahaan mineral.

(1) Mineral adalah senyawa anorganik yang ter-
bentuk di alam.
    """

    preprocessor = LawTextPreprocessor(year=2020)
    result = preprocessor.preprocess(sample)

    print("=== 원본 ===")
    print(result.original[:500])
    print("\n=== 정제 후 ===")
    print(result.cleaned)
    print("\n=== 변경 사항 ===")
    for change in result.changes:
        print(f"  - {change}")
