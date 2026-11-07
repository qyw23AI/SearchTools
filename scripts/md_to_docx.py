#!/usr/bin/env python3
"""
Convert Markdown (.md) documents to beautifully styled academic Word (.docx) files.
Tailored for Chinese graduation thesis proposals, literature reviews, and research papers.
Features:
- Chinese / English dual-font typography (SimSun / Times New Roman; SimHei for headings)
- Standard academic three-line tables (三线表)
- Math equation rendering & KaTeX symbol translation
- Blockquote & GitHub-style callout alert boxes
- Multi-level heading hierarchy & TOC-friendly outline
"""

import sys
import os
import re
from pathlib import Path
import docx
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

def set_cell_borders(cell, top=None, bottom=None, left=None, right=None):
    """Set custom borders on a table cell using oxml."""
    tc = cell._element
    tcPr = tc.get_or_add_tcPr()
    tcBorders = parse_xml(f'<w:tcBorders {nsdecls("w")}/>')
    
    borders = {'top': top, 'bottom': bottom, 'left': left, 'right': right}
    for border_name, border_style in borders.items():
        if border_style:
            # border_style: (val, sz, space, color) e.g. ('single', '12', '0', '000000')
            val, sz, space, color = border_style
            b_el = parse_xml(f'<w:{border_name} {nsdecls("w")} w:val="{val}" w:sz="{sz}" w:space="{space}" w:color="{color}"/>')
            tcBorders.append(b_el)
        else:
            b_el = parse_xml(f'<w:{border_name} {nsdecls("w")} w:val="none"/>')
            tcBorders.append(b_el)
    tcPr.append(tcBorders)

def set_cell_shading(cell, color_hex):
    """Set cell background color."""
    tc = cell._element
    tcPr = tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}"/>')
    tcPr.append(shd)

def set_run_fonts(run, ascii_font='Times New Roman', east_asia_font='宋体', size_pt=12, bold=False, italic=False, color=None):
    run.font.name = ascii_font
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    run.font.italic = italic
    if color:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = parse_xml(f'<w:rFonts {nsdecls("w")} w:ascii="{ascii_font}" w:hAnsi="{ascii_font}" w:eastAsia="{east_asia_font}" w:cs="{ascii_font}"/>')
    rPr.append(rFonts)

def clean_math_syntax(math_str):
    """Translate LaTeX math expressions into human-readable Unicode mathematical expressions."""
    s = math_str.strip()
    replacements = [
        (r'\\mathcal\{([A-Za-z])\}', r'\1'),
        (r'\\mathbb\{([A-Za-z])\}', r'\1'),
        (r'\\mathbf\{([A-Za-z0-9])\}', r'\1'),
        (r'\\text\{([^}]+)\}', r'\1'),
        (r'\\mathrm\{([^}]+)\}', r'\1'),
        (r'\\pm', '±'),
        (r'\\leq', '≤'),
        (r'\\geq', '≥'),
        (r'\\neq', '≠'),
        (r'\\approx', '≈'),
        (r'\\in', '∈'),
        (r'\\notin', '∉'),
        (r'\\times', '×'),
        (r'\\cdot', '·'),
        (r'\\Delta', 'Δ'),
        (r'\\tau', 'τ'),
        (r'\\lambda', 'λ'),
        (r'\\alpha', 'α'),
        (r'\\beta', 'β'),
        (r'\\gamma', 'γ'),
        (r'\\theta', 'θ'),
        (r'\\sigma', 'σ'),
        (r'\\mu', 'μ'),
        (r'\\omega', 'ω'),
        (r'\\Omega', 'Ω'),
        (r'\\pi', 'π'),
        (r'\\infty', '∞'),
        (r'\\sum_\{([^}]+)\}\^\{([^}]+)\}', r'∑(\1 to \2)'),
        (r'\\sum', '∑'),
        (r'\\int', '∫'),
        (r'\\partial', '∂'),
        (r'\\nabla', '∇'),
        (r'\\to', '→'),
        (r'\\rightarrow', '→'),
        (r'\\leftarrow', '←'),
        (r'\\Leftarrow', '⇐'),
        (r'\\Rightarrow', '⇒'),
        (r'\\Leftrightarrow', '⇔'),
        (r'\\frac\{([^}]+)\}\{([^}]+)\}', r'(\1)/(\2)'),
        (r'\\sqrt\{([^}]+)\}', r'√(\1)'),
        (r'\^\{([^}]+)\}', r'^\1'),
        (r'_\{([^}]+)\}', r'_\1'),
    ]
    for pattern, repl in replacements:
        s = re.sub(pattern, repl, s)
    s = s.replace(r'\,', ' ').replace(r'\;', ' ').replace(r'\quad', '    ').replace(r'\!', '')
    s = s.replace('\\{', '{').replace('\\}', '}').replace(r'\_', '_')
    return s

def add_inline_runs(paragraph, text, base_font='Times New Roman', base_ea='宋体', base_size=12, base_color=None):
    """Parse inline markdown (bold, italic, code, math, links) and append runs with proper typography."""
    token_pattern = re.compile(
        r'(\$\$.*?\$\$|\$.*?\$|\*\*.*?\*\*|\*.*?\*|`.*?`|\[.*?\]\(.*?\))'
    )
    tokens = token_pattern.split(text)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith('$$') and tok.endswith('$$'):
            math_content = clean_math_syntax(tok[2:-2])
            r = paragraph.add_run(f' {math_content} ')
            set_run_fonts(r, 'Cambria Math', '宋体', base_size, italic=True, color=RGBColor(0x1a, 0x36, 0x5d))
        elif tok.startswith('$') and tok.endswith('$'):
            math_content = clean_math_syntax(tok[1:-1])
            r = paragraph.add_run(f' {math_content} ')
            set_run_fonts(r, 'Cambria Math', '宋体', base_size, italic=True, color=RGBColor(0x1a, 0x36, 0x5d))
        elif tok.startswith('**') and tok.endswith('**'):
            r = paragraph.add_run(tok[2:-2])
            set_run_fonts(r, base_font, '黑体' if base_ea == '宋体' else base_ea, base_size, bold=True, color=base_color)
        elif tok.startswith('*') and tok.endswith('*'):
            r = paragraph.add_run(tok[1:-1])
            set_run_fonts(r, base_font, base_ea, base_size, italic=True, color=base_color)
        elif tok.startswith('`') and tok.endswith('`'):
            r = paragraph.add_run(tok[1:-1])
            set_run_fonts(r, 'Consolas', '仿宋', base_size - 0.5, color=RGBColor(0x8a, 0x3b, 0x14))
        elif tok.startswith('[') and '](' in tok and tok.endswith(')'):
            m = re.match(r'\[(.*?)\]\((.*?)\)', tok)
            link_text = m.group(1) if m else tok
            r = paragraph.add_run(link_text)
            set_run_fonts(r, base_font, base_ea, base_size, color=RGBColor(0x1d, 0x4e, 0xd8))
        else:
            r = paragraph.add_run(tok)
            set_run_fonts(r, base_font, base_ea, base_size, color=base_color)

def parse_markdown_to_docx(md_content, output_docx_path):
    doc = docx.Document()
    
    # Page Setup (A4, Standard 2.54cm margins)
    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)
        section.page_width = Inches(8.27)  # A4 210mm
        section.page_height = Inches(11.69) # A4 297mm
        
        # Header / Footer
        header = section.header
        p_hdr = header.paragraphs[0]
        p_hdr.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r_hdr = p_hdr.add_run('毕业设计（论文）开题报告 · 面向复杂障碍环境的脉冲强化学习机器人运动控制方法研究')
        set_run_fonts(r_hdr, 'Times New Roman', '宋体', 9, color=RGBColor(0x71, 0x80, 0x96))

    lines = md_content.splitlines()
    in_yaml = False
    in_code = False
    code_lang = ''
    code_lines = []
    in_table = False
    table_lines = []
    in_math_block = False
    math_lines = []

    def flush_table():
        nonlocal table_lines, in_table
        if not table_lines:
            return
        # Parse table markdown
        rows_data = []
        for l in table_lines:
            l = l.strip()
            if not l:
                continue
            if re.match(r'^[\|\s:\-]+$', l):
                continue
            cells = [c.strip() for c in l.split('|')]
            if len(cells) >= 2 and cells[0] == '':
                cells = cells[1:]
            if len(cells) >= 1 and cells[-1] == '':
                cells = cells[:-1]
            if cells:
                rows_data.append(cells)
        table_lines = []
        in_table = False
        if not rows_data:
            return

        cols_count = max(len(r) for r in rows_data)
        # Normalize row lengths
        for r in rows_data:
            while len(r) < cols_count:
                r.append('')

        table = doc.add_table(rows=len(rows_data), cols=cols_count)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = True

        # Academic Three-Line Table (三线表)
        # Top & bottom: 1.5pt ('single', '12', '0', '000000')
        # Header divider: 0.75pt ('single', '6', '0', '000000')
        border_top = ('single', '12', '0', '1A202C')
        border_bottom = ('single', '12', '0', '1A202C')
        border_mid = ('single', '6', '0', '1A202C')

        for r_idx, row in enumerate(table.rows):
            is_header = (r_idx == 0)
            is_last = (r_idx == len(table.rows) - 1)
            for c_idx, cell in enumerate(row.cells):
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
                # Set borders
                top_b = border_top if is_header else None
                bot_b = border_bottom if is_last else (border_mid if is_header else None)
                set_cell_borders(cell, top=top_b, bottom=bot_b, left=None, right=None)

                # Shading
                if is_header:
                    set_cell_shading(cell, 'F1F5F9')
                elif r_idx % 2 == 1:
                    set_cell_shading(cell, 'F8FAFC')

                cell_text = rows_data[r_idx][c_idx]
                p = cell.paragraphs[0]
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.space_after = Pt(3)
                if is_header:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    add_inline_runs(p, cell_text, 'Times New Roman', '黑体', 10, RGBColor(0x0f, 0x17, 0x2a))
                    for r in p.runs:
                        r.font.bold = True
                else:
                    if len(cell_text) < 15 and (c_idx == 0 or cell_text.replace('.', '').isdigit() or '%' in cell_text):
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    else:
                        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                    add_inline_runs(p, cell_text, 'Times New Roman', '宋体', 9.5)

        # Space after table
        p_sp = doc.add_paragraph()
        p_sp.paragraph_format.space_before = Pt(4)
        p_sp.paragraph_format.space_after = Pt(4)

    def flush_code():
        nonlocal code_lines, code_lang, in_code
        if not code_lines:
            in_code = False
            return
        tbl = doc.add_table(rows=1, cols=1)
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        cell = tbl.cell(0, 0)
        set_cell_shading(cell, 'F8FAFC')
        border_code = ('single', '4', '0', 'CBD5E1')
        set_cell_borders(cell, top=border_code, bottom=border_code, left=border_code, right=border_code)
        
        p = cell.paragraphs[0]
        p.paragraph_format.line_spacing = 1.1
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(4)
        raw_code = '\n'.join(code_lines)
        r = p.add_run(raw_code)
        set_run_fonts(r, 'Consolas', '仿宋', 9, color=RGBColor(0x1e, 0x29, 0x3b))
        code_lines = []
        in_code = False
        doc.add_paragraph().paragraph_format.space_after = Pt(4)

    def flush_math():
        nonlocal math_lines, in_math_block
        if not math_lines:
            in_math_block = False
            return
        raw_math = '\n'.join(math_lines)
        clean_text = clean_math_syntax(raw_math)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(6)
        r = p.add_run(clean_text)
        set_run_fonts(r, 'Cambria Math', '宋体', 11.5, italic=True, color=RGBColor(0x0f, 0x17, 0x2a))
        math_lines = []
        in_math_block = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Handle YAML frontmatter
        if i == 0 and stripped == '---':
            in_yaml = True
            i += 1
            continue
        if in_yaml:
            if stripped == '---':
                in_yaml = False
            i += 1
            continue

        # Handle Code blocks
        if stripped.startswith('```') or stripped.startswith('````'):
            if in_code:
                flush_code()
            else:
                if in_table:
                    flush_table()
                in_code = True
                code_lang = stripped.lstrip('`').strip()
                code_lines = []
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue

        # Handle Math block $$
        if stripped.startswith('$$') and stripped.endswith('$$') and len(stripped) > 2:
            if in_table:
                flush_table()
            math_lines = [stripped[2:-2]]
            flush_math()
            i += 1
            continue
        elif stripped == '$$':
            if in_math_block:
                flush_math()
            else:
                if in_table:
                    flush_table()
                in_math_block = True
                math_lines = []
            i += 1
            continue
        if in_math_block:
            math_lines.append(line)
            i += 1
            continue

        # Handle Tables
        if stripped.startswith('|') and '|' in stripped[1:]:
            in_table = True
            table_lines.append(line)
            i += 1
            continue
        else:
            if in_table:
                flush_table()

        # Empty lines
        if not stripped:
            i += 1
            continue

        # Horizontal rules
        if stripped in ('---', '***', '___'):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(6)
            r = p.add_run('—' * 32)
            set_run_fonts(r, 'Times New Roman', '宋体', 10, color=RGBColor(0x94, 0xa3, 0xb8))
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            i += 1
            continue

        # Headings
        if stripped.startswith('# '):
            title_text = stripped[2:].strip()
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(16)
            p.paragraph_format.space_after = Pt(12)
            p.paragraph_format.keep_with_next = True
            add_inline_runs(p, title_text, 'Times New Roman', '黑体', 18, RGBColor(0x0f, 0x17, 0x2a))
            for r in p.runs:
                r.font.bold = True
            i += 1
            continue
        elif stripped.startswith('## '):
            heading_text = stripped[3:].strip()
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(14)
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.keep_with_next = True
            add_inline_runs(p, heading_text, 'Times New Roman', '黑体', 14, RGBColor(0x0f, 0x17, 0x2a))
            for r in p.runs:
                r.font.bold = True
            i += 1
            continue
        elif stripped.startswith('### '):
            heading_text = stripped[4:].strip()
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(4)
            p.paragraph_format.keep_with_next = True
            add_inline_runs(p, heading_text, 'Times New Roman', '黑体', 12, RGBColor(0x1e, 0x29, 0x3b))
            for r in p.runs:
                r.font.bold = True
            i += 1
            continue
        elif stripped.startswith('#### '):
            heading_text = stripped[5:].strip()
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.keep_with_next = True
            add_inline_runs(p, heading_text, 'Times New Roman', '黑体', 11, RGBColor(0x33, 0x41, 0x55))
            for r in p.runs:
                r.font.bold = True
            i += 1
            continue

        # Blockquote / Alert
        if stripped.startswith('>'):
            quote_text = re.sub(r'^>\s?', '', stripped)
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.4)
            p.paragraph_format.right_indent = Inches(0.2)
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(4)
            p.paragraph_format.line_spacing = 1.2
            
            # Check for GitHub-style alert
            if quote_text.startswith('[!NOTE]') or quote_text.startswith('[!TIP]'):
                quote_text = '【提示】' + quote_text[7:]
                color = RGBColor(0x03, 0x69, 0xa1)
            elif quote_text.startswith('[!IMPORTANT]'):
                quote_text = '【重要】' + quote_text[12:]
                color = RGBColor(0x7c, 0x2d, 0x12)
            elif quote_text.startswith('[!WARNING]') or quote_text.startswith('[!CAUTION]'):
                quote_text = '【警告】' + quote_text[10:]
                color = RGBColor(0xb9, 0x1c, 0x1c)
            else:
                color = RGBColor(0x33, 0x41, 0x55)
                
            add_inline_runs(p, quote_text, 'Times New Roman', '楷体', 10.5, color)
            i += 1
            continue

        # Lists
        m_bullet = re.match(r'^(\s*)[-*+]\s+(.*)$', line)
        m_num = re.match(r'^(\s*)(\d+)\.\s+(.*)$', line)
        if m_bullet:
            indent_level = len(m_bullet.group(1)) // 2
            list_text = m_bullet.group(2)
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.25 * (indent_level + 1))
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.25
            r_bullet = p.add_run('• ')
            set_run_fonts(r_bullet, 'Times New Roman', '宋体', 11, bold=True)
            add_inline_runs(p, list_text, 'Times New Roman', '宋体', 11)
            i += 1
            continue
        elif m_num:
            indent_level = len(m_num.group(1)) // 2
            num_str = m_num.group(2)
            list_text = m_num.group(3)
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.25 * (indent_level + 1))
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.25
            r_num = p.add_run(f'{num_str}. ')
            set_run_fonts(r_num, 'Times New Roman', '宋体', 11, bold=True)
            add_inline_runs(p, list_text, 'Times New Roman', '宋体', 11)
            i += 1
            continue

        # Normal paragraph
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.line_spacing = 1.25
        # First-line indent for Chinese prose paragraphs (unless it starts with English/bracket/tag)
        if len(stripped) > 0 and ('\u4e00' <= stripped[0] <= '\u9fff'):
            p.paragraph_format.first_line_indent = Pt(22)
        add_inline_runs(p, stripped, 'Times New Roman', '宋体', 11)
        i += 1

    if in_table:
        flush_table()
    if in_code:
        flush_code()
    if in_math_block:
        flush_math()

    output_path = Path(output_docx_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    print(f"Successfully generated DOCX: {output_path} (size: {output_path.stat().st_size} bytes)")
    return str(output_path)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python md_to_docx.py <input_md_file> [output_docx_file]")
        sys.exit(1)
    input_file = Path(sys.argv[1])
    if len(sys.argv) >= 3:
        output_file = Path(sys.argv[2])
    else:
        output_file = input_file.with_suffix('.docx')
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    parse_markdown_to_docx(content, output_file)
