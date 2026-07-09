import io
import os
import re
import pandas as pd
from flask import Flask, request, send_file, render_template, jsonify
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024


def build_order_items(csv_file):
    df = pd.read_csv(csv_file, sep=None, engine='python', encoding='utf-8-sig')
    order_items = {}

    if 'Name' in df.columns and 'Lineitem name' in df.columns:
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
        for _, row in df.iterrows():
            try:
                ref = int(str(row['Référence']).strip())
            except (ValueError, TypeError):
                continue
            content = str(row['Description du contenu']).strip()
            if content and content.lower() != 'nan':
                order_items.setdefault(ref, []).append(content)
    else:
        raise ValueError(f"Format CSV non reconnu. Colonnes trouvées: {list(df.columns)}")

    return order_items


# FIX #1 : pypdf au lieu de pdfplumber (5x plus rapide : 5.6s vs 30s sur 216 pages,
# évite le timeout Railway) + regex élargi pour matcher les deux formats d'étiquettes :
#   - ancien format : "Reference : Commande #6969"
#   - nouveau format (Shop2Shop / international) : "Référence : 7202" ou "Reference : 7202"
REF_PATTERNS = [
    re.compile(r'Commande\s*#\s*(\d{3,6})'),
    re.compile(r'R[ée]f[ée]rence\s*:\s*(\d{3,6})'),
]

def get_page_references(reader):
    page_refs = {}
    for i, p in enumerate(reader.pages):
        ref_val = None
        try:
            text = p.extract_text() or ''
            for pattern in REF_PATTERNS:
                m = pattern.search(text)
                if m:
                    ref_val = int(m.group(1))
                    break
        except Exception:
            pass
        page_refs[i] = ref_val
    return page_refs


# FIX #2 : la taille/position du canvas d'overlay s'adapte au mediabox RÉEL de chaque
# page au lieu d'un 842x595 codé en dur. Sur les étiquettes "cropped" (mediabox
# décalé et plus petit, ex: 318x507 avec origine à 508,84), l'ancien code plantait
# systématiquement sur merge_page() ("Sequence index out of range"), rattrapé par le
# try/except mais consommant du temps/mémoire pour rien sur les 216 pages.
def add_articles_to_pdf(reader, order_items, page_refs):
    writer = PdfWriter()
    matched, unmatched = 0, 0

    for page_idx in range(len(reader.pages)):
        ref = page_refs.get(page_idx)
        items = order_items.get(ref, []) if ref else []
        original_page = reader.pages[page_idx]

        if items:
            matched += 1
        elif ref:
            unmatched += 1

        if not items:
            # Rien à dessiner : on ajoute la page telle quelle, pas besoin de
            # canvas/merge (gain de temps + évite un merge inutile)
            writer.add_page(original_page)
            continue

        try:
            mb = original_page.mediabox
            W, H = float(mb.width), float(mb.height)

            n = len(items)
            if n == 1:
                font_size, line_height = 9, 10
            elif n == 2:
                font_size, line_height = 8, 9
            elif n == 3:
                font_size, line_height = 7, 8
            else:
                font_size, line_height = 6.5, 7.5

            box_w = min(W - 8, 260)
            box_h = n * line_height + 6
            box_x1 = 4
            box_x2 = box_x1 + box_w
            box_top = H - 4
            box_bottom = box_top - box_h

            packet = io.BytesIO()
            c = canvas.Canvas(packet, pagesize=(W, H))
            c.setFillColorRGB(1.0, 0.95, 0.8)
            c.setStrokeColorRGB(0.85, 0.4, 0.0)
            c.setLineWidth(0.6)
            c.rect(box_x1, box_bottom, box_x2 - box_x1, box_h, fill=1, stroke=1)

            c.setFillColorRGB(0.6, 0.15, 0.0)
            c.setFont("Helvetica-Bold", font_size)

            max_chars = int((box_x2 - box_x1 - 8) / (font_size * 0.52))
            start_y = box_top - line_height + 1
            for i, item_text in enumerate(items):
                y = start_y - i * line_height
                if len(item_text) > max_chars:
                    item_text = item_text[:max_chars - 1] + '...'
                c.drawString(box_x1 + 4, y, item_text)

            c.save()
            packet.seek(0)

            # FIX #3 : le calque overlay est dessiné en coordonnées locales (0,0)->(W,H),
            # mais le mediabox de la page originale a une origine décalée (ex: 508.89, 84.62
            # au lieu de 0,0) — son contenu visible utilise donc des coordonnées ABSOLUES
            # dans cette plage. Un simple merge_page() plaçait l'overlay hors du mediabox
            # visible : le texte existait dans le PDF (extractible) mais n'était jamais
            # affiché/imprimé. On translate l'overlay avec merge_transformed_page() pour
            # le recaler exactement sur l'origine réelle du mediabox.
            overlay_reader = PdfReader(packet)
            offset_x, offset_y = float(mb.left), float(mb.bottom)
            original_page.merge_transformed_page(
                overlay_reader.pages[0], (1, 0, 0, 1, offset_x, offset_y)
            )
            writer.add_page(original_page)

        except Exception:
            writer.add_page(original_page)

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)
    return output, matched, unmatched


@app.route('/debug', methods=['POST'])
def debug():
    if 'pdf' not in request.files or 'csv' not in request.files:
        return jsonify({'error': 'missing files'}), 400
    pdf_bytes = request.files['pdf'].read()
    csv_file = request.files['csv']
    reader = PdfReader(io.BytesIO(pdf_bytes))
    order_items = build_order_items(csv_file)
    page_refs = get_page_references(reader)
    sample = {str(k): v for k, v in list(page_refs.items())[:5]}
    csv_sample = {str(k): v for k, v in list(order_items.items())[:5]}
    matched = sum(1 for ref in page_refs.values() if ref and order_items.get(ref))
    return jsonify({
        'page_refs': sample,
        'csv_orders': csv_sample,
        'matched': matched,
        'total_pages': len(page_refs),
        'total_orders': len(order_items)
    })


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/process', methods=['POST'])
def process():
    if 'pdf' not in request.files or 'csv' not in request.files:
        return jsonify({'error': 'Fichiers PDF et CSV requis'}), 400

    pdf_file = request.files['pdf']
    csv_file = request.files['csv']

    if not pdf_file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Le fichier doit être un PDF'}), 400
    if not csv_file.filename.lower().endswith('.csv'):
        return jsonify({'error': 'Le fichier doit être un CSV'}), 400

    try:
        pdf_bytes = pdf_file.read()
        reader = PdfReader(io.BytesIO(pdf_bytes))
        order_items = build_order_items(csv_file)
        page_refs = get_page_references(reader)
        output_pdf, matched, unmatched = add_articles_to_pdf(reader, order_items, page_refs)

        filename = pdf_file.filename.replace('.pdf', '_avec_articles.pdf')
        response = send_file(
            output_pdf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
        response.headers['X-Matched'] = str(matched)
        response.headers['X-Unmatched'] = str(unmatched)
        response.headers['X-Total'] = str(len(page_refs))
        response.headers['Access-Control-Expose-Headers'] = 'X-Matched, X-Unmatched, X-Total'
        return response

    except Exception as e:
        return jsonify({'error': f'Erreur de traitement: {str(e)}'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
