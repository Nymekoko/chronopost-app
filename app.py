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
    df = pd.read_csv(csv_file)
    order_items = {}
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
    return order_items


def get_page_references(pdf_bytes):
    page_refs = {}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for i, p in enumerate(pdf.pages):
                ref_val = None
                try:
                    text = p.extract_text() or ''
                    matches = re.findall(r'Reference\s*[:\-]?\s*(\d{4,6})', text)
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
