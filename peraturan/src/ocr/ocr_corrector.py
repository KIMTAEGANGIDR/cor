"""
ILIS OCR Corrector - 후처리 교정기

OCR 오류를 자동으로 교정하여 품질 향상
- 규칙 기반 교정: 자주 발생하는 OCR 오류 패턴
- 사전 기반 교정: 인도네시아어 법률 용어
- 문맥 기반 교정: 인도네시아어 단어 내 문자 혼동 수정
- 포맷 정규화: 띄어쓰기, 하이픈 끊김 처리

주요 OCR 오류 패턴 (실제 데이터 분석 기반):
1. O ↔ 0 혼동: 20O → 200, IND0NESIA → INDONESIA
2. l ↔ I ↔ 1 혼동: lndonesia → Indonesia, 1NDONESIA → INDONESIA
3. rn ↔ m 혼동: Pernerintah → Pemerintah
4. 과도한 띄어쓰기: 라인 브레이크 지점
5. 하이픈 끊김: Undang-\nUndang → Undang-Undang

사용법:
    from peraturan.src.ocr.ocr_corrector import OCRCorrector

    corrector = OCRCorrector()
    result = corrector.correct(ocr_text)

    # 오류 탐지만 (교정 없이)
    errors = corrector.detect_errors(ocr_text)
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Set
from enum import Enum

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """OCR 오류 유형"""
    CHAR_CONFUSION = "char_confusion"      # 문자 혼동 (O↔0, l↔I 등)
    LIGATURE = "ligature"                  # 합자 오류 (rn↔m, cl↔d 등)
    WHITESPACE = "whitespace"              # 띄어쓰기 오류
    HYPHEN_BREAK = "hyphen_break"          # 하이픈 끊김
    NUMBER_IN_WORD = "number_in_word"      # 단어 내 숫자 (PRES1DEN)
    UNKNOWN_WORD = "unknown_word"          # 사전에 없는 단어
    OLD_SPELLING = "old_spelling"          # 옛 철자법 (Ejaan Lama)
    NOISE = "noise"                        # OCR 노이즈
    HTML_TAG = "html_tag"                  # HTML 태그


@dataclass
class DetectedError:
    """탐지된 오류"""
    error_type: ErrorType
    position: int           # 텍스트 내 위치
    original: str           # 원본 문자열
    suggested: str          # 교정 제안
    confidence: float       # 신뢰도 (0-1)
    context: str = ""       # 주변 문맥


@dataclass
class CorrectionResult:
    """교정 결과"""
    original: str
    corrected: str
    corrections_made: int = 0
    correction_details: list = field(default_factory=list)

    @property
    def improvement_ratio(self) -> float:
        """교정 비율 (0-1)"""
        if not self.original:
            return 0.0
        return self.corrections_made / len(self.original.split())


class OCRCorrector:
    """OCR 오류 교정기 - 인도네시아 법률 문서 특화"""

    # ==========================================
    # 1. 문자 혼동 패턴 (가장 흔한 OCR 오류)
    # ==========================================
    # 숫자 → 문자 (단어 내에서 숫자가 나오면 문자로 교정)
    DIGIT_TO_LETTER = {
        "0": "O",  # 0 → O (IND0NESIA → INDONESIA)
        "1": "I",  # 1 → I (1NDONESIA → INDONESIA)
        "3": "E",  # 3 → E (PR3SIDEN → PRESIDEN)
        "4": "A",  # 4 → A (드물게)
        "5": "S",  # 5 → S (INDONE5IA → INDONESIA)
        "6": "G",  # 6 → G (드물게)
        "7": "T",  # 7 → T (7AHUN → TAHUN)
        "8": "B",  # 8 → B (드물게)
    }

    # 문자 → 숫자 (숫자 문맥에서 문자가 나오면 숫자로 교정)
    LETTER_TO_DIGIT = {
        "O": "0",  # O → 0 (20O → 200)
        "o": "0",  # o → 0
        "I": "1",  # I → 1 (괄호 안: (I) → (1))
        "l": "1",  # l → 1 (소문자 L)
        "S": "5",  # S → 5 (드물게)
        "B": "8",  # B → 8 (드물게)
        "G": "6",  # G → 6 (드물게)
        "T": "7",  # T → 7 (드물게)
    }

    # 특수문자 → 문자
    SPECIAL_TO_LETTER = {
        "!": "I",   # ! → I (PRES!DEN → PRESIDEN)
        "|": "I",   # | → I
        "[": "I",   # [ → I (드물게)
        "]": "I",   # ] → I (드물게)
        "/": "I",   # / → I (드물게, 기울어진 I)
    }

    # 합자 오류 (ligature confusion)
    # 주의: 너무 공격적인 패턴은 오탐 유발 (li→h 제거됨)
    LIGATURE_PATTERNS = {
        "rn": "m",   # rn → m (Pernerintah → Pemerintah) - 가장 흔함
        "cl": "d",   # cl → d (Unclang → Undang)
        "vv": "w",   # vv → w (Lavv → Law)
        # "li": "h",  # 제거 - 오탐 발생 (SALINAN → SAHNAN)
        # "ii": "n",  # 제거 - 오탐 발생
    }

    # ==========================================
    # 옛 인도네시아어 철자법 (Ejaan Lama, 1947-1972)
    # → 현대 철자법 (Ejaan Yang Disempurnakan, 1972~)
    # ==========================================
    OLD_SPELLING_PATTERNS = [
        # 자음 변환 (순서 중요 - 긴 패턴 먼저)
        (r"tj", "c"),     # tjara → cara, tjatatan → catatan
        (r"dj", "j"),     # Djuli → Juli, djumlah → jumlah, djabatan → jabatan
        (r"nj", "ny"),    # njalah → nyalah, njata → nyata
        (r"sj", "sy"),    # sjarat → syarat, sjah → syah
        (r"ch", "kh"),    # chabar → khabar (드물게)
        # j → y: 패턴 규칙 제거 (과적용 문제)
        # - "Djenderal" → "Jenderal" 후 "Yenderal"로 오변환됨
        # - j→y 변환은 OLD_SPELLING_WORDS 사전 기반으로만 처리
        # (r"(?<![dnt])j(?!u)", "y"),  # 비활성화 - 오탐 발생
        # oe → u (1947년 이전 철자법, 매우 오래된 문서)
        (r"oe", "u"),     # soedah → sudah, boelan → bulan
    ]

    # 옛 철자법 단어 매핑 (확장 사전 - 300+ 단어)
    # 1947-1972년 문서 처리를 위한 포괄적 사전
    OLD_SPELLING_WORDS = {
        # ==========================================
        # dj → j (가장 흔한 패턴)
        # ==========================================
        "djuga": "juga",
        "djadi": "jadi",
        "djalan": "jalan",
        "djasa": "jasa",
        "djawab": "jawab",
        "djumlah": "jumlah",
        "djabatan": "jabatan",
        "djenderal": "jenderal",
        "djakarta": "jakarta",
        "djanuari": "januari",
        "djuli": "juli",
        "djuni": "juni",
        # 추가 dj→j 단어들 (1950-70년대 문서 빈출)
        "djaminan": "jaminan",
        "djawatan": "jawatan",
        "djurusan": "jurusan",
        "djarak": "jarak",
        "djauh": "jauh",
        "djelas": "jelas",
        "djiwa": "jiwa",
        "djodjo": "jojo",
        "djual": "jual",
        "djumat": "jumat",
        "djurang": "jurang",
        "djuru": "juru",
        "djurnal": "jurnal",
        "djustru": "justru",
        "djuta": "juta",
        "djenis": "jenis",
        "djedjak": "jejak",
        "djerit": "jerit",
        "djuru": "juru",
        "djuara": "juara",
        "djiwasraja": "jiwasraya",
        "djarum": "jarum",
        "djaga": "jaga",
        "djahat": "jahat",
        "djahit": "jahit",
        "djaksa": "jaksa",
        "djam": "jam",
        "djaman": "jaman",
        "djambi": "jambi",
        "djamu": "jamu",
        "djanda": "janda",
        "djangan": "jangan",
        "djangkar": "jangkar",
        "djangkit": "jangkit",
        "djanji": "janji",
        "djantung": "jantung",
        "djari": "jari",
        "djas": "jas",
        "djatah": "jatah",
        "djatim": "jatim",
        "djateng": "jateng",
        "djabar": "jabar",
        "djawa": "jawa",
        "djawaban": "jawaban",
        "djebak": "jebak",
        "djedjer": "jejer",
        "djelak": "jelak",
        "djelang": "jelang",
        "djelaskan": "jelaskan",
        "djelek": "jelek",
        "djeli": "jeli",
        "djelita": "jelita",
        "djelmaan": "jelmaan",
        "djembatan": "jembatan",
        "djemur": "jemur",
        "djenaka": "jenaka",
        "djenazah": "jenazah",
        "djendela": "jendela",
        "djengkal": "jengkal",
        "djengkel": "jengkel",
        "djenis": "jenis",
        "djentera": "jentera",
        "djepang": "jepang",
        "djepit": "jepit",
        "djerami": "jerami",
        "djerih": "jerih",
        "djeritan": "jeritan",
        "djernih": "jernih",
        "djeruji": "jeruji",
        "djeruk": "jeruk",

        # ==========================================
        # tj → c
        # ==========================================
        "tjara": "cara",
        "tjatatan": "catatan",
        "tjukup": "cukup",
        "tjap": "cap",
        "tjabang": "cabang",
        "tjabut": "cabut",
        "tjahaja": "cahaya",
        "tjakar": "cakar",
        "tjakap": "cakap",
        "tjakra": "cakra",
        "tjakup": "cakup",
        "tjalon": "calon",
        "tjampur": "campur",
        "tjangkul": "cangkul",
        "tjantik": "cantik",
        "tjap": "cap",
        "tjapai": "capai",
        "tjari": "cari",
        "tjat": "cat",
        "tjatur": "catur",
        "tjedera": "cedera",
        "tjek": "cek",
        "tjekal": "cekal",
        "tjelaka": "celaka",
        "tjelana": "celana",
        "tjempaka": "cempaka",
        "tjendekiawan": "cendekiawan",
        "tjenderung": "cenderung",
        "tjengkeh": "cengkeh",
        "tjepat": "cepat",
        "tjerai": "cerai",
        "tjeramah": "ceramah",
        "tjerita": "cerita",
        "tjermat": "cermat",
        "tjermin": "cermin",
        "tjipta": "cipta",
        "tjiri": "ciri",
        "tjita": "cita",
        "tjium": "cium",
        "tjoba": "coba",
        "tjocok": "cocok",
        "tjoklat": "coklat",
        "tjontoh": "contoh",
        "tjuaca": "cuaca",
        "tjubit": "cubit",
        "tjuci": "cuci",
        "tjucu": "cucu",
        "tjukur": "cukur",
        "tjuma": "cuma",
        "tjurang": "curang",
        "tjuri": "curi",
        "tjuti": "cuti",

        # ==========================================
        # nj → ny
        # ==========================================
        "njata": "nyata",
        "njaris": "nyaris",
        "njala": "nyala",
        "njaman": "nyaman",
        "njamuk": "nyamuk",
        "njani": "nyanyi",
        "njata": "nyata",
        "njawa": "nyawa",
        "njeri": "nyeri",
        "njiur": "nyiur",

        # ==========================================
        # sj → sy
        # ==========================================
        "sjarat": "syarat",
        "sjah": "syah",
        "sjahrir": "syahrir",
        "sjair": "syair",
        "sjaitan": "syaitan",
        "sjarikat": "syarikat",
        "sjaraf": "syaraf",
        "sjukur": "syukur",

        # ==========================================
        # j → y (단어 시작 위치에서만, 사전 기반)
        # 주의: 패턴 규칙 대신 사전으로만 처리 (과적용 방지)
        # j 유지 단어들 (변환하면 안됨):
        # juga, jadi, jalan, jabatan, jenderal, januari, juli, juni
        # → 이것들은 현대 철자법에서도 j 유지
        # ==========================================
        "jang": "yang",
        "jaitu": "yaitu",
        "jogjakarta": "yogyakarta",
        "jogja": "yogya",

        # ==========================================
        # oe → u (1947년 이전 철자법, 매우 오래된 문서)
        # ==========================================
        "oemoem": "umum",
        "boekoe": "buku",
        "koempoelan": "kumpulan",
        "soedah": "sudah",
        "boelan": "bulan",
        "oendang": "undang",
        "peratoeran": "peraturan",
        "pemerintahoe": "pemerintahu",
        "negroe": "negru",
        "oetara": "utara",
        "oentoe": "untu",
        "oepaja": "upaya",
        "oebah": "ubah",
        "toedjoe": "tuju",
        "toelis": "tulis",
        "toenggoek": "tunggu",
        "poetoes": "putus",
        "poetoesan": "putusan",
        "koetip": "kutip",
        "koetipan": "kutipan",
        "boemi": "bumi",
        "boemipoetra": "bumiputra",
        "roemah": "rumah",
        "moeda": "muda",
        "goeroe": "guru",
        "toea": "tua",
        "doeloe": "dulu",
        "baroe": "baru",
        "tahoen": "tahun",

        # ==========================================
        # 복합 패턴 (dj, tj 포함 단어)
        # ==========================================
        "wadjib": "wajib",
        "dikundjungi": "dikunjungi",
        "perdjalanan": "perjalanan",
        "pendjualan": "penjualan",
        "keadjaiban": "keajaiban",
        "menghadliri": "menghadiri",  # dl → d (드물게)
        "sedjak": "sejak",
        "kedjadian": "kejadian",
        "pekerdjaan": "pekerjaan",
        "pendjelasan": "penjelasan",
        "ketentjuan": "ketentuan",
        "mentjapai": "mencapai",
        "pertjaja": "percaya",
        "berdjandji": "berjanji",
        "mendjadi": "menjadi",
        "mendjaga": "menjaga",
        "mendjual": "menjual",
        "mendjawab": "menjawab",
        "mendjelaskan": "menjelaskan",
        "pendjara": "penjara",
        "pendjagaan": "penjagaan",
        "kedjaksaan": "kejaksaan",
        "terdjadi": "terjadi",
        "terdjamin": "terjamin",
        "terdjangkau": "terjangkau",
        "berdjalan": "berjalan",
        "berdjuang": "berjuang",
        "peradjurit": "prajurit",
        "madjoe": "maju",
        "madjikan": "majikan",
        "padjak": "pajak",
        "badjak": "bajak",
        "berdjakarta": "berjakarta",
        "didjadikan": "dijadikan",
        "didjaga": "dijaga",
        "didjamin": "dijamin",
        "didjual": "dijual",
        "didjawab": "dijawab",
        "menindjau": "meninjau",
        "penindjau": "peninjau",
        "peniladjaran": "penilajaran",
        "pengadjaran": "pengajaran",
        "peladjaran": "pelajaran",
        "peladjar": "pelajar",
        "beladjar": "belajar",
        "mengadjar": "mengajar",
        "adjaran": "ajaran",
        "pengadji": "pengaji",
        "mengadji": "mengaji",
        "nedjis": "najis",
        "sedjahtera": "sejahtera",
        "kesedjahteraan": "kesejahteraan",
        "mendjerat": "menjerat",
        "djerat": "jerat",
        "djero": "jero",
        "djatuh": "jatuh",
        "djatuhan": "jatuhan",
        "kedjatuhan": "kejatuhan",
        "pengadilandjaksaan": "pengadilanjaksaan",
        "pentjatatan": "pencatatan",
        "pertjobaan": "percobaan",
        "pentjurian": "pencurian",
        "pertjuma": "percuma",

        # ==========================================
        # 법률 문서 특화 단어
        # ==========================================
        "oendang-oendang": "undang-undang",
        "peratoeran": "peraturan",
        "kepoetoesan": "keputusan",
        "penetapanpemerintah": "penetapanpemerintah",
        "instruksipresiden": "instruksipresiden",
        "ketentoean": "ketentuan",
        "peralihan": "peralihan",
        "penoetoep": "penutup",
        "menimbangbahwa": "menimbangbahwa",
        "mengingat": "mengingat",
        "memoetoes": "memutus",
        "memoetoekan": "memutuskan",
        "menetapkanbahwa": "menetapkanbahwa",
        "koersi": "kursi",
        "sidang": "sidang",
        "pengadilan": "pengadilan",
        "hakim": "hakim",
        "djaksa": "jaksa",
        "terdakwa": "terdakwa",
        "saksi": "saksi",
        "boekti": "bukti",
        "salinan": "salinan",
        "lampiran": "lampiran",
    }

    # ==========================================
    # 보호 단어 목록 (합자 변환 제외)
    # li→h, ii→n 같은 합자 패턴을 적용하면 안 되는 단어들
    # ==========================================
    PROTECTED_LIGATURE_WORDS = {
        # 법률 용어 (li 패턴 포함)
        "salinan", "lampiran", "kualifikasi", "modifikasi",
        "identifikasi", "klasifikasi", "ratifikasi", "verifikasi",
        "spesifikasi", "diversifikasi", "simplifikasi", "amplifikasi",
        "berlian", "dalil", "dalili", "amali", "asli",
        "kali", "perwakilan", "kabupaten", "kecamatan",
        "kelurahan", "desa", "provinsi", "nasional",
        "internasional", "regional", "legal", "ilegal",
        "lintas", "melintasi", "silinder", "militer",
        "sipil", "terlibat", "melibatkan", "berlaku",
        "politik", "poliisi", "kriminal", "minimal",
        "maksimal", "optimal", "final", "terminal",
        "original", "marginal", "nominal", "formal",
        "informal", "normal", "abnormal", "liberal",
        "bilateral", "multilateral", "unilateral",
    }

    # ==========================================
    # OCR 노이즈 패턴 (의미 없는 문자열)
    # ==========================================
    NOISE_PATTERNS = [
        # 의미 없는 특수문자 조합
        r"[\$\+\-\*\#\@\!\%\^\&\=]{2,}",  # $$, ++, --, **, ##, @@ 등
        r"[\°\±\×\÷\¥\€\£]{1,}",           # 통화/수학 기호
        r"\d+\.\d+[\°\%]",                 # 33.33%, ±1.5° 등 (문맥 없는)
        r"(?<!\w)[a-zA-Z]\s*[\+\-\*]\s*[a-zA-Z](?!\w)",  # a + b, x - y 등
        r"più",                            # 이탈리아어 노이즈
        r"STATE\s*S",                      # OCR 노이즈
        r"N\s*\d+\.\s*\$\d+",              # N 15. $200 형태
        r"ay\s*±[\d\.\,]+",                # ay ±1,5 형태
        # 불필요한 점선/대시 라인
        r"[\.\-\=\_]{5,}",                 # ..... ----- ===== _____
        r"(?:--\s*){2,}",                  # -- -- -- -- 반복
    ]

    # HTML 태그 패턴
    HTML_TAG_PATTERN = re.compile(r"</?(?:br|p|div|span|b|i|u|strong|em)[^>]*>", re.IGNORECASE)

    # ==========================================
    # 2. 인도네시아어 핵심 단어 (문맥 교정용)
    # ==========================================
    # 이 단어들은 문자 혼동 교정 시 참조로 사용됨
    INDONESIAN_WORDS = {
        # 국가/정부 관련
        "indonesia", "republik", "presiden", "pemerintah", "negara",
        "menteri", "gubernur", "bupati", "walikota", "daerah",
        # 법률 문서 유형
        "undang", "peraturan", "keputusan", "instruksi", "penetapan",
        "perpres", "perppu", "permen", "keppres", "inpres",
        # 법률 구조
        "pasal", "ayat", "huruf", "angka", "bab", "bagian", "paragraf",
        "ketentuan", "umum", "penutup", "peralihan",
        # 법률 행위
        "menimbang", "mengingat", "memutuskan", "menetapkan",
        "menyatakan", "memerintahkan", "mengatur", "melarang",
        # 일반 용어
        "tentang", "dengan", "dalam", "untuk", "bahwa", "sebagai",
        "terhadap", "lembaga", "badan", "nomor", "tahun",
        "salinan", "lampiran", "penjelasan", "perubahan",
        "tuhan", "rahmat", "berkat", "atas",
        # 동사/형용사
        "adalah", "dapat", "harus", "wajib", "tidak", "atau", "dan",
        "setiap", "semua", "lain", "tersebut", "sebagaimana",
    }

    # ==========================================
    # 3. 인도네시아 법률 핵심 단어 교정 사전
    # ==========================================
    LEGAL_WORD_CORRECTIONS = {
        # 국가/정부 관련
        "PRES!DEN": "PRESIDEN",
        "PRESIDEN": "PRESIDEN",  # 정상
        "PRES1DEN": "PRESIDEN",
        "PRESID3N": "PRESIDEN",
        "REPUB1IK": "REPUBLIK",
        "REPIJBUK": "REPUBLIK",
        "REPUB!IK": "REPUBLIK",
        "REPUBL1K": "REPUBLIK",
        "REPUBLIC": "REPUBLIK",
        "1NDONESIA": "INDONESIA",
        "INDONE5IA": "INDONESIA",
        "INDONES1A": "INDONESIA",
        "INDONES!A": "INDONESIA",
        "INDONE!3IA": "INDONESIA",
        "INDONESTA": "INDONESIA",
        "INDONESLA": "INDONESIA",
        "PEMER1NTAH": "PEMERINTAH",
        "PEMERIN7AH": "PEMERINTAH",
        "NEGARA": "NEGARA",
        "N3GARA": "NEGARA",

        # 법률 문서 유형
        "UNDANG-UNDANG": "UNDANG-UNDANG",
        "UNDANG—UNDANG": "UNDANG-UNDANG",
        "UNDANG_UNDANG": "UNDANG-UNDANG",
        "UNDANGUNDANG": "UNDANG-UNDANG",
        "PERATURAN": "PERATURAN",
        "PERA7URAN": "PERATURAN",
        "PERAIURAN": "PERATURAN",
        "KEPUTUSAN": "KEPUTUSAN",
        "KEPUIUSAN": "KEPUTUSAN",
        "KEPU7USAN": "KEPUTUSAN",
        "INSTRUKSI": "INSTRUKSI",
        "1NSTRUKSI": "INSTRUKSI",
        "PENETAPAN": "PENETAPAN",
        "PENE7APAN": "PENETAPAN",

        # 법률 구조 용어
        "PASAL": "PASAL",
        "PASA1": "PASAL",
        "AYAT": "AYAT",
        "AYA7": "AYAT",
        "HURUF": "HURUF",
        "ANGKA": "ANGKA",
        "BAGIAN": "BAGIAN",
        "BAG1AN": "BAGIAN",

        # 법률 행위 용어
        "MENIMBANG": "MENIMBANG",
        "MEN1MBANG": "MENIMBANG",
        "MENGINGAT": "MENGINGAT",
        "MENG1NGAT": "MENGINGAT",
        "MEMUTUSKAN": "MEMUTUSKAN",
        "MEMU7USKAN": "MEMUTUSKAN",
        "MENETAPKAN": "MENETAPKAN",
        "MENE7APKAN": "MENETAPKAN",

        # 일반 법률 용어
        "KETENTUAN": "KETENTUAN",
        "KE7EN7UAN": "KETENTUAN",
        "PELAKSANAAN": "PELAKSANAAN",
        "PELAKSANA": "PELAKSANA",
        "PERUBAHAN": "PERUBAHAN",
        "PENJELASAN": "PENJELASAN",
        "LAMPIRAN": "LAMPIRAN",
        "LAMP1RAN": "LAMPIRAN",
        "SALINAN": "SALINAN",
        "SAL1NAN": "SALINAN",
        "TENTANG": "TENTANG",
        "7ENTANG": "TENTANG",
        "DENGAN": "DENGAN",
        "DALAM": "DALAM",
        "UNTUK": "UNTUK",
        "UN7UK": "UNTUK",
        "BAHWA": "BAHWA",
        "SEBAGAI": "SEBAGAI",
        "SEBAGA1": "SEBAGAI",
        "TERHADAP": "TERHADAP",
        "7ERHADAP": "TERHADAP",
        "MENTERI": "MENTERI",
        "MENTER1": "MENTERI",
        "MEN7ERI": "MENTERI",
        "LEMBAGA": "LEMBAGA",
        "BADAN": "BADAN",

        # 숫자 관련
        "NOMOR": "NOMOR",
        "N0MOR": "NOMOR",
        "NOM0R": "NOMOR",
        "TAHUN": "TAHUN",
        "7AHUN": "TAHUN",

        # 종교/철학적 표현
        "TUHAN": "TUHAN",
        "7UHAN": "TUHAN",
        "RAHMAT": "RAHMAT",
        "RAHMA7": "RAHMAT",
    }

    # ==========================================
    # 4. 정규식 기반 패턴 교정
    # ==========================================
    REGEX_CORRECTIONS = [
        # === 숫자 내 문자 혼동 ===
        # 연도 교정 (19xx, 20xx) - O/o → 0
        (r"\b(19|20)([Oo])(\d)\b", lambda m: f"{m.group(1)}0{m.group(3)}"),      # 20O4 → 2004
        (r"\b(19|20)(\d)([Oo])\b", lambda m: f"{m.group(1)}{m.group(2)}0"),      # 201O → 2010
        (r"\b(19|20)([Oo])([Oo])\b", lambda m: f"{m.group(1)}00"),               # 20OO → 2000
        # 숫자 중간의 O → 0 (3자리 이상 숫자)
        (r"\b(\d+)O(\d+)\b", lambda m: f"{m.group(1)}0{m.group(2)}"),            # 20O → 200
        (r"\b(\d)O(\d)\b", lambda m: f"{m.group(1)}0{m.group(2)}"),              # 1O1 → 101
        # 숫자 시작/끝의 O → 0
        (r"\bO(\d{2,})\b", lambda m: f"0{m.group(1)}"),                          # O123 → 0123
        (r"\b(\d{2,})O\b", lambda m: f"{m.group(1)}0"),                          # 123O → 1230

        # === 법률 구조 패턴 ===
        # BAB 로마숫자 교정 (1/|/! → I)
        (r"(?i)\bBAB\s+([1|!]+)\b", lambda m: f"BAB {m.group(1).replace('1', 'I').replace('|', 'I').replace('!', 'I')}"),
        # Pasal 번호 교정 (앞에 O/o가 붙으면 제거)
        (r"(?i)\bPasal\s+[Oo](\d+)\b", lambda m: f"Pasal {m.group(1)}"),         # Pasal O1 → Pasal 1
        # Ayat 괄호 교정
        (r"(?i)\bAyat\s*\(([Oo])\)", r"Ayat (0)"),                               # Ayat (O) → Ayat (0) (드물지만)

        # === 괄호 안 숫자 교정 ===
        # (I) → (1) 단, ayat/huruf 뒤에서만 (로마숫자 BAB I 는 제외)
        (r"(?i)(?:ayat|huruf)\s*\(([Il|!1])\)", lambda m: f"({m.group(1).replace('I', '1').replace('l', '1').replace('|', '1').replace('!', '1')})"),

        # === 하이픈/대시 정규화 ===
        (r"[—–−]", "-"),  # em-dash, en-dash, minus → hyphen

        # === 띄어쓰기 정규화 ===
        # 연속 공백 → 단일 공백
        (r"[ \t]{2,}", " "),
        # 하이픈 전후 불필요한 공백 제거 (UNDANG - UNDANG → UNDANG-UNDANG)
        (r"(\w)\s*-\s*(\w)", r"\1-\2"),
    ]

    # ==========================================
    # 5. 하이픈 끊김 패턴 (줄바꿈 시 발생)
    # ==========================================
    # 하이픈으로 끝나는 줄 + 다음 줄 시작을 연결
    HYPHEN_BREAK_PATTERN = re.compile(r"(\w+)-\s*\n\s*(\w+)")

    # ==========================================
    # 6. 흔한 OCR 오류 단어 쌍 (오류 → 정답)
    # ==========================================
    COMMON_OCR_ERRORS = {
        # rn → m 오류
        "pernerintah": "pemerintah",
        "pernbangunan": "pembangunan",
        "kernenterian": "kementerian",
        "rnasyarakat": "masyarakat",
        "pernbentukan": "pembentukan",
        "rnenetapkan": "menetapkan",
        "rnengingat": "mengingat",
        "rnenimbang": "menimbang",
        "rnenteri": "menteri",
        "pernberlakuan": "pemberlakuan",
        # cl → d 오류
        "unclang": "undang",
        "keaclilan": "keadilan",
        "pencliclikan": "pendidikan",
        "keputusclan": "keputusan",
        "clengan": "dengan",
        "claiam": "dalam",
        # vv → w 오류
        "lavv": "law",
        "nevv": "new",
        "wvajib": "wajib",
        # l → I 오류 (단어 시작)
        "lndonesia": "indonesia",
        "lnstruksi": "instruksi",
        "lnpres": "inpres",
        # 기타 흔한 오류
        "republlk": "republik",
        "presiclen": "presiden",
        "repub1ik": "republik",
        "repub|ik": "republik",
    }

    def __init__(
        self,
        use_dictionary: bool = True,
        use_regex: bool = True,
        use_context: bool = True,
        use_ligature_fix: bool = True,
        use_hyphen_fix: bool = True,
        use_old_spelling_fix: bool = True,
        use_noise_filter: bool = True,
        use_html_filter: bool = True,
        use_conditional_ligature: bool = True,  # 조건부 합자 교정
    ):
        self.use_dictionary = use_dictionary
        self.use_regex = use_regex
        self.use_context = use_context
        self.use_ligature_fix = use_ligature_fix
        self.use_hyphen_fix = use_hyphen_fix
        self.use_old_spelling_fix = use_old_spelling_fix
        self.use_noise_filter = use_noise_filter
        self.use_html_filter = use_html_filter
        self.use_conditional_ligature = use_conditional_ligature

        # 대소문자 무시 사전 구축
        self._word_corrections_lower = {
            k.lower(): v for k, v in self.LEGAL_WORD_CORRECTIONS.items()
        }

        # 흔한 OCR 오류 사전 (소문자)
        self._common_errors_lower = {
            k.lower(): v.lower() for k, v in self.COMMON_OCR_ERRORS.items()
        }

        # 옛 철자법 사전 (소문자)
        self._old_spelling_lower = {
            k.lower(): v.lower() for k, v in self.OLD_SPELLING_WORDS.items()
        }

        # 인도네시아어 단어 세트 (빠른 검색용)
        self._indonesian_words_set = set(self.INDONESIAN_WORDS)

        # 보호 단어 세트 (합자 변환 제외)
        self._protected_ligature_words = set(
            w.lower() for w in self.PROTECTED_LIGATURE_WORDS
        )

        # 정규식 컴파일
        self._compiled_regex = []
        for pattern, replacement in self.REGEX_CORRECTIONS:
            try:
                compiled = re.compile(pattern)
                self._compiled_regex.append((compiled, replacement))
            except re.error as e:
                logger.warning(f"정규식 컴파일 실패: {pattern} - {e}")

        # 옛 철자법 정규식 컴파일
        self._compiled_old_spelling = []
        for pattern, replacement in self.OLD_SPELLING_PATTERNS:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                self._compiled_old_spelling.append((compiled, replacement))
            except re.error as e:
                logger.warning(f"옛 철자법 정규식 컴파일 실패: {pattern} - {e}")

        # 노이즈 패턴 정규식 컴파일
        self._compiled_noise = []
        for pattern in self.NOISE_PATTERNS:
            try:
                compiled = re.compile(pattern)
                self._compiled_noise.append(compiled)
            except re.error as e:
                logger.warning(f"노이즈 패턴 컴파일 실패: {pattern} - {e}")

    # 줄바꿈 보존용 마커 (일반 텍스트에 나타나지 않는 문자열)
    _NEWLINE_MARKER = "\x00NL\x00"

    def correct(self, text: str) -> CorrectionResult:
        """
        OCR 텍스트 교정

        Args:
            text: OCR 추출 텍스트

        Returns:
            CorrectionResult
        """
        if not text:
            return CorrectionResult(original="", corrected="", corrections_made=0)

        original = text
        corrected = text
        corrections = []

        # 0. HTML 태그 제거 (가장 먼저)
        if self.use_html_filter:
            corrected, html_corrections = self._remove_html_tags(corrected)
            corrections.extend(html_corrections)

        # 1. 하이픈 끊김 수정 (줄바꿈 처리) - 줄바꿈 마커 전에 처리
        if self.use_hyphen_fix:
            corrected, hyphen_corrections = self._fix_hyphen_breaks(corrected)
            corrections.extend(hyphen_corrections)

        # === 줄바꿈 보존: 마커로 대체 ===
        # split()+join() 패턴에서 줄바꿈이 손실되는 것을 방지
        corrected = corrected.replace("\n", self._NEWLINE_MARKER)

        # 2. 노이즈 필터링 (의미 없는 문자열 제거)
        if self.use_noise_filter:
            corrected, noise_corrections = self._filter_noise(corrected)
            corrections.extend(noise_corrections)

        # 3. 정규식 기반 패턴 교정 (띄어쓰기, 숫자-문자 혼동)
        if self.use_regex:
            corrected, regex_corrections = self._apply_regex_corrections(corrected)
            corrections.extend(regex_corrections)

        # 4. 합자 오류 교정 (rn→m, cl→d)
        if self.use_ligature_fix:
            corrected, ligature_corrections = self._fix_ligature_errors(corrected)
            corrections.extend(ligature_corrections)

        # 5. 사전 기반 단어 교정 (명시적 오류→정답)
        if self.use_dictionary:
            corrected, dict_corrections = self._apply_dictionary_corrections(corrected)
            corrections.extend(dict_corrections)

        # 6. 문맥 기반 교정 (단어 내 숫자→문자)
        if self.use_context:
            corrected, context_corrections = self._fix_numbers_in_words(corrected)
            corrections.extend(context_corrections)

        # 7. 옛 철자법 변환 (Ejaan Lama → Ejaan Yang Disempurnakan)
        if self.use_old_spelling_fix:
            corrected, old_spelling_corrections = self._fix_old_spelling(corrected)
            corrections.extend(old_spelling_corrections)

        # === 줄바꿈 복원: 마커를 줄바꿈으로 ===
        corrected = corrected.replace(self._NEWLINE_MARKER, "\n")

        return CorrectionResult(
            original=original,
            corrected=corrected,
            corrections_made=len(corrections),
            correction_details=corrections,
        )

    def _fix_hyphen_breaks(self, text: str) -> tuple[str, list]:
        """
        하이픈 끊김 수정 (줄바꿈 시 발생하는 단어 분리)

        예: Undang-\nUndang → Undang-Undang
        """
        corrections = []

        def replace_func(match):
            before = match.group(1)
            after = match.group(2)
            corrections.append({
                "type": "hyphen_break",
                "original": f"{before}-\\n{after}",
                "corrected": f"{before}-{after}",
            })
            return f"{before}-{after}"

        corrected = self.HYPHEN_BREAK_PATTERN.sub(replace_func, text)
        return corrected, corrections

    def _remove_html_tags(self, text: str) -> tuple[str, list]:
        """
        HTML 태그 제거

        예: <br> → 줄바꿈, <b>text</b> → text
        """
        corrections = []

        # <br> 태그를 줄바꿈으로 변환
        br_pattern = re.compile(r"<br\s*/?>", re.IGNORECASE)
        br_matches = br_pattern.findall(text)
        if br_matches:
            text = br_pattern.sub("\n", text)
            corrections.append({
                "type": "html_tag",
                "original": "<br>",
                "corrected": "\\n",
                "count": len(br_matches),
            })

        # 다른 HTML 태그 제거
        other_matches = self.HTML_TAG_PATTERN.findall(text)
        if other_matches:
            text = self.HTML_TAG_PATTERN.sub("", text)
            corrections.append({
                "type": "html_tag",
                "original": "HTML tags",
                "corrected": "(removed)",
                "count": len(other_matches),
            })

        return text, corrections

    def _filter_noise(self, text: str) -> tuple[str, list]:
        """
        OCR 노이즈 필터링 (의미 없는 문자열 제거)

        예: $200 $400 $, ±1,5°, -- -- -- 등
        """
        corrections = []
        corrected = text

        for pattern in self._compiled_noise:
            matches = pattern.findall(corrected)
            if matches:
                corrected = pattern.sub(" ", corrected)
                for match in matches:
                    corrections.append({
                        "type": "noise",
                        "original": match if isinstance(match, str) else str(match),
                        "corrected": "(removed)",
                    })

        # 연속 공백 정리
        corrected = re.sub(r" {2,}", " ", corrected)

        return corrected, corrections

    def _fix_old_spelling(self, text: str) -> tuple[str, list]:
        """
        옛 인도네시아어 철자법 변환 (Ejaan Lama → Ejaan Yang Disempurnakan)

        1947-1972년 문서에서 사용된 철자법:
        - dj → j (Djuli → Juli)
        - tj → c (tjara → cara)
        - nj → ny (njata → nyata)
        - sj → sy (sjarat → syarat)
        - j → y (jang → yang, 특정 위치에서)
        - oe → u (soedah → sudah, 1947년 이전)
        """
        corrections = []
        corrected = text

        # 1. 단어 단위 사전 기반 변환 (정확도 높음)
        words = corrected.split()
        corrected_words = []

        for word in words:
            prefix, core, suffix = self._split_punctuation(word)
            core_lower = core.lower()

            # 사전에서 찾기
            if core_lower in self._old_spelling_lower:
                new_core = self._old_spelling_lower[core_lower]
                preserved = self._preserve_case(core, new_core)
                corrections.append({
                    "type": "old_spelling_word",
                    "original": core,
                    "corrected": preserved,
                })
                core = preserved

            corrected_words.append(prefix + core + suffix)

        corrected = " ".join(corrected_words)

        # 2. 정규식 기반 패턴 변환 (사전에 없는 단어 처리)
        for pattern, replacement in self._compiled_old_spelling:
            matches = pattern.findall(corrected)
            if matches:
                # 대소문자 보존 치환
                def replace_with_case(match):
                    original = match.group(0)
                    if original.isupper():
                        return replacement.upper()
                    elif original[0].isupper():
                        return replacement.capitalize()
                    else:
                        return replacement

                new_corrected = pattern.sub(replace_with_case, corrected)
                if new_corrected != corrected:
                    for match in matches:
                        corrections.append({
                            "type": "old_spelling_pattern",
                            "pattern": pattern.pattern,
                            "original": match if isinstance(match, str) else match,
                            "corrected": replacement,
                        })
                    corrected = new_corrected

        return corrected, corrections

    def _fix_ligature_errors(self, text: str) -> tuple[str, list]:
        """
        합자 오류 교정 (rn→m, cl→d 등)

        인도네시아어 단어 내에서만 적용
        보호 단어(PROTECTED_LIGATURE_WORDS)는 교정 제외
        """
        corrections = []
        words = text.split()
        corrected_words = []

        for word in words:
            # 구두점 분리
            prefix, core, suffix = self._split_punctuation(word)
            core_lower = core.lower()

            # 보호 단어 확인 - 합자 변환 제외
            if self._is_protected_word(core_lower):
                corrected_words.append(prefix + core + suffix)
                continue

            # 흔한 OCR 오류 사전에서 찾기
            if core_lower in self._common_errors_lower:
                correct_word = self._common_errors_lower[core_lower]
                # 대소문자 유지
                preserved = self._preserve_case(core, correct_word)
                corrections.append({
                    "type": "ligature",
                    "original": core,
                    "corrected": preserved,
                })
                core = preserved
            else:
                # 사전에 없으면 합자 패턴 직접 적용 시도
                # 단, 조건부 합자 교정이 활성화된 경우 사전 검증 수행
                new_core = core
                for wrong, correct in self.LIGATURE_PATTERNS.items():
                    if wrong in core_lower:
                        # 조건부 합자 교정: 변환 결과가 사전에 있는 경우만 적용
                        if self.use_conditional_ligature:
                            test_core = self._replace_preserving_case(new_core, wrong, correct)
                            if self._is_valid_correction(test_core.lower()):
                                new_core = test_core
                        else:
                            # 무조건 적용 (기존 동작)
                            new_core = self._replace_preserving_case(new_core, wrong, correct)

                if new_core != core:
                    corrections.append({
                        "type": "ligature_pattern",
                        "original": core,
                        "corrected": new_core,
                    })
                    core = new_core

            corrected_words.append(prefix + core + suffix)

        return " ".join(corrected_words), corrections

    def _is_protected_word(self, word_lower: str) -> bool:
        """보호 단어 여부 확인 (합자 변환 제외 대상)"""
        # 정확히 일치
        if word_lower in self._protected_ligature_words:
            return True
        # 접두사/접미사 포함 단어도 보호 (예: "disalinan", "lampirannya")
        for protected in self._protected_ligature_words:
            if protected in word_lower and len(word_lower) <= len(protected) + 5:
                return True
        return False

    def _is_valid_correction(self, word_lower: str) -> bool:
        """교정 결과가 유효한 단어인지 확인"""
        # 인도네시아어 사전 확인
        if word_lower in self._indonesian_words_set:
            return True
        # 법률 용어 사전 확인
        if word_lower in self._word_corrections_lower:
            return True
        # 흔한 OCR 오류 사전의 정답 단어인지 확인
        if word_lower in self._common_errors_lower.values():
            return True
        return False

    def _replace_preserving_case(self, text: str, old: str, new: str) -> str:
        """대소문자 유지하며 치환"""
        result = text
        # 소문자 버전
        result = result.replace(old, new)
        # 대문자 버전
        result = result.replace(old.upper(), new.upper())
        # 첫글자 대문자
        result = result.replace(old.title(), new.title())
        return result

    def _fix_numbers_in_words(self, text: str) -> tuple[str, list]:
        """
        단어 내 숫자/특수문자를 문자로 교정

        예: 1NDONESIA → INDONESIA, PRES1DEN → PRESIDEN, REPUB|IK → REPUBLIK
        단, 순수 숫자나 숫자+문자 조합(예: Pasal 1)은 제외
        """
        corrections = []
        words = text.split()
        corrected_words = []

        for word in words:
            prefix, core, suffix = self._split_punctuation(word)

            # 순수 숫자는 스킵
            if core.isdigit():
                corrected_words.append(word)
                continue

            # 숫자, 특수문자, 문자가 섞인 경우 처리
            has_digit = any(c.isdigit() for c in core)
            has_special = any(c in self.SPECIAL_TO_LETTER for c in core)
            has_letter = any(c.isalpha() for c in core)

            if (has_digit or has_special) and has_letter:
                # 숫자/특수문자 → 문자 변환 시도
                new_core = self._convert_digits_to_letters(core)

                # 변환 후 달라졌으면
                if new_core != core:
                    # 변환된 단어가 인도네시아어 사전에 있는지 확인
                    if new_core.lower() in self._indonesian_words_set:
                        corrections.append({
                            "type": "digit_to_letter",
                            "original": core,
                            "corrected": new_core,
                        })
                        core = new_core
                    else:
                        # 사전에 없어도, 변환이 의미 있으면 적용
                        # (예: 7AHUN → TAHUN, REPUB|IK → REPUBLIK)
                        if self._looks_like_word(new_core) and not self._looks_like_word(core):
                            corrections.append({
                                "type": "digit_to_letter",
                                "original": core,
                                "corrected": new_core,
                            })
                            core = new_core

            corrected_words.append(prefix + core + suffix)

        return " ".join(corrected_words), corrections

    def _convert_digits_to_letters(self, word: str) -> str:
        """숫자를 문자로 변환"""
        result = []
        for char in word:
            if char in self.DIGIT_TO_LETTER:
                result.append(self.DIGIT_TO_LETTER[char])
            elif char in self.SPECIAL_TO_LETTER:
                result.append(self.SPECIAL_TO_LETTER[char])
            else:
                result.append(char)
        return "".join(result)

    def _looks_like_word(self, text: str) -> bool:
        """텍스트가 단어처럼 보이는지 확인 (모두 문자인지)"""
        return text.isalpha() and len(text) >= 2

    def _split_punctuation(self, word: str) -> tuple[str, str, str]:
        """단어에서 앞뒤 구두점 분리"""
        prefix = ""
        suffix = ""
        core = word

        while core and not core[0].isalnum():
            prefix += core[0]
            core = core[1:]
        while core and not core[-1].isalnum():
            suffix = core[-1] + suffix
            core = core[:-1]

        return prefix, core, suffix

    def _preserve_case(self, original: str, replacement: str) -> str:
        """원본의 대소문자 패턴 유지"""
        if original.isupper():
            return replacement.upper()
        elif original.islower():
            return replacement.lower()
        elif original.istitle():
            return replacement.title()
        else:
            # 복잡한 패턴은 원본 그대로
            return replacement

    def _apply_dictionary_corrections(self, text: str) -> tuple[str, list]:
        """사전 기반 교정"""
        corrections = []
        words = text.split()
        corrected_words = []

        for word in words:
            # 단어 정규화 (구두점 분리)
            prefix = ""
            suffix = ""
            core_word = word

            # 앞뒤 구두점 분리
            while core_word and not core_word[0].isalnum():
                prefix += core_word[0]
                core_word = core_word[1:]
            while core_word and not core_word[-1].isalnum():
                suffix = core_word[-1] + suffix
                core_word = core_word[:-1]

            # 사전에서 교정 찾기
            upper_word = core_word.upper()
            if upper_word in self.LEGAL_WORD_CORRECTIONS:
                correct_word = self.LEGAL_WORD_CORRECTIONS[upper_word]
                if upper_word != correct_word:
                    corrections.append({
                        "type": "dictionary",
                        "original": core_word,
                        "corrected": correct_word,
                    })
                    # 원래 대소문자 유지 시도
                    if core_word.isupper():
                        core_word = correct_word.upper()
                    elif core_word.istitle():
                        core_word = correct_word.title()
                    else:
                        core_word = correct_word

            corrected_words.append(prefix + core_word + suffix)

        return " ".join(corrected_words), corrections

    def _apply_regex_corrections(self, text: str) -> tuple[str, list]:
        """정규식 기반 교정"""
        corrections = []
        corrected = text

        for pattern, replacement in self._compiled_regex:
            matches = pattern.findall(corrected)
            if matches:
                # 교정 적용
                if callable(replacement):
                    corrected = pattern.sub(replacement, corrected)
                else:
                    corrected = pattern.sub(replacement, corrected)

                for match in matches:
                    corrections.append({
                        "type": "regex",
                        "pattern": pattern.pattern,
                        "match": match if isinstance(match, str) else match[0],
                    })

        return corrected, corrections

    def add_custom_correction(self, wrong: str, correct: str):
        """커스텀 교정 규칙 추가"""
        self.LEGAL_WORD_CORRECTIONS[wrong.upper()] = correct.upper()
        self._word_corrections_lower[wrong.lower()] = correct

    def detect_errors(self, text: str) -> List[DetectedError]:
        """
        OCR 오류 탐지 (교정 없이 리포트만)

        Args:
            text: OCR 추출 텍스트

        Returns:
            탐지된 오류 리스트
        """
        errors = []
        words = text.split()

        for i, word in enumerate(words):
            prefix, core, suffix = self._split_punctuation(word)
            core_lower = core.lower()

            # 1. 사전 기반 오류 탐지
            upper_word = core.upper()
            if upper_word in self.LEGAL_WORD_CORRECTIONS:
                correct = self.LEGAL_WORD_CORRECTIONS[upper_word]
                if upper_word != correct:
                    errors.append(DetectedError(
                        error_type=ErrorType.CHAR_CONFUSION,
                        position=i,
                        original=core,
                        suggested=correct,
                        confidence=0.95,
                        context=self._get_context(words, i),
                    ))

            # 2. 흔한 OCR 오류 탐지
            if core_lower in self._common_errors_lower:
                errors.append(DetectedError(
                    error_type=ErrorType.LIGATURE,
                    position=i,
                    original=core,
                    suggested=self._common_errors_lower[core_lower],
                    confidence=0.90,
                    context=self._get_context(words, i),
                ))

            # 3. 단어 내 숫자 탐지
            has_digit = any(c.isdigit() for c in core)
            has_letter = any(c.isalpha() for c in core)
            if has_digit and has_letter and len(core) > 2:
                new_core = self._convert_digits_to_letters(core)
                if new_core.lower() in self._indonesian_words_set:
                    errors.append(DetectedError(
                        error_type=ErrorType.NUMBER_IN_WORD,
                        position=i,
                        original=core,
                        suggested=new_core,
                        confidence=0.85,
                        context=self._get_context(words, i),
                    ))

        # 4. 하이픈 끊김 탐지
        hyphen_matches = self.HYPHEN_BREAK_PATTERN.finditer(text)
        for match in hyphen_matches:
            errors.append(DetectedError(
                error_type=ErrorType.HYPHEN_BREAK,
                position=match.start(),
                original=match.group(0),
                suggested=f"{match.group(1)}-{match.group(2)}",
                confidence=0.99,
            ))

        return errors

    def _get_context(self, words: List[str], index: int, window: int = 2) -> str:
        """주변 문맥 추출"""
        start = max(0, index - window)
        end = min(len(words), index + window + 1)
        return " ".join(words[start:end])

    def get_error_summary(self, errors: List[DetectedError]) -> Dict:
        """오류 요약 통계"""
        summary = {
            "total": len(errors),
            "by_type": {},
            "high_confidence": 0,
            "low_confidence": 0,
        }

        for error in errors:
            error_type = error.error_type.value
            summary["by_type"][error_type] = summary["by_type"].get(error_type, 0) + 1

            if error.confidence >= 0.9:
                summary["high_confidence"] += 1
            else:
                summary["low_confidence"] += 1

        return summary


def main():
    """테스트"""
    print("=" * 70)
    print("OCR 오류 교정기 테스트")
    print("=" * 70)

    # 테스트 케이스들
    test_cases = [
        # 1. 숫자-문자 혼동
        ("1NDONESIA REPUBL1K", "숫자→문자 혼동"),
        ("PRES1DEN PEMER1NTAH", "숫자→문자 혼동"),
        ("7AHUN 2024 7ENTANG", "숫자→문자 혼동"),

        # 2. O↔0 혼동
        ("TAHUN 20O4", "O→0 (숫자 내)"),
        ("NOMOR 1O1", "O→0 (숫자 내)"),
        ("IND0NESIA", "0→O (단어 내)"),

        # 3. 합자 오류
        ("PERNERINTAH RNASYARAKAT", "rn→m 혼동"),
        ("UNCLANG-UNCLANG", "cl→d 혼동"),

        # 4. 특수문자 혼동
        ("PRES!DEN REPUB|IK", "!→I, |→I"),

        # 5. 하이픈 끊김
        ("UNDANG-\nUNDANG", "하이픈 끊김"),

        # 6. 복합 오류
        ("""SALINAN
PRES!DEN REPIJBUK 1NDONESIA
UNDANG—UNDANG N0MOR 11 7AHUN 20O8
PERNERINTAH telah MENE7APKAN
BAB 1 KE7EN7UAN UMUM
PASA1 1""", "복합 오류"),

        # 7. 옛 철자법 (Ejaan Lama) - 신규
        ("Djuli Djanuari djumlah", "옛 철자법: dj→j"),
        ("tjara tjatatan tjukup", "옛 철자법: tj→c"),
        ("wadjib perdjalanan dikundjungi", "옛 철자법: dj→j (단어 내)"),
        ("jang jaitu jogjakarta", "옛 철자법: j→y"),
        ("sjarat njata", "옛 철자법: sj→sy, nj→ny"),

        # 8. HTML 태그 - 신규
        ("PRESIDEN<br>REPUBLIK<br>INDONESIA", "HTML 태그 제거"),
        ("<b>SALINAN</b> dengan <i>rahmat</i>", "HTML 태그 제거"),

        # 9. OCR 노이즈 - 신규
        ("PRESIDEN $200 $400 $ REPUBLIK", "노이즈 필터링"),
        ("ay ±1,5°¥ +3 +3 °C no più", "노이즈 필터링"),
        ("-- -- -- -- -- -- PASAL 1", "노이즈 필터링"),

        # 10. 1953년 문서 스타일 복합 테스트 - 신규
        ("""PRESIDEN REPUBLIK INDONESIA
KEPUTUSAN PRESIDEN REPUBLIK INDONESIA No. 126 TAHUN 1953.
Membatja : surat undangan Direktur Djenderal F.A.O.
tanggal 8 Mei 1953<br>untuk mengirimkan delegasi
Menimbang: bahwa perlu mengirimkan suatu Perutusan
jang diadakan di Bangalore pada tanggal 27 Djuli
perdjalanan djabatan keluar Negeri
wadjib mempertanggung-djawabkan kepada Djawatan""", "1953년 문서 스타일"),
    ]

    corrector = OCRCorrector()

    for test_text, description in test_cases:
        print(f"\n{'─' * 70}")
        print(f"테스트: {description}")
        print(f"{'─' * 70}")
        print(f"원본: {test_text[:100]}{'...' if len(test_text) > 100 else ''}")

        result = corrector.correct(test_text)
        print(f"교정: {result.corrected[:100]}{'...' if len(result.corrected) > 100 else ''}")
        print(f"교정 횟수: {result.corrections_made}")

        if result.correction_details:
            print("상세:")
            for c in result.correction_details[:5]:
                print(f"  - [{c['type']}] {c.get('original', '')} → {c.get('corrected', '')}")

    # 오류 탐지 테스트
    print(f"\n{'=' * 70}")
    print("오류 탐지 테스트 (교정 없이)")
    print("=" * 70)

    detect_text = "1NDONESIA PERNERINTAH 7AHUN 20O4 PRES!DEN"
    errors = corrector.detect_errors(detect_text)

    print(f"텍스트: {detect_text}")
    print(f"탐지된 오류: {len(errors)}개")
    for error in errors:
        print(f"  - [{error.error_type.value}] '{error.original}' → '{error.suggested}' (신뢰도: {error.confidence:.0%})")

    summary = corrector.get_error_summary(errors)
    print(f"\n요약: {summary}")


if __name__ == "__main__":
    main()
