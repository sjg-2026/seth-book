#!/usr/bin/env python3
"""
build.py — rebuild The Name Game editions from manuscript.md.

    python3 build.py --src DIR --out DIR [--pdf]

DIR holds manuscript.md plus the previous book.html and dashboard.html
(pulled from the private Artifact "[Artifact] Seth's Book Source").
Both HTML files are used as templates: their <head>, CSS, scripts and
interactive data are kept byte for byte; only the book text is regenerated.

Outputs in --out:
    book.html               print edition (text rebuilt from manuscript.md)
    dashboard.html          dashboard with DATA.chapters rebuilt
    The-Name-Game-Book.pdf  book.html printed with headless Chromium (--pdf)
    build-report.json       counts and hashes for the index page

No Dropbox paths. No network. Assertions fail loudly.
"""
import argparse, hashlib, html, json, re, subprocess, sys
from pathlib import Path

ORN = '<div class="orn">&#10022;&nbsp;&#10022;&nbsp;&#10022;</div>'
ORN_DASH = "<div class='orn'>&#10022;&nbsp;&#10022;&nbsp;&#10022;</div>"
ROMAN = {"I": "PART I", "II": "PART II", "III": "PART III"}


# ---------- inline markdown ----------
def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def slug(title: str) -> str:
    t = re.sub(r"[’']", "", title).lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t


# ---------- block parser ----------
def blocks(lines):
    """Yield (kind, payload) from manuscript lines inside one chapter."""
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if not ln.strip():
            i += 1; continue
        if ln.strip() == "---":
            yield ("orn", None); i += 1; continue
        if ln.startswith("## "):
            yield ("h3", ln[3:].strip()); i += 1; continue
        if ln.startswith("> "):
            buf = []
            while i < n and (lines[i].startswith(">") ):
                buf.append(lines[i][1:].lstrip(" ")); i += 1
            yield ("sidebar", buf); continue
        if ln.lstrip().startswith("|"):
            buf = []
            while i < n and lines[i].lstrip().startswith("|"):
                buf.append(lines[i].strip()); i += 1
            yield ("table", buf); continue
        if re.match(r"^(\d+\.|[-*])\s", ln):
            ordered = bool(re.match(r"^\d+\.", ln))
            buf = []
            while i < n and re.match(r"^(\d+\.|[-*])\s", lines[i]):
                buf.append(re.sub(r"^(\d+\.|[-*])\s+", "", lines[i])); i += 1
            yield ("list", (ordered, buf)); continue
        buf = []
        while i < n and lines[i].strip() and not re.match(r"^(## |> |\||---$|\d+\.\s|[-*]\s)", lines[i]):
            buf.append(lines[i].strip()); i += 1
        yield ("p", " ".join(buf))


def table_html(rows, cls):
    cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
    cells = [r for r in cells if not all(re.fullmatch(r":?-+:?", c) for c in r)]
    head, body = cells[0], cells[1:]
    out = [f"<table class='{cls}'><thead><tr>"] + [f"<th>{inline(c)}</th>" for c in head] + ["</tr></thead><tbody>"]
    for r in body:
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def render(lines, style):
    """style: 'book' or 'dash' — the two editions use slightly different class names."""
    out, first_p = [], True
    for kind, pl in blocks(lines):
        if kind == "p":
            cls = ' class="dropcap"' if (first_p and style == "book") else ""
            out.append(f"<p{cls}>{inline(pl)}</p>"); first_p = False
        elif kind == "h3":
            out.append(f'<h3 class="sub">{inline(pl)}</h3>' if style == "book" else f"<h4>{inline(pl)}</h4>")
        elif kind == "orn":
            out.append(ORN if style == "book" else ORN_DASH)
        elif kind == "sidebar":
            head = None; body = []
            for l in pl:
                if l.startswith("### "): head = l[4:].strip()
                elif l.strip(): body.append(l.strip())
            inner = "".join(f"<p>{inline(b)}</p>" for b in body)
            if style == "book":
                out.append(f'<aside class="sidebar"><p class="sb-head">{inline(head or "")}</p>{inner}</aside>')
            else:
                out.append(f"<aside class='sb'><p class='sbh'>{inline(head or '')}</p>{inner}</aside>")
        elif kind == "table":
            out.append(table_html(pl, "name-index" if style == "book" else "ix"))
        elif kind == "list":
            ordered, items = pl
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
    sep = "\n" if style == "book" else ""
    return sep.join(out)


# ---------- manuscript structure ----------
def parse(md: str):
    lines = md.split("\n")
    # skip front matter: everything before the first "# PART"
    start = next(i for i, l in enumerate(lines) if l.startswith("# PART"))
    lines = lines[start:]
    parts, cur_part, cur_ch = [], None, None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("# PART "):
            m = re.match(r"# PART (I{1,3}) — (.+)", ln)
            cur_part = {"no": ROMAN[m.group(1)], "title": m.group(2).strip(), "epigraph": None, "chapters": []}
            parts.append(cur_part); cur_ch = None; i += 1
            # optional italic epigraph right after the part line
            while i < len(lines) and not lines[i].strip(): i += 1
            if i < len(lines) and re.fullmatch(r"\*[^*].*\*", lines[i].strip()):
                cur_part["epigraph"] = lines[i].strip().strip("*"); i += 1
            continue
        if ln.startswith("# OUTRO"):
            cur_part = {"no": "OUTRO", "title": lines[i + 1][2:].strip(), "epigraph": None, "chapters": []}
            parts.append(cur_part)
            cur_ch = {"label": "Outro", "title": cur_part["title"], "lines": []}
            cur_part["chapters"].append(cur_ch); i += 2; continue
        if re.match(r"# Chapter \d+$", ln):
            cur_ch = {"label": ln[2:].strip(), "title": lines[i + 1][2:].strip(), "lines": []}
            cur_part["chapters"].append(cur_ch); i += 2; continue
        if cur_ch is None:
            assert ln.strip() in ("", "---"), f"text outside a chapter: {ln!r}"
        else:
            cur_ch["lines"].append(ln)
        i += 1
    return parts


# ---------- book.html ----------
def build_book(parts, template: str) -> str:
    head, rest = template.split('<main class="book">', 1)
    main_old, tail = rest.split("</main>", 1)
    # keep title page + copyright page from the template (static front matter)
    front_end = main_old.index('<section class="toc">')
    front = main_old[:front_end]
    n_ch = sum(len(p["chapters"]) for p in parts if p["no"] != "OUTRO")
    words = {1: "ONE", 2: "TWO", 3: "THREE", 4: "FOUR", 5: "FIVE", 6: "SIX", 7: "SEVEN", 8: "EIGHT", 9: "NINE", 10: "TEN",
             11: "ELEVEN", 12: "TWELVE", 13: "THIRTEEN", 14: "FOURTEEN", 15: "FIFTEEN", 16: "SIXTEEN", 17: "SEVENTEEN",
             18: "EIGHTEEN", 19: "NINETEEN", 20: "TWENTY", 21: "TWENTY-ONE", 22: "TWENTY-TWO"}
    front = re.sub(r"A FIELD GUIDE IN [A-Z-]+ CHAPTERS", f"A FIELD GUIDE IN {words[n_ch]} CHAPTERS", front)

    toc = ['<section class="toc"><h2 class="toc-head">Contents</h2>']
    body = []
    num = 0
    for p in parts:
        if p["no"] == "OUTRO":
            toc.append(f'<p class="toc-part">Outro — {inline(p["title"])}</p>')
        else:
            toc.append(f'<p class="toc-part">Part {p["no"].split()[1]} — {inline(p["title"].title())}</p>')
            epi = f'<p class="part-epigraph">{inline(p["epigraph"])}</p>' if p["epigraph"] else ""
            body.append(f'\n<section class="part-card">\n  <p class="part-no">{p["no"]}</p>\n  <div class="part-rule"></div>\n'
                        f'  <h2 class="part-title">{inline(p["title"])}</h2>\n  {epi}\n</section>\n')
        for ch in p["chapters"]:
            sid = slug(ch["title"])
            if ch["label"] == "Outro":
                tnum = "&nbsp;"
            else:
                num += 1; tnum = str(num)
            toc.append(f'<p class="toc-row"><a href="#{sid}"><span class="toc-num">{tnum}</span>'
                       f'<span class="toc-title">{inline(ch["title"])}</span><span class="toc-leader"></span></a></p>')
            body.append(f'\n<section class="chapter" id="{sid}">\n  <header class="chapter-head">\n'
                        f'    <p class="chapter-label">{ch["label"]}</p>\n'
                        f'    <h2 class="chapter-title">{inline(ch["title"])}</h2>\n'
                        f'    <div class="chapter-rule"><span></span><span class="diamond">&#9670;</span><span></span></div>\n'
                        f'  </header>\n{render(ch["lines"], "book")}\n</section>\n')
    toc.append("</section>")
    main_new = front + "".join(toc) + "".join(body)
    return head + '<main class="book">' + main_new + "</main>" + tail


# ---------- dashboard.html ----------
def build_dashboard(parts, template: str) -> str:
    key = "const DATA = "
    i = template.index(key) + len(key)
    obj, end = json.JSONDecoder().raw_decode(template[i:])
    chapters = []
    for p in parts:
        pname = "Outro" if p["no"] == "OUTRO" else f'{p["no"].replace("PART ", "PART ")}: {p["title"].title()}'
        for ch in p["chapters"]:
            chapters.append({"part": pname, "label": ch["label"], "title": ch["title"],
                             "html": render(ch["lines"], "dash")})
    obj["chapters"] = chapters
    new_json = json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")
    out = template[:i] + new_json + template[i + end:]
    n = len(chapters) - 1
    out = re.sub(r"All \d+ chapters and the outro", f"All {n} chapters and the outro", out)
    return out


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pdf", action="store_true")
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    md = (src / "manuscript.md").read_text(encoding="utf-8")
    parts = parse(md)
    n_ch = sum(len(p["chapters"]) for p in parts if p["no"] != "OUTRO")
    assert n_ch == 21, f"expected 21 chapters, parsed {n_ch}"
    assert parts[-1]["no"] == "OUTRO" and len(parts[-1]["chapters"]) == 1, "outro missing"

    book = build_book(parts, (src / "book.html").read_text(encoding="utf-8"))
    assert book.count('<section class="chapter"') == 22, "book: chapter sections"
    assert book.count("<aside") >= 5, "book: sidebars"
    assert "<main" in book and "</main>" in book and "tpick" in book, "book: template kept"
    (out / "book.html").write_text(book, encoding="utf-8")

    dash = build_dashboard(parts, (src / "dashboard.html").read_text(encoding="utf-8"))
    j = dash.index("const DATA = ") + len("const DATA = ")
    chk, _ = json.JSONDecoder().raw_decode(dash[j:])
    assert len(chk["chapters"]) == 22 and chk["chapters"][-1]["label"] == "Outro", "dashboard: chapters"
    assert all(k in chk for k in ("seths", "real", "index", "quiz")), "dashboard: other data kept"
    (out / "dashboard.html").write_text(dash, encoding="utf-8")

    (out / "manuscript.md").write_text(md, encoding="utf-8")

    report = {"chapters": n_ch, "words": len(re.findall(r"\S+", md)),
              "manuscript": {"bytes": len(md.encode()), "md5": md5(out / "manuscript.md")},
              "book.html": {"bytes": (out / "book.html").stat().st_size, "md5": md5(out / "book.html")},
              "dashboard.html": {"bytes": (out / "dashboard.html").stat().st_size, "md5": md5(out / "dashboard.html")}}

    if a.pdf:
        pdf = out / "The-Name-Game-Book.pdf"
        import os, shutil
        chrome = (os.environ.get("CHROME") or shutil.which("chromium") or shutil.which("chromium-browser")
                  or shutil.which("google-chrome") or next(iter(sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))), None))
        assert chrome, "no Chromium found; set CHROME=/path/to/chrome"
        cmd = [str(chrome), "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
               "--run-all-compositor-stages-before-draw", "--virtual-time-budget=10000",
               f"--print-to-pdf={pdf}", f"file://{(out / 'book.html').resolve()}"]
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        assert pdf.stat().st_size > 100_000, "pdf too small"
        report["pdf"] = {"bytes": pdf.stat().st_size, "md5": md5(pdf)}

    (out / "build-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
