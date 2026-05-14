#!/usr/bin/env python3
"""
follow-builders digest 专用 md → html 渲染器。

只支持本 skill digest 实际用到的 markdown 子集，避免依赖任何 PyPI 包。

支持的语法：
- # H1（取出作为 <title>，从正文剔除）
- ## H2 / ### H3（H3 支持 `Role · Name` 自动拆 .role span）
- 段落（空行分段）
- > blockquote（连续 `> ` 行合并成一个 blockquote）
- 行首 🔗/🎬 + URL（包含 <url> 或裸 URL）→ <p class="link-line">
- **bold** → <strong>
- "..." 英文引语段中保留为普通文本（前后段会用 <span class="quote-en"> 包，但 LLM 已经在 md 里用普通引号即可）
- [text](url) → <a>
- <url> 自动链接 → <a>
- ---  → <hr>
- 最后一段含 `Generated through` 的 footer 自动套 <footer>

用法：
  python3 md_to_html.py <input.md> <template.html> > output.html
  python3 md_to_html.py <input.md> <template.html> <output.html>
"""

import sys
import re
import html
from pathlib import Path
from datetime import datetime


LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
AUTOLINK_RE = re.compile(r'<(https?://[^>\s]+)>')
BOLD_RE = re.compile(r'\*\*([^*]+)\*\*')


def inline(text: str) -> str:
    """处理内联：占位符模式避免 html.escape 破坏链接。
    顺序：先抽出 autolink / md-link / bold → 用占位符代替 → escape → 还原。"""
    placeholders = {}
    counter = [0]

    def stash(html_snippet: str) -> str:
        key = f'\x00P{counter[0]}\x00'
        placeholders[key] = html_snippet
        counter[0] += 1
        return key

    def sub_autolink(m):
        url = m.group(1)
        return stash(f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{html.escape(url, quote=False)}</a>')

    def sub_mdlink(m):
        label, url = m.group(1), m.group(2)
        return stash(f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{html.escape(label, quote=False)}</a>')

    def sub_bold(m):
        return stash(f'<strong>{html.escape(m.group(1), quote=False)}</strong>')

    text = AUTOLINK_RE.sub(sub_autolink, text)
    text = LINK_RE.sub(sub_mdlink, text)
    text = BOLD_RE.sub(sub_bold, text)
    text = html.escape(text, quote=False)
    for key, snippet in placeholders.items():
        text = text.replace(key, snippet)
    return text


def render_h3(content: str) -> str:
    """H3 支持 'Role · Name' 自动拆分为 <h3>Role <span class="role">· Name</span></h3>。"""
    if ' · ' in content:
        role, _, name = content.partition(' · ')
        return f'<h3>{inline(role)} <span class="role">· {inline(name)}</span></h3>'
    return f'<h3>{inline(content)}</h3>'


def render_link_line(line: str) -> str:
    """🔗 <url> 或 🎬 <url> 单行 → link-line 段。"""
    return f'<p class="link-line">{inline(line)}</p>'


def render_paragraph(lines: list) -> str:
    """普通段落，多行 join 用空格（中文）。"""
    text = ' '.join(l.strip() for l in lines).strip()
    if not text:
        return ''
    # 整段以 🔗/🎬 开头视为 link-line
    if text.startswith(('🔗 ', '🎬 ')):
        return render_link_line(text)
    return f'<p>{inline(text)}</p>'


def render_blockquote(lines: list) -> str:
    """连续 '> ' 行合成一个 blockquote。"""
    inner_lines = []
    cur = []
    for l in lines:
        stripped = l[2:] if l.startswith('> ') else l[1:].lstrip() if l.startswith('>') else l
        if not stripped.strip():
            if cur:
                inner_lines.append(' '.join(cur))
                cur = []
        else:
            cur.append(stripped.strip())
    if cur:
        inner_lines.append(' '.join(cur))
    inner_html = '\n'.join(f'<p>{inline(p)}</p>' for p in inner_lines)
    return f'<blockquote>\n{inner_html}\n</blockquote>'


def md_to_body(md_text: str) -> tuple[str, str]:
    """返回 (title, body_html)。"""
    lines = md_text.splitlines()
    title = ''
    out = []
    i = 0
    in_footer = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # H1 — 后面立即追加 meta 行
        if stripped.startswith('# ') and not title:
            title = stripped[2:].strip()
            out.append(f'<h1>{inline(title)}</h1>')
            meta = f'由 Follow Builders skill 整理 · 跟踪一线 AI builder 的真实声音'
            out.append(f'<div class="meta">{html.escape(meta, quote=False)}</div>')
            i += 1
            continue

        # H2
        if stripped.startswith('## '):
            out.append(f'<h2>{inline(stripped[3:].strip())}</h2>')
            i += 1
            continue

        # H3
        if stripped.startswith('### '):
            out.append(render_h3(stripped[4:].strip()))
            i += 1
            continue

        # HR → footer 开始
        if stripped == '---':
            out.append('<hr>')
            in_footer = True
            i += 1
            continue

        # blockquote
        if stripped.startswith('>'):
            block = []
            while i < len(lines) and (lines[i].strip().startswith('>') or lines[i].strip() == ''):
                if lines[i].strip().startswith('>'):
                    block.append(lines[i].strip())
                    i += 1
                else:
                    # 空行——往后再看一行是否仍是 quote
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith('>'):
                        block.append('')
                        i += 1
                    else:
                        break
            out.append(render_blockquote(block))
            continue

        # 空行 → 跳过
        if not stripped:
            i += 1
            continue

        # 普通段落：连续非空非特殊行
        para = []
        while i < len(lines):
            l = lines[i]
            s = l.strip()
            if not s:
                break
            if s.startswith(('# ', '## ', '### ', '>', '---')):
                break
            para.append(l)
            i += 1

        rendered = render_paragraph(para)
        if in_footer and 'Generated through' in ' '.join(para):
            out.append(f'<footer>{rendered[3:-4]}</footer>')  # strip <p>...</p>
        else:
            out.append(rendered)

    return title, '\n'.join(b for b in out if b)


def main():
    if len(sys.argv) < 3:
        print('Usage: md_to_html.py <input.md> <template.html> [output.html]', file=sys.stderr)
        sys.exit(1)

    md_path = Path(sys.argv[1])
    tpl_path = Path(sys.argv[2])
    out_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    md_text = md_path.read_text(encoding='utf-8')
    tpl = tpl_path.read_text(encoding='utf-8')

    title, body = md_to_body(md_text)

    rendered = (
        tpl.replace('{{TITLE}}', html.escape(title or 'AI Digest', quote=True))
           .replace('{{BODY}}', body)
    )

    if out_path:
        out_path.write_text(rendered, encoding='utf-8')
        print(f'wrote {out_path}', file=sys.stderr)
    else:
        sys.stdout.write(rendered)


if __name__ == '__main__':
    main()
