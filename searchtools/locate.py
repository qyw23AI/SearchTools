import os
import re
import sys
from pathlib import Path

# Never add another Python interpreter's site-packages: native extensions are ABI-specific.
try:
    import fitz
except ImportError:
    fitz = None


def resolve_attachment_pdf(att_key: str, zot_key: str = "") -> Path | None:
    """Find the real absolute PDF path for a given attachment key or parent item key."""
    from .zotero import Library
    lib = Library()
    
    # Resolve the exact requested attachment through Zotero's configured storage.
    if att_key:
        try:
            from urllib.parse import urlparse, unquote
            u = urlparse(lib.client.file_url(att_key))
            path = Path(unquote(u.path))
            if u.scheme == 'file' and path.is_file() and path.suffix.lower() == '.pdf':
                return path
        except Exception:
            pass
        # Never silently switch to a different attachment of the same paper.
        return None

    # 2. Try library attachment info
    key_to_try = att_key or zot_key
    if key_to_try:
        try:
            path, _ = lib.attachment_info(key_to_try)
            if path and Path(path).exists() and Path(path).suffix.lower() == '.pdf':
                return Path(path)
        except Exception:
            pass

    # 3. Try zot_key if different
    if zot_key and zot_key != att_key:
        try:
            path, _ = lib.attachment_info(zot_key)
            if path and Path(path).exists() and Path(path).suffix.lower() == '.pdf':
                return Path(path)
        except Exception:
            pass
            
    return None


def normalize_with_map(text):
    """Normalize typography, spacing and line-break hyphens, retaining source offsets."""
    import unicodedata
    chars, offsets = [], []
    removed = set()
    for match in re.finditer(r'[-\u00ad]\s*\n\s*(?=\w)', text):
        removed.update(range(match.start(), match.end()))
    for i, char in enumerate(text):
        if i in removed or char == '\u00ad':
            continue
        for c in unicodedata.normalize('NFKC', char).casefold():
            if not c.isspace():
                chars.append(c)
                offsets.append(i)
    return ''.join(chars), offsets


def locate_quote_in_pdf(pdf_path: str | Path, quote: str, page_hint: str = '') -> dict:
    """Verify the complete quote; hints never resolve duplicate-source ambiguity.

    PDF blocks are layout paragraphs, not model-invented semantic paragraphs.
    Each span carries line rectangles and original block text for inspection.
    """
    import hashlib
    if fitz is None:
        import subprocess, json
        python = Path('/home/qyw/DeepTutor/.venv/bin/python')
        if python.is_file():
            try:
                result = subprocess.run([str(python), '-m', 'searchtools.locate'],
                    input=json.dumps({'pdf_path': str(pdf_path), 'quote': quote, 'page_hint': page_hint}),
                    text=True, capture_output=True, timeout=90, cwd=Path(__file__).resolve().parents[1], check=True)
                return json.loads(result.stdout)
            except Exception as exc:
                return {'version': 3, 'status': 'failed', 'rects': [], 'spans': [], 'error': str(exc)}
    failure = {'version': 3, 'status': 'unverified', 'page_index': None,
               'page_label': None, 'rects': [], 'spans': []}
    target, _ = normalize_with_map(quote or '')
    if len(target) < 16:
        return dict(failure, error='Quote too short for reliable attribution')
    if not fitz or not Path(pdf_path).is_file():
        return dict(failure, error='PDF missing or PyMuPDF unavailable')
    try:
        pdf_hash = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
        with fitz.open(str(pdf_path)) as doc:
            text, lines, paragraphs = '', [], {}
            for pi, page in enumerate(doc):
                for bi, block in enumerate(page.get_text('rawdict', sort=True)['blocks']):
                    if block.get('type') != 0:
                        continue
                    # A standalone page number in the footer is layout metadata.
                    # Keep numeric table/body blocks; only remove bottom-margin folios.
                    block_text = ''.join(char['c'] for line in block.get('lines', [])
                                         for span in line.get('spans', []) for char in span.get('chars', []))
                    if block_text.strip().isdigit() and block['bbox'][1] > page.rect.height - 65:
                        continue
                    pid = f'{pdf_hash[:16]}:p{pi}:b{bi}'
                    start = len(text)
                    for li, line in enumerate(block.get('lines', [])):
                        chars = [char for span in line['spans'] for char in span.get('chars', [])]
                        line_text = ''.join(char['c'] for char in chars)
                        a = len(text)
                        text += line_text + '\n'
                        # Line bounds are intentionally explicit, not character-tight boxes.
                        x0, y0, x1, y1 = line['bbox']
                        lines.append({'start': a, 'end': len(text)-1, 'page_index': pi,
                                      'paragraph_id': pid, 'line_index': li,
                                      'chars': chars,
                                      'rect': [x0, page.rect.height-y1, x1, page.rect.height-y0]})
                    paragraphs[pid] = {'text': text[start:], 'start': start,
                                       'end': len(text), 'page_index': pi}
            normalized, mapping = normalize_with_map(text)
            hits = []
            cursor = 0
            while True:
                pos = normalized.find(target, cursor)
                if pos < 0:
                    break
                hits.append(pos)
                cursor = pos + 1
            if len(hits) != 1:
                return dict(failure, status='ambiguous' if hits else 'unverified',
                            matches=len(hits), pdf_sha256=pdf_hash,
                            error='Complete quote is repeated' if hits else 'Complete quote not found')
            start, end = mapping[hits[0]], mapping[hits[0]+len(target)-1]+1
            spans = []
            for pid, para in paragraphs.items():
                selected = [ln for ln in lines if ln['paragraph_id'] == pid and
                            ln['start'] < end and ln['end'] > start]
                if not selected:
                    continue
                page = doc[para['page_index']]
                rects = []
                for ln in selected:
                    selected_chars = ln['chars'][max(0, start-ln['start']):max(0, end-ln['start'])]
                    if not selected_chars:
                        continue
                    boxes = [c['bbox'] for c in selected_chars]
                    rects.append([min(b[0] for b in boxes), page.rect.height-max(b[3] for b in boxes),
                                  max(b[2] for b in boxes), page.rect.height-min(b[1] for b in boxes)])
                spans.append({'paragraph_id': pid, 'page_index': para['page_index'],
                              'page_label': page.get_label() or str(para['page_index']+1),
                              'paragraph_text': para['text'],
                              'quote': text[max(start, para['start']):min(end, para['end'])],
                              'quote_start': max(start, para['start'])-para['start'],
                              'quote_end': min(end, para['end'])-para['start'],
                              'rects': rects,
                              'line_indices': [ln['line_index'] for ln in selected]})
            if any(doc[span['page_index']].rotation for span in spans):
                return dict(failure, error='Rotated PDF requires coordinate review', pdf_sha256=pdf_hash)
            first = spans[0]
            return {'version': 3, 'status': 'exact', 'pdf_sha256': pdf_hash,
                    'page_index': first['page_index'], 'page_label': first['page_label'],
                    'rects': first['rects'], 'spans': spans, 'quote': text[start:end],
                    'coordinate_system': 'pdf-bottom-left', 'geometry': 'character-range-line-unions'}
    except Exception as exc:
        return dict(failure, error=str(exc))


if __name__ == '__main__':
    import json
    request = json.load(sys.stdin)
    print(json.dumps(locate_quote_in_pdf(**request), ensure_ascii=False))
