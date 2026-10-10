"""Brief files to plain text. Markdown, email, memo, CSV and HTML tables become lines. Never raises."""
import re
from html.parser import HTMLParser


def _cells(line):
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [c.strip() for c in text.split("|")]


def _is_sep(line):
    return bool(re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", line))


def _is_row(line):
    return line.strip().startswith("|") and line.count("|") >= 2


def _render_table(rows):
    """One line per data row: Header: cell | Header: cell."""
    parsed = [_cells(r) for r in rows if not _is_sep(r)]
    if not parsed:
        return []
    width = max(len(r) for r in parsed)
    header, data = parsed[0], parsed[1:]
    if not data:
        data, header = parsed, [f"col{i + 1}" for i in range(width)]
    if len(header) < width:
        header = header + [f"col{i + 1}" for i in range(len(header), width)]
    lines = []
    for row in data:
        pairs = []
        for i in range(width):
            cell = row[i] if i < len(row) else ""
            name = header[i] or f"col{i + 1}"
            pairs.append(f"{name}: {cell}".strip())
        lines.append(" | ".join(pairs))
    return lines


def flatten_markdown_tables(text):
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        if _is_row(lines[i]):
            block = []
            while i < len(lines) and (_is_row(lines[i]) or _is_sep(lines[i])):
                block.append(lines[i])
                i += 1
            try:
                out.extend(_render_table(block))
            except Exception:
                out.extend(block)
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._row = None
        self._cell = None
        self._rows = []
        self._capture = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br":
            self._capture.append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._flush_table()
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4"):
            self._capture.append("\n")

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif self._row is None:
            self._capture.append(data)

    def _flush_table(self):
        if not self._rows:
            return
        header, data = self._rows[0], self._rows[1:]
        if not data:
            data, header = self._rows, [f"col{i + 1}" for i in range(len(self._rows[0]))]
        for row in data:
            pairs = []
            for i, name in enumerate(header):
                cell = row[i] if i < len(row) else ""
                pairs.append(f"{name}: {cell}".strip())
            self._capture.append("\n" + " | ".join(pairs))
        self._capture.append("\n")
        self._rows = []

    def text(self):
        self._flush_table()
        return "".join(self.parts + self._capture)


def flatten_html(text):
    if "<table" not in text.lower() and "<tr" not in text.lower():
        return text
    parser = _HTML()
    try:
        parser.feed(text)
        parser.close()
        return parser.text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", text)


def _csv_line(line):
    if line.startswith("|") or len(line) > 240 or line.count(",") < 1:
        return None
    if re.search(r"[.!?]\s*$", line) and line.count(" ") > 8:
        return None
    parts = [p.strip() for p in line.split(",")]
    if any(not p or len(p) > 80 for p in parts):
        return None
    return parts


def flatten_csv_blocks(text):
    """A fenced csv block, or a tight comma block, becomes one line per row."""
    text = re.sub(r"```csv\s*\n(.*?)```", lambda m: _csv_block(m.group(1)), text, flags=re.S | re.I)
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        parsed = _csv_line(lines[i])
        nxt = _csv_line(lines[i + 1]) if i + 1 < len(lines) else None
        if parsed and nxt and len(parsed) == len(nxt) and len(parsed) >= 2:
            block = []
            width = len(parsed)
            while i < len(lines):
                row = _csv_line(lines[i])
                if not row or len(row) != width:
                    break
                block.append(row)
                i += 1
            if len(block) >= 2:
                header, data = block[0], block[1:]
                for row in data:
                    out.append(" | ".join(f"{header[j]}: {row[j]}" for j in range(width)))
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _csv_block(body):
    rows = [[c.strip() for c in line.split(",")] for line in body.splitlines() if line.strip()]
    if len(rows) < 2:
        return body
    header, data = rows[0], rows[1:]
    return "\n".join(" | ".join(f"{header[j]}: {row[j] if j < len(row) else ''}" for j in range(len(header))) for row in data)


def to_plain(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u00a0", " ")
    try:
        text = flatten_html(text)
    except Exception:
        pass
    try:
        text = flatten_markdown_tables(text)
    except Exception:
        pass
    try:
        text = flatten_csv_blocks(text)
    except Exception:
        pass
    return text


def read_files(paths):
    """Document order. Each file is marked so a later file can override an earlier one."""
    chunks = []
    for i, path in enumerate(paths, 1):
        raw = path.read_text(encoding="utf-8", errors="replace")
        chunks.append(f"===== DOCUMENT {i}: {path.name} =====\n" + to_plain(raw))
    return "\n\n".join(chunks)
