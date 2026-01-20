"""Peraturan (legal document) model for Peraturan Crawler."""

from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime


@dataclass
class Peraturan:
    """Indonesian legal document model.

    Attributes:
        slug: URL slug as primary key (e.g., 'uu-no-2-tahun-2025')
        jenis: Document type (e.g., 'UNDANG-UNDANG', 'PP', 'PERPRES')
        nomor: Document number
        tahun: Year
        tentang: Title/subject
        source_url: Original URL of the document
    """

    # Primary Key
    slug: str

    # Basic information (required)
    jenis: str
    nomor: str
    tahun: int
    tentang: str
    source_url: str

    # Drafting information (optional)
    pemrakarsa: Optional[str] = None
    tempat_penetapan: Optional[str] = None
    tanggal_penetapan: Optional[str] = None
    pejabat_penetapan: Optional[str] = None

    # Promulgation information (optional)
    tahun_pengundangan: Optional[int] = None
    nomor_pengundangan: Optional[str] = None
    nomor_tambahan: Optional[str] = None
    tanggal_pengundangan: Optional[str] = None
    pejabat_pengundangan: Optional[str] = None

    # Status
    status: str = "Berlaku"  # Berlaku (active) / Tidak Berlaku (inactive)

    # File information
    pdf_url: Optional[str] = None
    local_pdf_path: Optional[str] = None

    # Metadata
    created_at: Optional[str] = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: Optional[str] = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self) -> None:
        """Validate and normalize fields after initialization."""
        # Ensure tahun is valid
        current_year = datetime.now().year
        if not (1945 <= self.tahun <= current_year + 1):
            raise ValueError(f"Invalid year: {self.tahun}")

        # NOTE: PDF URL is now extracted from detail page by parser
        # Do NOT auto-generate - wrong URL pattern causes 302 redirects

    @property
    def display_name(self) -> str:
        """Human-readable name of the document."""
        return f"{self.jenis} No. {self.nomor} Tahun {self.tahun}"

    @property
    def short_type(self) -> str:
        """Short type code for folder organization."""
        type_map = {
            # 헌법/국회
            "UUD": "uud",
            "UUD 1945": "uud",
            "UNDANG-UNDANG DASAR": "uud",
            "TAP MPR": "tapmpr",
            "KETETAPAN MPR": "tapmpr",
            "KETETAPAN MAJELIS PERMUSYAWARATAN RAKYAT": "tapmpr",
            # 중앙정부 법령
            "UNDANG-UNDANG": "uu",
            "UNDANG-UNDANG DARURAT": "uu-darurat",
            "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "perppu",
            "PERPPU": "perppu",
            "PERATURAN PEMERINTAH": "pp",
            "PERATURAN PRESIDEN": "perpres",
            "KEPUTUSAN PRESIDEN": "keppres",
            "INSTRUKSI PRESIDEN": "inpres",
            "PENETAPAN PRESIDEN": "penpres",
            "PENPRES": "penpres",
            # 부처/기관 규정
            "PERATURAN MENTERI": "permen",
            "PERATURAN BADAN/LEMBAGA": "perban",
        }
        return type_map.get(self.jenis.upper(), "other")

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Peraturan":
        """Create instance from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
