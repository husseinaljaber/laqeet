"""Recover text from PDF fonts that lost their Unicode map.

Some ministry PDFs embed an Arabic font (Identity-H) whose ToUnicode table only covers part of the glyphs.
For the rest, PyMuPDF hands back the glyph id as if it were a character (U+162D «ᘭ» = glyph 5677, …).
The font file itself still knows what each glyph is: its cmap maps letters to base glyphs, and its GSUB
table turns base glyphs into initial/medial/final forms and ligatures. Walking those tables backwards
gives the letters for every shaped glyph.
"""
from __future__ import annotations

import io


def glyph_text_map(font_bytes: bytes) -> dict[int, str]:
    """glyph id -> the letters it draws, for every glyph we can trace back to the cmap."""
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(font_bytes), lazy=True)
    order = font.getGlyphOrder()
    gid = {name: i for i, name in enumerate(order)}
    text: dict[str, str] = {}
    for code, name in (font.getBestCmap() or {}).items():
        if name not in text or len(chr(code)) < len(text[name]):
            text[name] = chr(code)
    pairs_single: list[tuple[str, str]] = []
    pairs_lig: list[tuple[list[str], str]] = []
    if "GSUB" in font:
        for lookup in font["GSUB"].table.LookupList.Lookup:
            for sub in lookup.SubTable:
                if lookup.LookupType == 7:  # extension
                    sub = sub.ExtSubTable
                kind = getattr(sub, "LookupType", lookup.LookupType)
                if kind == 1:
                    pairs_single += list(sub.mapping.items())
                elif kind == 3:  # alternates
                    for base, alts in sub.alternates.items():
                        pairs_single += [(base, a) for a in alts]
                elif kind == 4:
                    for first, ligs in sub.ligatures.items():
                        for lig in ligs:
                            pairs_lig.append(([first] + list(lig.Component), lig.LigGlyph))
    for _ in range(4):  # forms of forms (e.g. a final form of a ligature)
        for base, form in pairs_single:
            if base in text and form not in text:
                text[form] = text[base]
        for comps, lig in pairs_lig:
            if lig not in text and all(c in text for c in comps):
                text[lig] = "".join(text[c] for c in comps)
    return {gid[name]: t for name, t in text.items() if name in gid}


def page_glyph_maps(doc, page) -> dict[str, dict[int, str]]:
    """Font name (as PyMuPDF reports it in spans) -> glyph map, for the fonts used on a page."""
    maps = {}
    for xref, ext, _type, basefont, _name, enc in page.get_fonts():
        if enc != "Identity-H" or ext not in ("ttf", "otf"):
            continue
        try:
            buf = doc.extract_font(xref)[3]
            if buf:
                maps[basefont.split("+")[-1]] = glyph_text_map(buf)
        except Exception:  # a broken font should not stop the whole file
            continue
    return maps
