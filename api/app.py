#!/usr/bin/env python3
"""
Digitació API - Backend per OCR de partitures i renderitzat PDF amb digitació.

Endpoints:
  POST /api/ocr       - PDF partitura → MusicXML (via Audiveris)
  POST /api/render-pdf - MusicXML amb digitació → PDF (via Verovio)
"""

import json
import os
import shutil
import subprocess
import tempfile
import glob

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

AUDIVERIS_JAR = os.environ.get('AUDIVERIS_JAR', '/opt/audiveris/build/libs/audiveris.jar')
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

def find_audiveris():
    jar = AUDIVERIS_JAR
    if os.path.exists(jar):
        return jar
    for path in [
        '/opt/audiveris/build/libs/audiveris.jar',
        '/opt/audiveris/audiveris.jar',
        os.path.expanduser('~/audiveris/build/libs/audiveris.jar'),
    ]:
        if os.path.exists(path):
            return path
    return None


@app.route('/api/ocr', methods=['POST'])
def ocr():
    if 'file' not in request.files:
        return jsonify({'error': 'Cap fitxer enviat'}), 400

    file = request.files['file']
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Nomes es suporten fitxers PDF'}), 400

    jar = find_audiveris()
    if not jar:
        return jsonify({'error': 'Audiveris no esta instal·lat al servidor. Executa setup.sh primer.'}), 503

    tmpdir = tempfile.mkdtemp(prefix='digitacio_ocr_')
    try:
        pdf_path = os.path.join(tmpdir, 'input.pdf')
        file.save(pdf_path)

        if os.path.getsize(pdf_path) > MAX_FILE_SIZE:
            return jsonify({'error': 'Fitxer massa gran (max 20MB)'}), 413

        output_dir = os.path.join(tmpdir, 'output')
        os.makedirs(output_dir)

        cmd = [
            'xvfb-run', '-a',
            'java', '-Xmx2g',
            '-cp', jar,
            'Audiveris',
            '-batch',
            '-export',
            '-output', output_dir,
            '--', pdf_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            cwd=tmpdir,
        )

        mxl_files = glob.glob(os.path.join(output_dir, '**', '*.mxl'), recursive=True)
        xml_files = glob.glob(os.path.join(output_dir, '**', '*.musicxml'), recursive=True)
        xml_files += glob.glob(os.path.join(output_dir, '**', '*.xml'), recursive=True)

        target = None
        if mxl_files:
            import zipfile
            mxl = mxl_files[0]
            with zipfile.ZipFile(mxl) as z:
                for name in z.namelist():
                    if name.endswith('.xml') or name.endswith('.musicxml'):
                        target_path = os.path.join(tmpdir, 'extracted.musicxml')
                        with z.open(name) as src, open(target_path, 'wb') as dst:
                            dst.write(src.read())
                        target = target_path
                        break
        elif xml_files:
            target = xml_files[0]

        if not target:
            stderr_msg = result.stderr[-500:] if result.stderr else 'Sense detalls'
            return jsonify({
                'error': f'Audiveris no ha pogut reconèixer la partitura.',
                'details': stderr_msg,
            }), 422

        with open(target, 'r', encoding='utf-8') as f:
            musicxml = f.read()

        return jsonify({'musicxml': musicxml, 'source': 'audiveris'})

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'El processament ha trigat massa (timeout 3 min)'}), 504
    except Exception as e:
        return jsonify({'error': f'Error intern: {str(e)}'}), 500
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.route('/api/render-pdf', methods=['POST'])
def render_pdf():
    try:
        import verovio
    except ImportError:
        return jsonify({'error': 'Verovio no esta instal·lat. pip install verovio'}), 503

    data = request.get_json()
    if not data or 'musicxml' not in data:
        return jsonify({'error': 'Cal enviar musicxml al body JSON'}), 400

    musicxml = data['musicxml']
    title = data.get('title', 'digitacio')

    tmpdir = tempfile.mkdtemp(prefix='digitacio_render_')
    try:
        tk = verovio.toolkit()
        tk.setOptions(json.dumps({
            'pageWidth': 2100,
            'pageHeight': 2970,
            'scale': 40,
            'adjustPageHeight': False,
            'footer': 'none',
            'header': 'none',
            'spacingStaff': 8,
            'spacingSystem': 8,
        }))

        if not tk.loadData(musicxml):
            return jsonify({'error': 'Verovio no ha pogut processar el MusicXML'}), 422

        tk.redoLayout()
        page_count = tk.getPageCount()

        svgs = []
        for i in range(1, page_count + 1):
            svg = tk.renderToSVG(i)
            svgs.append(svg)

        try:
            import cairosvg
            from pypdf import PdfWriter, PdfReader
            import io

            writer = PdfWriter()
            for svg in svgs:
                pdf_bytes = cairosvg.svg2pdf(
                    bytestring=svg.encode('utf-8'),
                    output_width=595,  # A4 width in points
                    output_height=842,  # A4 height in points
                )
                reader = PdfReader(io.BytesIO(pdf_bytes))
                for page in reader.pages:
                    writer.add_page(page)

            output_path = os.path.join(tmpdir, f'{title}.pdf')
            with open(output_path, 'wb') as f:
                writer.write(f)

            return send_file(
                output_path,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f'{title}-digitacio.pdf',
            )

        except ImportError:
            svg_path = os.path.join(tmpdir, f'{title}.svg')
            with open(svg_path, 'w', encoding='utf-8') as f:
                f.write(svgs[0])
            return send_file(
                svg_path,
                mimetype='image/svg+xml',
                as_attachment=True,
                download_name=f'{title}-digitacio.svg',
            )

    except Exception as e:
        return jsonify({'error': f'Error renderitzant: {str(e)}'}), 500
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.route('/api/health', methods=['GET'])
def health():
    audiveris_ok = find_audiveris() is not None
    try:
        import verovio
        verovio_ok = True
    except ImportError:
        verovio_ok = False
    try:
        import cairosvg
        cairosvg_ok = True
    except ImportError:
        cairosvg_ok = False

    return jsonify({
        'status': 'ok',
        'audiveris': audiveris_ok,
        'verovio': verovio_ok,
        'cairosvg': cairosvg_ok,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5085, debug=False)
