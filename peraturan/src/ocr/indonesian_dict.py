"""
인도네시아어 사전 및 형태소 분석기

구성:
1. Sastrawi 기반 스테머 (형태소 분석)
2. 일반 단어 사전 (공개 리스트)
3. 법률 용어 사전 (도메인 특화)
4. 약어/두문자어 처리
5. 불용어 리스트
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional, Set

try:
    from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
    from Sastrawi.StopWordRemover.StopWordRemoverFactory import StopWordRemoverFactory
    SASTRAWI_AVAILABLE = True
except ImportError:
    SASTRAWI_AVAILABLE = False


class IndonesianDictionary:
    """
    인도네시아어 사전 및 형태소 분석기

    사용법:
        dict = IndonesianDictionary()
        dict.is_valid_word("undang")  # True
        dict.is_legal_term("PERPRES")  # True
        dict.get_stem("perundangan")  # "undang"
    """

    # 법률 용어 (도메인 특화)
    LEGAL_TERMS = {
        # 법령 유형
        "undang", "peraturan", "keputusan", "instruksi", "penetapan",
        "perpu", "perppu", "perpres", "permen", "perban",
        "keppres", "inpres", "kepmen", "surat", "edaran",

        # 법률 구조
        "pasal", "ayat", "huruf", "angka", "bab", "bagian", "paragraf",
        "ketentuan", "umum", "penutup", "peralihan",

        # 법적 행위
        "menetapkan", "memutuskan", "menimbang", "mengingat", "memperhatikan",
        "menyatakan", "memerintahkan", "mengatur", "melarang", "mewajibkan",
        "mencabut", "mengubah", "menambah", "menghapus",

        # 법적 개념
        "hukum", "hak", "kewajiban", "wewenang", "sanksi", "pidana", "perdata",
        "pelanggaran", "kejahatan", "tindak", "larangan", "izin", "perizinan",
        "perjanjian", "kontrak", "gugatan", "putusan", "vonis",

        # 기관/주체
        "pemerintah", "presiden", "menteri", "gubernur", "bupati", "walikota",
        "dpr", "dprd", "mpr", "mahkamah", "pengadilan", "kejaksaan", "kepolisian",
        "negara", "daerah", "republik", "indonesia",

        # 법적 상태
        "berlaku", "dicabut", "diubah", "tidak", "bertentangan", "sepanjang",
        "sah", "batal", "demi", "hukum",

        # 문서 마커
        "lembaran", "tambahan", "berita", "diundangkan", "ditetapkan",
        "disahkan", "ditandatangani",

        # 기타 법률 용어
        "subjek", "objek", "norma", "asas", "prinsip", "doktrin",
        "yurisdiksi", "kompetensi", "kewenangan", "delegasi", "mandat",
    }

    # 약어/두문자어
    ABBREVIATIONS = {
        "uu": "undang-undang",
        "pp": "peraturan pemerintah",
        "perpres": "peraturan presiden",
        "perppu": "peraturan pemerintah pengganti undang-undang",
        "perpu": "peraturan pemerintah pengganti undang-undang",
        "permen": "peraturan menteri",
        "perban": "peraturan badan",
        "keppres": "keputusan presiden",
        "kepmen": "keputusan menteri",
        "inpres": "instruksi presiden",
        "se": "surat edaran",
        "perda": "peraturan daerah",
        "pergub": "peraturan gubernur",
        "perbup": "peraturan bupati",
        "perwali": "peraturan walikota",
        "dpr": "dewan perwakilan rakyat",
        "dprd": "dewan perwakilan rakyat daerah",
        "mpr": "majelis permusyawaratan rakyat",
        "ri": "republik indonesia",
        "nkri": "negara kesatuan republik indonesia",
        "apbn": "anggaran pendapatan dan belanja negara",
        "apbd": "anggaran pendapatan dan belanja daerah",
    }

    # 불용어 (일반적으로 무시할 단어)
    STOPWORDS = {
        "dan", "atau", "yang", "di", "ke", "dari", "untuk", "dengan",
        "pada", "dalam", "oleh", "ini", "itu", "adalah", "sebagai",
        "tersebut", "dapat", "akan", "telah", "sudah", "belum",
        "tidak", "bukan", "jika", "bila", "apabila", "maka", "serta",
        "maupun", "atas", "bawah", "antara", "melalui", "terhadap",
        "tentang", "mengenai", "berdasarkan", "sesuai", "sebagaimana",
    }

    # 일반 인도네시아어 단어 (기본 세트 - 확장 필요)
    COMMON_WORDS = {
        # 동사
        "adalah", "ada", "akan", "bisa", "dapat", "harus", "perlu",
        "menjadi", "membuat", "memberikan", "melakukan", "menggunakan",
        "mendapat", "memiliki", "mengetahui", "melihat", "mendengar",

        # 명사
        "orang", "waktu", "tahun", "hari", "bulan", "tempat", "cara",
        "hal", "masalah", "kegiatan", "pekerjaan", "hasil", "tujuan",

        # 형용사
        "baru", "lama", "besar", "kecil", "tinggi", "rendah", "baik", "buruk",
        "penting", "utama", "khusus", "umum", "tertentu", "lain", "sama",

        # 숫자 관련
        "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan",
        "sembilan", "sepuluh", "sebelas", "puluh", "ratus", "ribu", "juta",
        "pertama", "kedua", "ketiga", "keempat", "kelima",

        # 대명사
        "saya", "kami", "kita", "anda", "mereka", "dia", "ia", "beliau",

        # 접속사/전치사
        "dan", "atau", "tetapi", "namun", "karena", "sebab", "oleh",
        "untuk", "dengan", "tanpa", "dalam", "pada", "dari", "ke",
    }

    def __init__(
        self,
        extra_legal_terms: Optional[Set[str]] = None,
        extra_common_words: Optional[Set[str]] = None,
        custom_dict_path: Optional[Path] = None,
    ):
        """
        Args:
            extra_legal_terms: 추가 법률 용어 세트
            extra_common_words: 추가 일반 단어 세트
            custom_dict_path: 커스텀 사전 파일 경로 (한 줄에 한 단어)
        """
        # Sastrawi 스테머 초기화
        self._stemmer = None
        self._stopword_remover = None
        if SASTRAWI_AVAILABLE:
            try:
                factory = StemmerFactory()
                self._stemmer = factory.create_stemmer()
                sw_factory = StopWordRemoverFactory()
                self._stopword_remover = sw_factory.create_stop_word_remover()
            except Exception as e:
                print(f"Warning: Sastrawi 초기화 실패: {e}")

        # 사전 구성
        self._legal_terms = self.LEGAL_TERMS.copy()
        self._common_words = self.COMMON_WORDS.copy()
        self._stopwords = self.STOPWORDS.copy()
        self._abbreviations = self.ABBREVIATIONS.copy()

        # 추가 용어 병합
        if extra_legal_terms:
            self._legal_terms.update(extra_legal_terms)
        if extra_common_words:
            self._common_words.update(extra_common_words)

        # 커스텀 사전 로드
        if custom_dict_path and custom_dict_path.exists():
            self._load_custom_dict(custom_dict_path)

        # 전체 유효 단어 세트 (캐시용)
        self._all_valid_words = (
            self._legal_terms |
            self._common_words |
            self._stopwords |
            set(self._abbreviations.keys())
        )

    def _load_custom_dict(self, path: Path) -> None:
        """커스텀 사전 파일 로드"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    word = line.strip().lower()
                    if word and not word.startswith('#'):
                        self._common_words.add(word)
        except Exception as e:
            print(f"Warning: 커스텀 사전 로드 실패: {e}")

    @lru_cache(maxsize=10000)
    def get_stem(self, word: str) -> str:
        """
        단어의 어근(stem) 추출

        Args:
            word: 원본 단어

        Returns:
            어근 (Sastrawi 없으면 원본 반환)
        """
        word = word.lower().strip()
        if self._stemmer:
            try:
                return self._stemmer.stem(word)
            except:
                pass
        return word

    @lru_cache(maxsize=10000)
    def is_valid_word(self, word: str) -> bool:
        """
        유효한 인도네시아어 단어인지 확인

        Args:
            word: 확인할 단어

        Returns:
            True if 유효한 단어
        """
        word = word.lower().strip()

        # 빈 문자열 또는 너무 짧은 단어
        if len(word) < 2:
            return False

        # 숫자만 있는 경우
        if word.isdigit():
            return True  # 숫자는 유효

        # 직접 사전 매칭
        if word in self._all_valid_words:
            return True

        # 어근으로 매칭
        stem = self.get_stem(word)
        if stem in self._all_valid_words:
            return True

        # 약어 확인
        if word.upper() in [a.upper() for a in self._abbreviations.keys()]:
            return True

        return False

    @lru_cache(maxsize=5000)
    def is_legal_term(self, word: str) -> bool:
        """
        법률 용어인지 확인

        Args:
            word: 확인할 단어

        Returns:
            True if 법률 용어
        """
        word = word.lower().strip()

        # 직접 매칭
        if word in self._legal_terms:
            return True

        # 어근으로 매칭
        stem = self.get_stem(word)
        if stem in self._legal_terms:
            return True

        # 약어 확인 (법령 유형)
        legal_abbrevs = {"uu", "pp", "perpres", "perppu", "perpu", "permen",
                        "perban", "keppres", "inpres", "kepmen", "perda"}
        if word.lower() in legal_abbrevs:
            return True

        return False

    def is_stopword(self, word: str) -> bool:
        """불용어 여부 확인"""
        return word.lower().strip() in self._stopwords

    def expand_abbreviation(self, abbrev: str) -> Optional[str]:
        """약어 확장"""
        return self._abbreviations.get(abbrev.lower().strip())

    def analyze_text(self, text: str) -> dict:
        """
        텍스트 분석

        Args:
            text: 분석할 텍스트

        Returns:
            분석 결과 딕셔너리
        """
        # 단어 추출 (하이픈 분리, Unicode 악센트 포함 - quality_scorer와 동일)
        normalized = re.sub(r'-', ' ', text.lower())
        words = re.findall(r'\b[a-zA-Z\u00C0-\u024F]{2,}\b', normalized)

        # 분류
        valid_words = []
        invalid_words = []
        legal_terms = []
        stopwords = []

        for word in words:
            if self.is_stopword(word):
                stopwords.append(word)
            elif self.is_legal_term(word):
                legal_terms.append(word)
                valid_words.append(word)
            elif self.is_valid_word(word):
                valid_words.append(word)
            else:
                invalid_words.append(word)

        total = len(words)
        return {
            "total_words": total,
            "valid_count": len(valid_words),
            "invalid_count": len(invalid_words),
            "legal_count": len(legal_terms),
            "stopword_count": len(stopwords),
            "valid_ratio": len(valid_words) / total if total > 0 else 0,
            "legal_ratio": len(legal_terms) / total if total > 0 else 0,
            "invalid_words": list(set(invalid_words))[:20],  # 상위 20개만
            "legal_terms": list(set(legal_terms)),
        }

    def add_legal_term(self, term: str) -> None:
        """법률 용어 추가"""
        self._legal_terms.add(term.lower().strip())
        self._all_valid_words.add(term.lower().strip())
        # 캐시 클리어
        self.is_legal_term.cache_clear()
        self.is_valid_word.cache_clear()

    def add_common_word(self, word: str) -> None:
        """일반 단어 추가"""
        self._common_words.add(word.lower().strip())
        self._all_valid_words.add(word.lower().strip())
        # 캐시 클리어
        self.is_valid_word.cache_clear()


# CLI 테스트용
if __name__ == "__main__":
    print("=== 인도네시아어 사전 테스트 ===\n")

    dict = IndonesianDictionary()

    # 단어 테스트
    test_words = [
        "undang",       # 법률 용어
        "perundangan",  # 파생어
        "PERPRES",      # 약어
        "pasal",        # 구조 용어
        "adalah",       # 일반 단어
        "xyz123",       # 무효
        "menetapkan",   # 법적 행위
    ]

    print("단어 테스트:")
    for word in test_words:
        valid = dict.is_valid_word(word)
        legal = dict.is_legal_term(word)
        stem = dict.get_stem(word)
        print(f"  {word:15} → 유효: {valid}, 법률용어: {legal}, 어근: {stem}")

    # 텍스트 분석 테스트
    test_text = """
    UNDANG-UNDANG REPUBLIK INDONESIA
    NOMOR 12 TAHUN 2011
    TENTANG PEMBENTUKAN PERATURAN PERUNDANG-UNDANGAN

    Pasal 1
    Dalam Undang-Undang ini yang dimaksud dengan Pembentukan
    Peraturan Perundang-undangan adalah pembuatan Peraturan
    Perundang-undangan yang mencakup tahapan perencanaan.
    """

    print("\n텍스트 분석:")
    result = dict.analyze_text(test_text)
    for k, v in result.items():
        print(f"  {k}: {v}")
