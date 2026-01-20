#!/usr/bin/env python3
"""
인도네시아 법령 → Akoma Ntoso (LegalDocML) XML 변환기

구조:
- BAB (chapter) → Pasal (article) → Ayat (paragraph) → Huruf (point) → Angka (item)
- Penjelasan (notes) 섹션 포함

OASIS LegalDocML 표준: https://docs.oasis-open.org/legaldocml/akn-core/v1.0/
"""

import json
import re
import sqlite3
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
from datetime import datetime
from typing import Optional

# 경로 설정
PROJECT_DIR = Path(__file__).parent.parent
DB_PATH = PROJECT_DIR / "data" / "peraturan.db"
OUTPUT_DIR = PROJECT_DIR / "delivery" / "akoma-ntoso"

# Akoma Ntoso 네임스페이스
AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def setup_output_dir():
    """출력 디렉토리 생성"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "uu").mkdir(exist_ok=True)
    (OUTPUT_DIR / "pp").mkdir(exist_ok=True)
    (OUTPUT_DIR / "perpres").mkdir(exist_ok=True)
    (OUTPUT_DIR / "permen").mkdir(exist_ok=True)
    (OUTPUT_DIR / "other").mkdir(exist_ok=True)


def parse_penjelasan(text: str) -> dict:
    """
    Penjelasan (설명) 섹션 파싱 - 다양한 패턴 지원

    반환:
    {
        "umum": "일반 설명 텍스트",
        "pasal_demi_pasal": {
            "1": "Cukup jelas.",
            "2": {
                "text": "...",
                "huruf": {"a": "...", "b": "..."},
                "ayat": {"1": "...", "2": "..."}
            }
        }
    }
    """
    result = {
        "umum": "",
        "pasal_demi_pasal": {}
    }

    if not text:
        return result

    # PENJELASAN 섹션 찾기 (다양한 패턴)
    penjelasan_patterns = [
        r'PENJELASAN\s*\n\s*ATAS\s*\n',  # PENJELASAN\nATAS\n
        r'PENJELASAN\s*\n\s*MENGENAI\s*\n',  # PENJELASAN\nMENGENAI\n
        r'PENJELASAN\s*\n',  # PENJELASAN\n
    ]

    penjelasan_match = None
    for pattern in penjelasan_patterns:
        penjelasan_match = re.search(pattern, text, re.IGNORECASE)
        if penjelasan_match:
            break

    if not penjelasan_match:
        return result

    penjelasan_text = text[penjelasan_match.end():]

    # I. UMUM 섹션 (다양한 패턴)
    umum_patterns = [
        r'I\.?\s*\n?\s*UMUM\.?\s*\n(.*?)(?=II\.?\s*\n?\s*PASAL|PASAL\s+DEMI\s+PASAL|$)',
        r'UMUM\.?\s*\n(.*?)(?=II\.?\s*\n?\s*PASAL|PASAL\s+DEMI\s+PASAL|$)',
    ]

    for pattern in umum_patterns:
        umum_match = re.search(pattern, penjelasan_text, re.DOTALL | re.IGNORECASE)
        if umum_match:
            result["umum"] = clean_text(umum_match.group(1))
            break

    # II. PASAL DEMI PASAL 섹션 (다양한 패턴)
    pasal_patterns = [
        r'II\.?\s*\n?\s*PASAL\s+DEMI\s+PASAL\.?\s*\n(.*)',
        r'PASAL\s+DEMI\s+PASAL\.?\s*\n(.*)',
    ]

    for pattern in pasal_patterns:
        pasal_section_match = re.search(pattern, penjelasan_text, re.DOTALL | re.IGNORECASE)
        if pasal_section_match:
            pasal_text = pasal_section_match.group(1)
            result["pasal_demi_pasal"] = parse_pasal_explanations(pasal_text)
            break

    return result


def parse_pasal_explanations(text: str) -> dict:
    """개별 Pasal 설명 파싱"""
    explanations = {}

    # Pasal N 패턴으로 분리
    pasal_pattern = r'Pasal\s+(\d+)\s*\n'
    matches = list(re.finditer(pasal_pattern, text))

    for i, match in enumerate(matches):
        pasal_num = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        pasal_content = text[start:end].strip()

        # "Cukup jelas" 체크
        if re.match(r'Cukup\s+jelas\.?', pasal_content, re.IGNORECASE):
            explanations[pasal_num] = "Cukup jelas."
        else:
            # 상세 설명 파싱
            explanations[pasal_num] = parse_pasal_detail(pasal_content)

    return explanations


def parse_pasal_detail(text: str) -> dict:
    """Pasal 내 상세 설명 (Huruf, Ayat) 파싱"""
    result = {"text": "", "huruf": {}, "ayat": {}}

    # Huruf 패턴
    huruf_pattern = r'Huruf\s+([a-z])\s*\n(.*?)(?=Huruf\s+[a-z]|Ayat\s*\(?\d|$)'
    for match in re.finditer(huruf_pattern, text, re.DOTALL | re.IGNORECASE):
        huruf = match.group(1).lower()
        content = clean_text(match.group(2))
        result["huruf"][huruf] = content

    # Ayat 패턴
    ayat_pattern = r'Ayat\s*\(?(\d+)\)?\s*\n(.*?)(?=Ayat\s*\(?\d|Huruf\s+[a-z]|$)'
    for match in re.finditer(ayat_pattern, text, re.DOTALL | re.IGNORECASE):
        ayat_num = match.group(1)
        content = clean_text(match.group(2))
        result["ayat"][ayat_num] = content

    # Huruf/Ayat 없으면 전체를 text로
    if not result["huruf"] and not result["ayat"]:
        result["text"] = clean_text(text)

    return result


def clean_text(text: str) -> str:
    """텍스트 정리"""
    if not text:
        return ""
    # XML 비허용 제어 문자 제거 (ASCII 0-31 중 탭/줄바꿈 제외)
    text = ''.join(c if ord(c) >= 32 or c in '\n\r\t' else ' ' for c in text)
    # SK No 등 스캔 아티팩트 제거
    text = re.sub(r'SK\s+No\s+\d+\s*[A-Z]?\s*', '', text)
    # PRESIDEN 워터마크 제거
    text = re.sub(r'\n\s*PRESIDEN\s*\n', '\n', text)
    # 과도한 공백 정리
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def sanitize_for_xml(text: str) -> str:
    """XML 호환 텍스트로 정제"""
    if not text:
        return ""
    # 제어 문자 제거 (탭/줄바꿈 제외)
    return ''.join(c if ord(c) >= 32 or c in '\n\r\t' else '' for c in str(text))


def get_jenis_folder(jenis: str) -> str:
    """법령 유형에 따른 폴더 결정"""
    jenis_upper = jenis.upper()
    if "UNDANG-UNDANG" in jenis_upper or jenis_upper == "UU":
        return "uu"
    elif "PERATURAN PEMERINTAH" in jenis_upper or jenis_upper == "PP":
        return "pp"
    elif "PERATURAN PRESIDEN" in jenis_upper or "PERPRES" in jenis_upper:
        return "perpres"
    elif "PERATURAN MENTERI" in jenis_upper or "PERMEN" in jenis_upper:
        return "permen"
    else:
        return "other"


def create_akoma_ntoso_xml(law: dict, parsed_json: dict, penjelasan: dict) -> Element:
    """Akoma Ntoso XML 생성"""

    # Root element
    root = Element("akomaNtoso")
    root.set("xmlns", AKN_NS)

    # Act element
    act = SubElement(root, "act")
    act.set("name", law['slug'])

    # Meta section
    meta = SubElement(act, "meta")
    identification = SubElement(meta, "identification")
    identification.set("source", "#peraturan-go-id")

    # FRBRWork
    work = SubElement(identification, "FRBRWork")
    this_work = SubElement(work, "FRBRthis")
    this_work.set("value", f"/id/{law['jenis'].lower().replace(' ', '-')}/{law['tahun']}/{law['nomor']}")
    uri = SubElement(work, "FRBRuri")
    uri.set("value", f"/id/{law['jenis'].lower().replace(' ', '-')}/{law['tahun']}/{law['nomor']}")
    date = SubElement(work, "FRBRdate")
    date.set("date", law.get('tanggal_penetapan', '') or str(law['tahun']))
    date.set("name", "enacted")
    author = SubElement(work, "FRBRauthor")
    author.set("href", "#" + (law.get('pemrakarsa', '') or 'unknown').replace(' ', '-').lower())
    country = SubElement(work, "FRBRcountry")
    country.set("value", "id")

    # FRBRExpression
    expr = SubElement(identification, "FRBRExpression")
    this_expr = SubElement(expr, "FRBRthis")
    this_expr.set("value", f"/id/{law['jenis'].lower().replace(' ', '-')}/{law['tahun']}/{law['nomor']}/ind@")
    uri_expr = SubElement(expr, "FRBRuri")
    uri_expr.set("value", f"/id/{law['jenis'].lower().replace(' ', '-')}/{law['tahun']}/{law['nomor']}/ind@")
    lang = SubElement(expr, "FRBRlanguage")
    lang.set("language", "ind")

    # Preface
    preface = SubElement(act, "preface")
    doc_title = SubElement(preface, "docTitle")
    doc_title.text = f"{law['jenis']} NOMOR {law['nomor']} TAHUN {law['tahun']}"
    doc_purpose = SubElement(preface, "docPurpose")
    doc_purpose.text = law.get('tentang', '')

    # Body
    body = SubElement(act, "body")

    # Babs (Chapters)
    for bab in parsed_json.get('babs', []):
        chapter = SubElement(body, "chapter")
        chapter.set("eId", f"bab_{bab['nomor']}")

        num = SubElement(chapter, "num")
        num.text = f"BAB {bab['nomor']}"

        heading = SubElement(chapter, "heading")
        heading.text = bab.get('judul', '')

        # Pasals (Articles)
        for pasal in bab.get('pasals', []):
            article = SubElement(chapter, "article")
            article.set("eId", f"pasal_{pasal['nomor']}")

            art_num = SubElement(article, "num")
            art_num.text = f"Pasal {pasal['nomor']}"

            # Ayats (Paragraphs)
            ayats = pasal.get('ayats', [])
            if ayats:
                for ayat in ayats:
                    para = SubElement(article, "paragraph")
                    para.set("eId", f"pasal_{pasal['nomor']}__ayat_{ayat['nomor']}")

                    if ayat['nomor'] != 0:
                        para_num = SubElement(para, "num")
                        para_num.text = f"({ayat['nomor']})"

                    # Hurufs (Points)
                    hurufs = ayat.get('hurufs', [])
                    if hurufs:
                        for huruf in hurufs:
                            point = SubElement(para, "point")
                            point.set("eId", f"pasal_{pasal['nomor']}__ayat_{ayat['nomor']}__huruf_{huruf['huruf']}")

                            pt_num = SubElement(point, "num")
                            pt_num.text = f"{huruf['huruf']}."

                            content = SubElement(point, "content")
                            p = SubElement(content, "p")
                            p.text = sanitize_for_xml(huruf.get('text', '')[:500])  # 길이 제한

                            # Angkas (Items)
                            for angka in huruf.get('angkas', []):
                                item = SubElement(point, "item")
                                item.set("eId", f"pasal_{pasal['nomor']}__ayat_{ayat['nomor']}__huruf_{huruf['huruf']}__angka_{angka.get('angka', '')}")

                                item_num = SubElement(item, "num")
                                item_num.text = f"{angka.get('angka', '')}."

                                item_content = SubElement(item, "content")
                                item_p = SubElement(item_content, "p")
                                item_p.text = sanitize_for_xml(str(angka.get('text', ''))[:300])
                    else:
                        # No hurufs, just content
                        content = SubElement(para, "content")
                        p = SubElement(content, "p")
                        p.text = sanitize_for_xml(ayat.get('text', '')[:1000]) if ayat.get('text') else ''
            else:
                # No ayats, just pasal text
                if pasal.get('text'):
                    content = SubElement(article, "content")
                    p = SubElement(content, "p")
                    p.text = sanitize_for_xml(pasal.get('text', '')[:1000])

    # Conclusions (optional)
    conclusions = SubElement(act, "conclusions")
    signature = SubElement(conclusions, "signature")
    signature.text = law.get('pejabat_penetapan', '') or ''

    # Notes (Penjelasan)
    if penjelasan.get('umum') or penjelasan.get('pasal_demi_pasal'):
        notes = SubElement(act, "notes")
        notes.set("source", "#penjelasan")

        # I. UMUM
        if penjelasan.get('umum'):
            note_umum = SubElement(notes, "note")
            note_umum.set("eId", "penjelasan_umum")
            heading_umum = SubElement(note_umum, "heading")
            heading_umum.text = "I. UMUM"
            p_umum = SubElement(note_umum, "p")
            p_umum.text = sanitize_for_xml(penjelasan['umum'][:5000])  # 길이 제한

        # II. PASAL DEMI PASAL
        if penjelasan.get('pasal_demi_pasal'):
            for pasal_num, content in penjelasan['pasal_demi_pasal'].items():
                note_pasal = SubElement(notes, "note")
                note_pasal.set("eId", f"penjelasan_pasal_{pasal_num}")

                heading_pasal = SubElement(note_pasal, "heading")
                heading_pasal.text = f"Pasal {pasal_num}"

                if isinstance(content, str):
                    p_content = SubElement(note_pasal, "p")
                    p_content.text = sanitize_for_xml(content)
                elif isinstance(content, dict):
                    if content.get('text'):
                        p_text = SubElement(note_pasal, "p")
                        p_text.text = sanitize_for_xml(content['text'][:2000])

                    for huruf, huruf_text in content.get('huruf', {}).items():
                        p_huruf = SubElement(note_pasal, "p")
                        p_huruf.set("eId", f"penjelasan_pasal_{pasal_num}__huruf_{huruf}")
                        p_huruf.text = f"Huruf {huruf}: {sanitize_for_xml(huruf_text[:500])}"

                    for ayat_num, ayat_text in content.get('ayat', {}).items():
                        p_ayat = SubElement(note_pasal, "p")
                        p_ayat.set("eId", f"penjelasan_pasal_{pasal_num}__ayat_{ayat_num}")
                        p_ayat.text = f"Ayat ({ayat_num}): {sanitize_for_xml(ayat_text[:500])}"

    return root


def prettify_xml(elem: Element) -> str:
    """XML 포맷팅"""
    rough_string = tostring(elem, encoding='unicode')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")


def convert_law_to_xml(row: sqlite3.Row) -> Optional[str]:
    """단일 법령을 XML로 변환"""
    try:
        parsed_json = json.loads(row['parsed_json']) if row['parsed_json'] else {'babs': [], 'pasals': []}
        penjelasan = parse_penjelasan(row['extracted_text'] or '')

        law = {
            'slug': row['slug'],
            'jenis': row['jenis'],
            'nomor': row['nomor'],
            'tahun': row['tahun'],
            'tentang': row['tentang'],
            'tanggal_penetapan': row['tanggal_penetapan'],
            'pemrakarsa': row['pemrakarsa'],
            'pejabat_penetapan': row['pejabat_penetapan']
        }

        xml_elem = create_akoma_ntoso_xml(law, parsed_json, penjelasan)
        return prettify_xml(xml_elem)

    except Exception as e:
        print(f"  ⚠️ 변환 실패 {row['slug']}: {e}")
        return None


def main():
    """메인 실행"""
    print("=" * 60)
    print("인도네시아 법령 → Akoma Ntoso XML 변환")
    print("=" * 60)

    setup_output_dir()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 파싱 완료된 법령만 대상
    cursor = conn.execute("""
        SELECT slug, jenis, nomor, tahun, tentang,
               tanggal_penetapan, pemrakarsa, pejabat_penetapan,
               parsed_json, extracted_text
        FROM peraturan
        WHERE parse_success = 1
        ORDER BY tahun DESC, jenis, nomor
    """)

    total = 0
    success = 0
    with_penjelasan = 0

    for row in cursor:
        total += 1

        xml_content = convert_law_to_xml(row)
        if xml_content:
            # 폴더 결정 및 저장
            folder = get_jenis_folder(row['jenis'])
            output_path = OUTPUT_DIR / folder / f"{row['slug']}.xml"
            output_path.write_text(xml_content, encoding='utf-8')
            success += 1

            # Penjelasan 포함 여부
            if 'penjelasan_umum' in xml_content or 'penjelasan_pasal' in xml_content:
                with_penjelasan += 1

        if total % 1000 == 0:
            print(f"  진행: {total}건 처리, {success}건 성공, {with_penjelasan}건 Penjelasan 포함")

    conn.close()

    print("-" * 60)
    print(f"✅ 변환 완료!")
    print(f"   총 처리: {total}건")
    print(f"   성공: {success}건 ({success/total*100:.1f}%)")
    print(f"   Penjelasan 포함: {with_penjelasan}건")
    print(f"   출력: {OUTPUT_DIR}")

    # 통계 저장
    stats = {
        "converted_at": datetime.now().isoformat(),
        "total_processed": total,
        "success": success,
        "with_penjelasan": with_penjelasan,
        "format": "Akoma Ntoso 3.0",
        "standard": "OASIS LegalDocML"
    }
    (OUTPUT_DIR / "stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
