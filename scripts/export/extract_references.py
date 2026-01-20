#!/usr/bin/env python3
"""Extract law references from legal document text."""

import re
import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class LawReference:
    """Represents a reference to another law."""
    jenis: str          # UNDANG-UNDANG, PERATURAN_PEMERINTAH, etc.
    nomor: str          # Law number
    tahun: int          # Year
    tentang: Optional[str] = None  # Subject (if captured)
    context: Optional[str] = None  # Surrounding text
    ref_type: str = "REFERENCES"   # REFERENCES, AMENDS, REVOKES, IMPLEMENTS


@dataclass
class InternalReference:
    """Represents an internal reference within the same law."""
    target_type: str    # PASAL, AYAT, HURUF, ANGKA
    target_value: str   # Number or letter
    context: Optional[str] = None


@dataclass
class ConditionalClause:
    """Represents a conditional validity clause."""
    clause_type: str    # SEPANJANG_TIDAK_BERTENTANGAN, TETAP_BERLAKU, etc.
    text: str           # Full clause text
    target_law: Optional[LawReference] = None


class ReferenceExtractor:
    """Extract references from Indonesian legal texts."""

    # Law type patterns (jenis)
    LAW_TYPES = {
        r'Undang[-\s]?[Uu]ndang\s+Darurat': 'UNDANG-UNDANG_DARURAT',
        r'Undang[-\s]?[Uu]ndang': 'UNDANG-UNDANG',
        r'UU': 'UNDANG-UNDANG',
        r'Peraturan\s+Pemerintah\s+Pengganti\s+Undang[-\s]?[Uu]ndang': 'PERPPU',
        r'Perppu': 'PERPPU',
        r'Peraturan\s+Pemerintah': 'PERATURAN_PEMERINTAH',
        r'PP': 'PERATURAN_PEMERINTAH',
        r'Peraturan\s+Presiden': 'PERATURAN_PRESIDEN',
        r'Perpres': 'PERATURAN_PRESIDEN',
        r'Peraturan\s+Menteri\s+\w+': 'PERATURAN_MENTERI',
        r'Permen\w*': 'PERATURAN_MENTERI',
        r'Keputusan\s+Presiden': 'KEPUTUSAN_PRESIDEN',
        r'Keppres': 'KEPUTUSAN_PRESIDEN',
        r'Peraturan\s+Daerah': 'PERATURAN_DAERAH',
        r'Perda': 'PERATURAN_DAERAH',
        r'Staatsblad': 'STAATSBLAD',
        r'Stb\.?': 'STAATSBLAD',
        r'Lembaran\s+Negara': 'LEMBARAN_NEGARA',
        r'L\.?N\.?': 'LEMBARAN_NEGARA',
    }

    # External reference pattern
    # Matches: "Undang-Undang Nomor 11 Tahun 2020 tentang Cipta Kerja"
    # Also matches old formats: "Undang-undang Darurat No. 12 tahun 1950"
    # Also matches: "Undang-undang No. 19 Prp tahun 1960" (old Perppu format)
    EXTERNAL_REF_PATTERN = re.compile(
        r'(?P<jenis>Undang[-\s]?[Uu]ndang\s+Darurat|'
        r'Undang[-\s]?[Uu]ndang|UU|'
        r'Peraturan\s+Pemerintah\s+Pengganti\s+Undang[-\s]?[Uu]ndang|Perppu|'
        r'Peraturan\s+Pemerintah|PP|'
        r'Peraturan\s+Presiden|Perpres|'
        r'Peraturan\s+Menteri\s*\w*|Permen\w*|'
        r'Keputusan\s+Presiden|Keppres|'
        r'Peraturan\s+Daerah|Perda|'
        r'Staatsblad|Stb\.?)'
        r'\s+(?:Republik\s+Indonesia\s+)?'
        r'(?:Nomor|No\.?)\s*'
        r'(?P<nomor>[\d]+(?:\s*/\s*[\w\-]+)?)'
        r'(?:\s+(?P<prp>Prp))?'  # Optional Prp (old Perppu marker)
        r'\s+(?:Tahun|Thn\.?|tahun)\s*'
        r'(?P<tahun>\d{4})'
        r'(?:\s+[Tt]entang\s+(?P<tentang>[^(;.\n]{5,100}))?',
        re.IGNORECASE
    )

    # Colonial era Staatsblad pattern (e.g., "Staatsblad 1933 No. 261")
    STAATSBLAD_PATTERN = re.compile(
        r'(?P<jenis>Staatsblad|Stb\.?)\s+'
        r'(?P<tahun>\d{4})\s+'
        r'(?:Nomor|No\.?)\s*'
        r'(?P<nomor>[\d]+)',
        re.IGNORECASE
    )

    # Internal reference patterns
    INTERNAL_PASAL_PATTERN = re.compile(
        r'(?:sebagaimana\s+)?(?:dimaksud|diatur)\s+'
        r'(?:dalam|pada|di)\s+'
        r'(?:ketentuan\s+)?'
        r'Pasal\s+(?P<pasal>\d+[A-Za-z]?)'
        r'(?:\s+ayat\s*\((?P<ayat>\d+)\))?'
        r'(?:\s+huruf\s+(?P<huruf>[a-z]))?',
        re.IGNORECASE
    )

    # Conditional validity patterns
    CONDITIONAL_PATTERNS = {
        'SEPANJANG_TIDAK_BERTENTANGAN': re.compile(
            r'(?P<text>(?:dinyatakan\s+)?(?:masih\s+)?tetap\s+berlaku\s+'
            r'sepanjang\s+tidak\s+bertentangan\s+dengan[^.]{10,200})',
            re.IGNORECASE
        ),
        'TETAP_BERLAKU_SAMPAI': re.compile(
            r'(?P<text>tetap\s+berlaku\s+sampai\s+(?:dengan\s+)?'
            r'(?:ditetapkannya|diundangkannya)[^.]{10,150})',
            re.IGNORECASE
        ),
        'DICABUT_DAN_TIDAK_BERLAKU': re.compile(
            r'(?P<text>dicabut\s+dan\s+dinyatakan\s+tidak\s+berlaku[^.]{0,100})',
            re.IGNORECASE
        ),
    }

    # Amendment/Revocation context patterns
    AMEND_CONTEXT = re.compile(
        r'(?:perubahan\s+(?:atas|terhadap)|mengubah)\s+',
        re.IGNORECASE
    )
    REVOKE_CONTEXT = re.compile(
        r'(?:mencabut|dicabut|pencabutan)\s+',
        re.IGNORECASE
    )
    IMPLEMENT_CONTEXT = re.compile(
        r'(?:melaksanakan\s+ketentuan|sebagai\s+pelaksanaan)\s+',
        re.IGNORECASE
    )

    def __init__(self):
        self.stats = {
            'external_refs': 0,
            'internal_refs': 0,
            'conditional_clauses': 0,
            'amends': 0,
            'revokes': 0,
            'implements': 0,
        }

    def normalize_jenis(self, jenis_text: str) -> str:
        """Normalize law type to standard form."""
        jenis_upper = jenis_text.upper().strip()

        # Colonial era
        if 'STAATSBLAD' in jenis_upper or jenis_upper.startswith('STB'):
            return 'STAATSBLAD'
        elif 'LEMBARAN' in jenis_upper or jenis_upper in ('LN', 'L.N.'):
            return 'LEMBARAN_NEGARA'
        # Emergency law (구법)
        elif 'DARURAT' in jenis_upper:
            return 'UNDANG-UNDANG_DARURAT'
        elif 'PENGGANTI' in jenis_upper or 'PERPPU' in jenis_upper:
            return 'PERPPU'
        elif 'UNDANG' in jenis_upper or jenis_upper == 'UU':
            return 'UNDANG-UNDANG'
        elif 'PEMERINTAH' in jenis_upper and 'PRESIDEN' not in jenis_upper:
            return 'PERATURAN_PEMERINTAH'
        elif 'PRESIDEN' in jenis_upper:
            if 'KEPUTUSAN' in jenis_upper:
                return 'KEPUTUSAN_PRESIDEN'
            return 'PERATURAN_PRESIDEN'
        elif 'MENTERI' in jenis_upper or 'PERMEN' in jenis_upper:
            return 'PERATURAN_MENTERI'
        elif 'DAERAH' in jenis_upper or 'PERDA' in jenis_upper:
            return 'PERATURAN_DAERAH'
        else:
            return jenis_text.upper().replace(' ', '_')

    def extract_external_references(self, text: str) -> list[LawReference]:
        """Extract references to other laws."""
        refs = []
        seen = set()  # Avoid duplicates

        # Standard pattern (Nomor X Tahun YYYY)
        for match in self.EXTERNAL_REF_PATTERN.finditer(text):
            jenis = self.normalize_jenis(match.group('jenis'))
            nomor = match.group('nomor').strip()
            tahun = int(match.group('tahun'))
            tentang = match.group('tentang')

            # Check for old Prp (Perppu) marker
            prp = match.group('prp')
            if prp and 'UNDANG' in jenis:
                jenis = 'PERPPU'  # "Undang-undang No. X Prp" = Perppu

            # Skip duplicates
            ref_key = (jenis, nomor, tahun)
            if ref_key in seen:
                continue
            seen.add(ref_key)

            if tentang:
                tentang = tentang.strip()
                # Clean up tentang
                tentang = re.sub(r'\s+', ' ', tentang)

            # Get surrounding context (50 chars before match)
            start = max(0, match.start() - 50)
            context = text[start:match.start()].strip()

            # Determine reference type based on context
            ref_type = "REFERENCES"
            if self.AMEND_CONTEXT.search(context):
                ref_type = "AMENDS"
                self.stats['amends'] += 1
            elif self.REVOKE_CONTEXT.search(context):
                ref_type = "REVOKES"
                self.stats['revokes'] += 1
            elif self.IMPLEMENT_CONTEXT.search(context):
                ref_type = "IMPLEMENTS"
                self.stats['implements'] += 1

            refs.append(LawReference(
                jenis=jenis,
                nomor=nomor,
                tahun=tahun,
                tentang=tentang,
                context=context[-100:] if len(context) > 100 else context,
                ref_type=ref_type,
            ))

        # Colonial era Staatsblad pattern (Staatsblad YYYY No. X)
        for match in self.STAATSBLAD_PATTERN.finditer(text):
            jenis = 'STAATSBLAD'
            tahun = int(match.group('tahun'))
            nomor = match.group('nomor').strip()

            ref_key = (jenis, nomor, tahun)
            if ref_key in seen:
                continue
            seen.add(ref_key)

            start = max(0, match.start() - 50)
            context = text[start:match.start()].strip()

            refs.append(LawReference(
                jenis=jenis,
                nomor=nomor,
                tahun=tahun,
                tentang=None,
                context=context[-100:] if len(context) > 100 else context,
                ref_type="REFERENCES",
            ))

        self.stats['external_refs'] += len(refs)
        return refs

    def extract_internal_references(self, text: str) -> list[InternalReference]:
        """Extract internal references (Pasal, Ayat, Huruf)."""
        refs = []

        for match in self.INTERNAL_PASAL_PATTERN.finditer(text):
            pasal = match.group('pasal')
            ayat = match.group('ayat')
            huruf = match.group('huruf')

            # Build target value
            if ayat and huruf:
                target = f"Pasal {pasal} ayat ({ayat}) huruf {huruf}"
            elif ayat:
                target = f"Pasal {pasal} ayat ({ayat})"
            else:
                target = f"Pasal {pasal}"

            refs.append(InternalReference(
                target_type="PASAL",
                target_value=target,
                context=match.group(0)[:100],
            ))

        self.stats['internal_refs'] += len(refs)
        return refs

    def extract_conditional_clauses(self, text: str) -> list[ConditionalClause]:
        """Extract conditional validity clauses."""
        clauses = []

        for clause_type, pattern in self.CONDITIONAL_PATTERNS.items():
            for match in pattern.finditer(text):
                clause_text = match.group('text').strip()
                clause_text = re.sub(r'\s+', ' ', clause_text)

                # Try to extract target law from the clause
                target_refs = self.extract_external_references(clause_text)
                target_law = target_refs[0] if target_refs else None

                clauses.append(ConditionalClause(
                    clause_type=clause_type,
                    text=clause_text[:200],
                    target_law=target_law,
                ))

        self.stats['conditional_clauses'] += len(clauses)
        return clauses

    def extract_all(self, text: str, slug: str = "") -> dict:
        """Extract all types of references from text."""
        result = {
            'slug': slug,
            'external_refs': [asdict(r) for r in self.extract_external_references(text)],
            'internal_refs': [asdict(r) for r in self.extract_internal_references(text)],
            'conditional_clauses': [],
        }

        # Process conditional clauses
        for clause in self.extract_conditional_clauses(text):
            clause_dict = {
                'clause_type': clause.clause_type,
                'text': clause.text,
            }
            if clause.target_law:
                clause_dict['target_law'] = asdict(clause.target_law)
            result['conditional_clauses'].append(clause_dict)

        return result


def test_extractor(db_path: str = "data/peraturan.db", sample_slugs: list[str] = None):
    """Test the extractor on sample documents."""

    if sample_slugs is None:
        sample_slugs = [
            "uu-no-7-tahun-2021",           # 옴니버스
            "uu-no-6-tahun-2023",           # Cipta Kerja
            "pp-no-109-tahun-2012",         # 시행령
            "permenag-no-71-tahun-2013",    # 조건부 유효
            "pp-no-1-tahun-1963",           # 구법
            "uu-no-25-tahun-1953",          # 매우 오래된 법령
        ]

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    extractor = ReferenceExtractor()
    results = []

    print("\n" + "=" * 70)
    print("법령 참조 추출 테스트")
    print("=" * 70)

    for slug in sample_slugs:
        cursor.execute("""
            SELECT slug, jenis, tahun, tentang, extracted_text
            FROM peraturan WHERE slug = ?
        """, (slug,))

        row = cursor.fetchone()
        if not row:
            print(f"\n[경고] {slug} 문서를 찾을 수 없음")
            continue

        slug, jenis, tahun, tentang, text = row

        if not text:
            print(f"\n[경고] {slug} 텍스트 없음")
            continue

        print(f"\n{'─' * 70}")
        print(f"📄 {slug}")
        print(f"   유형: {jenis} | 연도: {tahun}")
        print(f"   제목: {tentang[:60]}..." if tentang and len(tentang) > 60 else f"   제목: {tentang}")
        print(f"   텍스트 길이: {len(text):,}자")
        print(f"{'─' * 70}")

        # Reset stats for this document
        extractor.stats = {k: 0 for k in extractor.stats}

        # Extract references
        result = extractor.extract_all(text, slug)
        results.append(result)

        # Print summary
        print(f"\n📊 추출 결과:")
        print(f"   외부 참조: {len(result['external_refs'])}건")
        print(f"   내부 참조: {len(result['internal_refs'])}건")
        print(f"   조건부 조항: {len(result['conditional_clauses'])}건")

        # Print sample external references
        if result['external_refs']:
            print(f"\n   🔗 외부 참조 샘플 (최대 5건):")
            for ref in result['external_refs'][:5]:
                ref_type_icon = {"AMENDS": "🔄", "REVOKES": "❌", "IMPLEMENTS": "⚡"}.get(ref['ref_type'], "📎")
                tentang_str = f" - {ref['tentang'][:40]}..." if ref.get('tentang') and len(ref['tentang']) > 40 else (f" - {ref['tentang']}" if ref.get('tentang') else "")
                print(f"      {ref_type_icon} {ref['jenis']} No. {ref['nomor']} Tahun {ref['tahun']}{tentang_str}")

        # Print conditional clauses
        if result['conditional_clauses']:
            print(f"\n   ⚠️ 조건부 조항:")
            for clause in result['conditional_clauses'][:3]:
                print(f"      • [{clause['clause_type']}]")
                print(f"        \"{clause['text'][:80]}...\"")

    conn.close()

    # Overall summary
    print("\n" + "=" * 70)
    print("전체 요약")
    print("=" * 70)

    total_external = sum(len(r['external_refs']) for r in results)
    total_internal = sum(len(r['internal_refs']) for r in results)
    total_conditional = sum(len(r['conditional_clauses']) for r in results)

    print(f"테스트 문서: {len(results)}건")
    print(f"총 외부 참조: {total_external}건")
    print(f"총 내부 참조: {total_internal}건")
    print(f"총 조건부 조항: {total_conditional}건")

    # Save results
    output_path = Path("data/reference_extraction_test.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {output_path}")

    return results


if __name__ == "__main__":
    test_extractor()
