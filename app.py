import io
import os
import re
import pandas as pd
import pdfplumber
from flask import Flask, request, send_file, render_template, jsonify
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024


def build_order_items(csv_file):
    # Auto-detect delimiter (Shopify uses ",", Chronopost import uses ";")
    # and strip BOM if present (utf-8-sig)
    df = pd.read_csv(csv_file, sep=None, engine='python', encoding='utf-8-sig')

    order_items = {}

    if 'Name' in df.columns and 'Lineitem name' in df.columns:
        # Raw Shopify orders export format
        for name, group in df.groupby('Name'):
            try:
                ref = int(str(name).replace('#', '').strip())
            except ValueError:
                continue
            items = []
            for _, row in group.iterrows():
                try:
                    qty = int(row['Lineitem quantity'])
                    item = str(row['Lineitem name'])
                    items.append(f'{qty}x {item}')
                except Exception:
                    pass
            order_items[ref] = items

    elif 'Référence' in df.columns and 'Description du contenu' in df.columns:
        # Chronopost import CSV format (already converted from Shopify)
        for _, row in df.iterrows():
            try:
                ref = int(str(row['Référence']).strip())
            except (ValueError, TypeError):
                continue
            content = str(row['Description du contenu']).strip()
            if content and content.lower() != 'nan':
                order_items.setdefault(ref, []).append(content)

    else:
        raise ValueError(
            f"Format CSV non reconnu. Colonnes trouvées: {list(df.columns)}"
        )

    return order_items


def get_page_references(pdf_bytes):
    page_refs = {}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for i, p in enumerate(pdf.pages):
                ref_val = None
                try:
                    text = p.extract_text() or ''
                    # Find all occurrences of "Commande #1234"
                    # (Chronopost labels use "Référence : Commande #XXXX",
                    # extracted text often breaks across lines, so we match
                    # on "Commande #" which is stable across all label formats)
                    matches = re.findall(r'Commande\s*#\s*(\d{3,6})', text)
                    if matches:
                        ref_val = int(matches[0])
                except Exception:
                    pass
                page_refs[i] = ref_val
    except Exception:
        pass
    return page_refs


def add_articles_to_pdf(pdf_bytes, order_items, page_refs):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()

    PAGE_W, PAGE_H = 842, 595
    BOX_X1, BOX_X2 = 514, 822
    BOX_PDF_BOTTOM, BOX_PDF_TOP = 571, 593
    BOX_HEIGHT = BOX_PDF_TOP - BOX_PDF_BOTTOM

    matched, unmatched = 0, 0

    for page_idx in range(len(reader.pages)):
        ref = page_refs.get(page_idx)
        items = order_items.get(ref, []) if ref else []

        if items:
            matched += 1
        elif ref:
            unmatched += 1

        try:
            packet = io.BytesIO()
            c = canvas.Canvas(packet, pagesize=(PAGE_W, PAGE_H))

            if items:
                n = len(items)
                if n == 1:
                    font_size, line_height = 9, 10
                elif n == 2:
                    font_size, line_height = 8, 9
                elif n == 3:
                    font_size, line_height = 7, 8
                else:
                    font_size, line_height = 6.5, 7.5

                c.setFillColorRGB(1.0, 0.95, 0.8)
                c.setStrokeColorRGB(0.85, 0.4, 0.0)
                c.setLineWidth(0.6)
                c.rect(BOX_X1, BOX_PDF_BOTTOM, BOX_X2 - BOX_X1, BOX_HEIGHT, fill=1, stroke=1)

                c.setFillColorRGB(0.6, 0.15, 0.0)
                c.setFont("Helvetica-Bold", font_size)

                total_text_h = n * line_height
                start_y = BOX_PDF_BOTTOM + (BOX_HEIGHT + total_text_h) / 2 - line_height + 1
                max_chars = int((BOX_X2 - BOX_X1 - 8) / (font_size * 0.52))

                for i, item_text in enumerate(items):
                    y = start_y - i * line_height
                    if len(item_text) > max_chars:
                        item_text = item_text[:max_chars - 1] + '...'
                    c.drawString(BOX_X1 + 4, y, item_text)

            c.save()
            packet.seek(0)

            overlay_reader = PdfReader(packet)
            original_page = reader.pages[page_idx]
            original_page.merge_page(overlay_reader.pages[0])
            writer.add_page(original_page)

        except Exception:
            # If overlay fails for a page, add original unchanged
            writer.add_page(reader.pages[page_idx])

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)
    return output, matched, unmatched

@app.route('/debug', methods=['POST'])
def debug():
    if 'pdf' not in request.files or 'csv' not in request.files:
        return
