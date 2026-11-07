import os
import re
import sys
from pathlib import Path

# Ensure PyMuPDF is importable
try:
    import fitz
except ImportError:
    for p in ['/home/qyw/DeepTutor/.venv/lib/python3.11/site-packages']:
        if os.path.exists(p) and p not in sys.path:
            sys.path.insert(0, p)
    try:
        import fitz
    except ImportError:
        fitz = None


def resolve_attachment_pdf(att_key: str, zot_key: str = "") -> Path | None:
    """Find the real absolute PDF path for a given attachment key or parent item key."""
    from .zotero import Library
    lib = Library()
    
    # 1. Check direct storage folder
    if att_key:
        att_dir = Path.home() / 'Zotero' / 'storage' / att_key
        if att_dir.is_dir():
            pdfs = list(att_dir.glob('*.pdf'))
            if pdfs:
                return pdfs[0]

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


def locate_quote_in_pdf(pdf_path: str | Path, quote: str, page_hint: str = "") -> dict:
    """
    Search for quote in PDF and return exact page_index (0-based) and
    bounding boxes in Zotero coordinate format (bottom-left origin).
    """
    pdf_p = Path(pdf_path)
    if not pdf_p.exists() or not fitz:
        return {
            'status': 'failed',
            'page_index': 0,
            'page_label': '1',
            'rects': [],
            'error': f'PDF not found or fitz unavailable: {pdf_path}'
        }

    try:
        doc = fitz.open(str(pdf_p))
    except Exception as e:
        return {
            'status': 'failed',
            'page_index': 0,
            'page_label': '1',
            'rects': [],
            'error': str(e)
        }

    total_pages = len(doc)
    if total_pages == 0:
        return {'status': 'failed', 'page_index': 0, 'page_label': '1', 'rects': [], 'error': 'Empty PDF'}

    # Parse page hints (e.g. "5-6" -> [4, 5], "7" -> [6])
    hint_indices = []
    if page_hint:
        for seg in re.split(r'[,-]', str(page_hint).strip()):
            try:
                val = int(seg.strip()) - 1
                if 0 <= val < total_pages and val not in hint_indices:
                    hint_indices.append(val)
            except Exception:
                pass

    fallback_page = hint_indices[0] if hint_indices else 0

    # Clean and normalize quote
    clean_q = re.sub(r'[\r\n\t]+', ' ', quote or '').strip()
    clean_q = re.sub(r'\s{2,}', ' ', clean_q)
    if not clean_q:
        return {
            'status': 'page_only',
            'page_index': fallback_page,
            'page_label': str(fallback_page + 1),
            'rects': []
        }

    # Generate multi-level search candidate phrases
    words = clean_q.split()
    candidates = []
    if len(words) >= 6:
        # Full first sentence or first 12 words
        first_clause = clean_q.split('.')[0].strip()
        if len(first_clause.split()) >= 4:
            candidates.append(first_clause)
        candidates.append(' '.join(words[:12]))
        candidates.append(' '.join(words[len(words)//2:len(words)//2 + 10]))
        candidates.append(' '.join(words[-12:]))
        candidates.append(' '.join(words[:6]))
    else:
        candidates.append(clean_q)

    # Clean punctuation for fallback matching
    candidates.extend([re.sub(r'[^\w\s]', '', c) for c in candidates[:3]])

    # Search order: hint pages first, then ±2 pages around hints, then remainder
    priority_pages = []
    for h in hint_indices:
        for offset in (0, -1, 1, -2, 2):
            p = h + offset
            if 0 <= p < total_pages and p not in priority_pages:
                priority_pages.append(p)
    full_order = priority_pages + [p for p in range(total_pages) if p not in priority_pages]

    for p_idx in full_order:
        page = doc[p_idx]
        h = page.rect.height
        
        for cand in candidates:
            if not cand or len(cand.strip()) < 4:
                continue
            rects = page.search_for(cand.strip())
            if not rects and len(cand) > 25:
                # Try prefix
                rects = page.search_for(cand[:24].strip())
                
            if rects:
                # Convert fitz Rect (top-left origin) to Zotero rect (bottom-left origin)
                z_rects = [
                    [
                        round(float(r.x0), 2),
                        round(float(h - r.y1), 2),
                        round(float(r.x1), 2),
                        round(float(h - r.y0), 2)
                    ]
                    for r in rects
                ]
                match_status = 'exact' if (p_idx in hint_indices or len(cand) >= 15) else 'fuzzy'
                return {
                    'status': match_status,
                    'page_index': p_idx,
                    'page_label': str(p_idx + 1),
                    'rects': z_rects,
                    'matched_phrase': cand[:40]
                }

    # If no text search matched, return page_only
    return {
        'status': 'page_only',
        'page_index': fallback_page,
        'page_label': str(fallback_page + 1),
        'rects': []
    }
