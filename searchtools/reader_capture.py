"""Validate native Reader selections before they can become source evidence."""
import math


def validate_position(position):
    if not isinstance(position, dict):
        raise ValueError('缺少阅读器选区坐标')
    page = position.get('pageIndex')
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        raise ValueError('阅读器物理页码无效')
    groups = [(page, position.get('rects'))]
    if position.get('nextPageRects'):
        groups.append((page + 1, position['nextPageRects']))
    for _, rects in groups:
        if not isinstance(rects, list) or not rects or len(rects) > 200:
            raise ValueError('缺少或过量的阅读器选区矩形')
        for rect in rects:
            if (not isinstance(rect, list) or len(rect) != 4 or
                    any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in rect) or
                    rect[0] >= rect[2] or rect[1] >= rect[3]):
                raise ValueError('阅读器选区矩形无效')
    return dict(groups)


def verify_selection_location(position, location):
    """Require both a verbatim PDF match and agreement with the native selection."""
    groups = validate_position(position)
    if location.get('status') != 'exact':
        raise ValueError('划选引文未能在 PDF 中唯一回验：' + str(location.get('status')))
    spans = location.get('spans') or []
    if set(groups) != {span['page_index'] for span in spans}:
        raise ValueError('划选物理页码与原文回验不一致')
    for span in spans:
        for rect in span['rects']:
            area = (rect[2]-rect[0]) * (rect[3]-rect[1])
            coverage = 0
            for native in groups[span['page_index']]:
                # Different PDF text engines can differ slightly in glyph metrics.
                overlap = max(0, min(rect[2], native[2]+2)-max(rect[0], native[0]-2)) * max(0, min(rect[3], native[3]+2)-max(rect[1], native[1]-2))
                coverage += overlap
            if area <= 0 or coverage / area < 0.8:
                raise ValueError('划选矩形与引文所在原文不一致')
    return True
