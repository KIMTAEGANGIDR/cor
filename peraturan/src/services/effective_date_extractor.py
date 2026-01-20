"""
시행일 추출기 (Effective Date Extractor)

법령 본문에서 시행일(tanggal berlaku) 정보 추출

패턴:
1. 공포일 시행: "mulai berlaku pada tanggal diundangkan"
2. 특정 날짜: "mulai berlaku pada tanggal 1 Januari 2025"
3. 지연 시행: "mulai berlaku 30 (tiga puluh) hari sejak tanggal diundangkan"
4. 소급 적용: "mulai berlaku surut sejak tanggal..."
5. 조건부: "mulai berlaku setelah ... ditetapkan"

현행성 판단에 중요한 메타데이터:
- tanggal_penetapan (제정일)
- tanggal_pengundangan (공포일)
- tanggal_berlaku (시행일) ← 이 모듈에서 추출
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple
from enum import Enum


class EffectiveDateType(Enum):
    """시행일 유형"""
    IMMEDIATE = "immediate"           # 공포일 즉시 시행
    SPECIFIC_DATE = "specific_date"   # 특정 날짜 지정
    DELAYED = "delayed"               # 공포 후 n일/월
    RETROACTIVE = "retroactive"       # 소급 적용
    CONDITIONAL = "conditional"       # 조건부 시행
    UNKNOWN = "unknown"               # 추출 불가


@dataclass
class EffectiveDateResult:
    """시행일 추출 결과"""
    date_type: EffectiveDateType
    effective_date: Optional[str]         # YYYY-MM-DD 형식
    raw_text: str                         # 원문 텍스트
    confidence: float                     # 추출 신뢰도 (0~1)
    delay_days: Optional[int] = None      # 지연 일수 (DELAYED 타입)
    condition: Optional[str] = None       # 조건 텍스트 (CONDITIONAL 타입)

    def to_dict(self) -> dict:
        return {
            "date_type": self.date_type.value,
            "effective_date": self.effective_date,
            "raw_text": self.raw_text,
            "confidence": round(self.confidence, 4),
            "delay_days": self.delay_days,
            "condition": self.condition,
        }


# 인도네시아어 월 이름
BULAN_MAP = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
    "september": 9, "oktober": 10, "november": 11, "desember": 12,
}

# 인도네시아어 숫자 (텍스트)
ANGKA_TEXT = {
    "satu": 1, "dua": 2, "tiga": 3, "empat": 4, "lima": 5,
    "enam": 6, "tujuh": 7, "delapan": 8, "sembilan": 9, "sepuluh": 10,
    "sebelas": 11, "dua belas": 12, "tiga belas": 13,
    "empat belas": 14, "lima belas": 15,
    "tiga puluh": 30, "enam puluh": 60, "sembilan puluh": 90,
    "seratus": 100, "seratus delapan puluh": 180,
}


class EffectiveDateExtractor:
    """
    시행일 추출기

    사용법:
        extractor = EffectiveDateExtractor()
        result = extractor.extract(text, tanggal_pengundangan="2024-01-15")
    """

    # 시행 조항 시작 패턴
    BERLAKU_SECTION_PATTERN = re.compile(
        r'(?:undang[- ]?undang|peraturan|keputusan|instruksi)\s+ini\s+mulai\s+berlaku',
        re.IGNORECASE
    )

    # 즉시 시행 패턴
    IMMEDIATE_PATTERNS = [
        re.compile(r'mulai\s+berlaku\s+pada\s+tanggal\s+di(?:undang|tetap)kan', re.I),
        re.compile(r'berlaku\s+pada\s+tanggal\s+di(?:undang|tetap)kan', re.I),
        re.compile(r'berlaku\s+sejak\s+tanggal\s+di(?:undang|tetap)kan', re.I),
    ]

    # 특정 날짜 패턴 (tanggal DD Bulan YYYY)
    SPECIFIC_DATE_PATTERN = re.compile(
        r'mulai\s+berlaku\s+(?:pada\s+)?tanggal\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})',
        re.IGNORECASE
    )

    # 지연 시행 패턴 (N hari/bulan sejak...)
    DELAYED_PATTERNS = [
        # "30 (tiga puluh) hari sejak tanggal diundangkan"
        re.compile(
            r'mulai\s+berlaku\s+(?:setelah\s+)?(\d+)\s*\([^)]+\)\s*(hari|bulan)\s+(?:sejak|setelah)\s+(?:tanggal\s+)?di(?:undang|tetap)kan',
            re.I
        ),
        # "30 hari sejak tanggal diundangkan"
        re.compile(
            r'mulai\s+berlaku\s+(?:setelah\s+)?(\d+)\s*(hari|bulan)\s+(?:sejak|setelah)\s+(?:tanggal\s+)?di(?:undang|tetap)kan',
            re.I
        ),
        # "tiga puluh hari sejak..."
        re.compile(
            r'mulai\s+berlaku\s+(?:setelah\s+)?([\w\s]+?)\s*(hari|bulan)\s+(?:sejak|setelah)\s+(?:tanggal\s+)?di(?:undang|tetap)kan',
            re.I
        ),
    ]

    # 소급 적용 패턴
    RETROACTIVE_PATTERN = re.compile(
        r'mulai\s+berlaku\s+(?:surut\s+)?(?:sejak|pada)\s+tanggal\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})',
        re.IGNORECASE
    )

    # 조건부 시행 패턴
    CONDITIONAL_PATTERNS = [
        re.compile(r'mulai\s+berlaku\s+setelah\s+(.+?)\s+di(?:tetap|undang)kan', re.I),
        re.compile(r'mulai\s+berlaku\s+(?:pada\s+)?(?:saat|ketika)\s+(.+)', re.I),
    ]

    def extract(
        self,
        text: str,
        tanggal_pengundangan: Optional[str] = None,
    ) -> EffectiveDateResult:
        """
        시행일 추출

        Args:
            text: 법령 본문
            tanggal_pengundangan: 공포일 (YYYY-MM-DD) - 계산에 사용

        Returns:
            EffectiveDateResult
        """
        if not text:
            return EffectiveDateResult(
                date_type=EffectiveDateType.UNKNOWN,
                effective_date=None,
                raw_text="",
                confidence=0.0,
            )

        # 시행 조항 영역 찾기 (문서 후반부에 있음)
        berlaku_section = self._find_berlaku_section(text)
        if not berlaku_section:
            # 전체 텍스트에서 검색
            berlaku_section = text[-3000:]  # 마지막 3000자

        # 1. 즉시 시행 확인
        for pattern in self.IMMEDIATE_PATTERNS:
            match = pattern.search(berlaku_section)
            if match:
                return EffectiveDateResult(
                    date_type=EffectiveDateType.IMMEDIATE,
                    effective_date=tanggal_pengundangan,
                    raw_text=match.group(0),
                    confidence=0.95,
                )

        # 2. 특정 날짜 확인
        match = self.SPECIFIC_DATE_PATTERN.search(berlaku_section)
        if match:
            date_str = self._parse_indonesian_date(
                match.group(1), match.group(2), match.group(3)
            )
            return EffectiveDateResult(
                date_type=EffectiveDateType.SPECIFIC_DATE,
                effective_date=date_str,
                raw_text=match.group(0),
                confidence=0.90,
            )

        # 3. 지연 시행 확인
        for pattern in self.DELAYED_PATTERNS:
            match = pattern.search(berlaku_section)
            if match:
                days = self._parse_delay(match.group(1), match.group(2))
                effective_date = None
                if tanggal_pengundangan and days:
                    effective_date = self._calculate_delayed_date(
                        tanggal_pengundangan, days
                    )
                return EffectiveDateResult(
                    date_type=EffectiveDateType.DELAYED,
                    effective_date=effective_date,
                    raw_text=match.group(0),
                    confidence=0.85,
                    delay_days=days,
                )

        # 4. 소급 적용 확인
        match = self.RETROACTIVE_PATTERN.search(berlaku_section)
        if match:
            date_str = self._parse_indonesian_date(
                match.group(1), match.group(2), match.group(3)
            )
            return EffectiveDateResult(
                date_type=EffectiveDateType.RETROACTIVE,
                effective_date=date_str,
                raw_text=match.group(0),
                confidence=0.85,
            )

        # 5. 조건부 시행 확인
        for pattern in self.CONDITIONAL_PATTERNS:
            match = pattern.search(berlaku_section)
            if match:
                return EffectiveDateResult(
                    date_type=EffectiveDateType.CONDITIONAL,
                    effective_date=None,
                    raw_text=match.group(0),
                    confidence=0.70,
                    condition=match.group(1).strip(),
                )

        # 추출 실패
        return EffectiveDateResult(
            date_type=EffectiveDateType.UNKNOWN,
            effective_date=None,
            raw_text="",
            confidence=0.0,
        )

    def _find_berlaku_section(self, text: str) -> Optional[str]:
        """시행 조항 영역 찾기"""
        match = self.BERLAKU_SECTION_PATTERN.search(text)
        if match:
            # 매치 위치부터 500자
            start = match.start()
            return text[start:start + 500]
        return None

    def _parse_indonesian_date(
        self,
        day: str,
        month: str,
        year: str
    ) -> Optional[str]:
        """인도네시아어 날짜를 YYYY-MM-DD로 변환"""
        try:
            day_int = int(day)
            month_int = BULAN_MAP.get(month.lower())
            year_int = int(year)

            if month_int and 1 <= day_int <= 31 and 1945 <= year_int <= 2100:
                return f"{year_int:04d}-{month_int:02d}-{day_int:02d}"
        except (ValueError, TypeError):
            pass
        return None

    def _parse_delay(self, number_str: str, unit: str) -> Optional[int]:
        """지연 기간 파싱"""
        try:
            # 숫자인 경우
            days = int(number_str)
        except ValueError:
            # 텍스트인 경우
            number_str_lower = number_str.lower().strip()
            days = ANGKA_TEXT.get(number_str_lower)
            if not days:
                return None

        # 월 단위면 30일로 변환
        if unit.lower() == "bulan":
            days *= 30

        return days

    def _calculate_delayed_date(
        self,
        base_date: str,
        delay_days: int
    ) -> Optional[str]:
        """지연 시행일 계산"""
        try:
            base = datetime.strptime(base_date, "%Y-%m-%d")
            effective = base + timedelta(days=delay_days)
            return effective.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return None


def extract_effective_date(
    text: str,
    tanggal_pengundangan: Optional[str] = None,
) -> EffectiveDateResult:
    """
    편의 함수: 시행일 추출

    Args:
        text: 법령 본문
        tanggal_pengundangan: 공포일

    Returns:
        EffectiveDateResult
    """
    extractor = EffectiveDateExtractor()
    return extractor.extract(text, tanggal_pengundangan)


# CLI 테스트
if __name__ == "__main__":
    print("=== 시행일 추출 테스트 ===\n")

    extractor = EffectiveDateExtractor()

    test_cases = [
        # 즉시 시행
        ("Undang-Undang ini mulai berlaku pada tanggal diundangkan.", "2024-01-15"),
        # 특정 날짜
        ("Peraturan ini mulai berlaku pada tanggal 1 Januari 2025.", None),
        # 지연 시행
        ("Peraturan ini mulai berlaku 30 (tiga puluh) hari sejak tanggal diundangkan.", "2024-01-15"),
        # 조건부
        ("Undang-Undang ini mulai berlaku setelah Peraturan Pemerintah ditetapkan.", None),
    ]

    for text, pub_date in test_cases:
        result = extractor.extract(text, pub_date)
        print(f"텍스트: {text[:60]}...")
        print(f"  유형: {result.date_type.value}")
        print(f"  시행일: {result.effective_date}")
        print(f"  신뢰도: {result.confidence:.2f}")
        if result.delay_days:
            print(f"  지연: {result.delay_days}일")
        if result.condition:
            print(f"  조건: {result.condition}")
        print()
