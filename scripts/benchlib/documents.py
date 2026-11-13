from __future__ import annotations

from pathlib import Path


def read_document(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".html", ".json", ".csv"}:
        return path.read_text(encoding="utf-8-sig")
    if suffix == ".docx":
        from docx import Document
        from docx.oxml.ns import qn
        doc = Document(path)
        # Include tracked insertions/deletions and comments in evaluation extraction.
        blocks = []
        for p in doc.element.body.iter(qn("w:p")):
            blocks.append("".join(t.text or "" for t in p.iter() if t.tag in {qn("w:t"), qn("w:delText")}))
        comments = []
        if hasattr(doc, "comments"):
            comments = [{"id": c.comment_id, "text": c.text} for c in doc.comments]
        return {"paragraphs_and_cells": blocks, "comments": comments}
    if suffix == ".xlsx":
        from openpyxl import load_workbook
        book = load_workbook(path, data_only=False)
        value = {s.title: [[{"cell": c.coordinate, "value": c.value} for c in row]
                           for row in s.iter_rows()] for s in book}
        book.close()
        return value
    if suffix == ".pptx":
        from pptx import Presentation
        return [{"slide": i+1, "text": [shape.text for shape in slide.shapes if shape.has_text_frame],
                 "tables": [[[c.text for c in row.cells] for row in shape.table.rows]
                            for shape in slide.shapes if shape.has_table],
                 "notes": slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""}
                for i, slide in enumerate(Presentation(path).slides)]
    if suffix == ".pdf":
        from pypdf import PdfReader
        source_pages=list(PdfReader(path).pages)
        pages = [{"page": i+1, "text": p.extract_text() or ""} for i, p in enumerate(source_pages)]
        scanned=[i+1 for i,p in enumerate(source_pages) if not pages[i]['text'].strip() and len(p.images)>0]
        if scanned:
            raise ValueError(f"PDF contains image-only pages requiring local OCR: {scanned}; partial extraction refused")
        if not any(p["text"].strip() for p in pages):
            raise ValueError("Scanned PDF needs local OCR; OCR is not silently delegated to an external service")
        return pages
    raise ValueError(f"Unsupported document extension: {suffix}")


def write_document(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".html", ".json", ".csv"}:
        path.write_text(str(data["text"]), encoding="utf-8")
    elif suffix == ".xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        book = Workbook()
        book.remove(book.active)
        names=list(data["sheets"])
        import re
        if not names or any(not n or len(n)>31 or re.search(r'[\\/*?:\[\]]',n) for n in names):
            raise ValueError("Excel sheet names must be nonempty, <=31 characters and contain no \\ / * ? : [ ]")
        if len({n.casefold() for n in names})!=len(names):
            raise ValueError("Excel sheet names must be unique ignoring case")
        for name, rows in data["sheets"].items():
            sheet = book.create_sheet(name)
            for row in rows:
                # Formulas must be explicitly supplied as {formula: "=..."}.
                sheet.append([v["formula"] if isinstance(v, dict) and "formula" in v
                              else ("'"+v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v)
                              for v in row])
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for c in sheet[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="24465D")
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = min(55, max(14, max(len(str(c.value or "")) for c in column)+2))
        book.save(path)
    elif suffix == ".docx":
        from docx import Document
        doc = Document()
        doc.add_heading(data.get("title", "Результат"), 0)
        for text in data.get("paragraphs", []):
            doc.add_paragraph(str(text))
        for table in data.get("tables", []):
            if not table:
                continue
            t = doc.add_table(rows=0, cols=max(map(len, table)))
            t.style = "Table Grid"
            for row in table:
                for cell, value in zip(t.add_row().cells, row):
                    cell.text = str(value)
        doc.save(path)
    elif suffix == ".pptx":
        from pptx import Presentation
        from pptx.util import Pt
        deck = Presentation()
        for item in data["slides"]:
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = item["title"]
            frame = slide.placeholders[1].text_frame
            frame.clear()
            for i, text in enumerate(item.get("bullets", [])):
                p = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
                p.text = str(text)
                p.font.size = Pt(20)
            slide.notes_slide.notes_text_frame.text = item.get("notes", "")
        deck.save(path)
    elif suffix == ".pdf":
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.utils import simpleSplit
        font_candidates = [Path("C:/Windows/Fonts/arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
        font = next((p for p in font_candidates if p.exists()), None)
        if font is None:
            raise RuntimeError("A Unicode TTF font is required for Russian PDF fixtures")
        pdfmetrics.registerFont(TTFont("FixtureUnicode", str(font)))
        c = canvas.Canvas(str(path))
        y = 800
        for line in [data.get("title", ""), *data.get("paragraphs", [])]:
            for part in simpleSplit(str(line), "FixtureUnicode", 11, 480):
                if y < 60:
                    c.showPage()
                    y = 800
                c.setFont("FixtureUnicode", 11)
                c.drawString(50, y, part)
                y -= 17
            y -= 8
        c.save()
    else:
        raise ValueError(f"Unsupported output extension: {suffix}")


def revise_contract(source: Path, target: Path, changes: list[dict]):
    """Real Word tracked revisions and anchored comments, preserving original document."""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document(source)
    from docx.text.paragraph import Paragraph
    paragraphs=[Paragraph(p,doc) for p in doc.element.body.iter(qn('w:p'))]
    selected=[]
    for change in changes:
        if not change.get('old'):
            raise ValueError("old clause must not be empty")
        matches=[p for p in paragraphs if change['old'] in p.text]
        if len(matches) != 1:
            raise ValueError("Each old clause must match exactly one paragraph")
        if matches[0].text.count(change['old'])!=1:
            raise ValueError("old clause occurs multiple times; provide a longer unique clause")
        if any(matches[0]._p is p._p for p in selected):
            raise ValueError("Multiple revisions in one paragraph require one combined replacement")
        if any(el.tag in {qn('w:ins'),qn('w:del')} for el in matches[0]._p.iter()):
            raise ValueError("Paragraph already has tracked revisions; refusing to erase existing revision history")
        selected.append(matches[0])
    ids=[int(el.get(qn('w:id'))) for el in doc.element.iter() if el.tag in {qn('w:ins'),qn('w:del')} and str(el.get(qn('w:id'),'')).isdigit()]
    next_id=max(ids,default=-1)+1
    for i,(change,p) in enumerate(zip(changes,selected)):
        before, after = p.text.split(change["old"], 1)
        p.clear()
        if before:
            p.add_run(before)
        for tag, texttag, text in [("del", "delText", change["old"]), ("ins", "t", change["new"])]:
            revision = OxmlElement("w:"+tag)
            revision.set(qn("w:id"), str(next_id+2*i + (tag == "ins")))
            revision.set(qn("w:author"), "Benchmark agent")
            revision.set(qn("w:date"), "2026-01-01T00:00:00Z")
            r = OxmlElement("w:r")
            t = OxmlElement("w:"+texttag)
            t.text = text
            t.set(qn("xml:space"), "preserve")
            r.append(t)
            revision.append(r)
            p._p.append(revision)
        anchor = p.add_run(after or " ")
        doc.add_comment(anchor, text=change["reason"]+"\nОснование: "+change["policy_ref"], author="Benchmark agent")
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.save(target)
