"""Render one docpack Markdown file to print-ready HTML (make_pdfs.sh prints it to PDF).

usage: uv run --no-project --with markdown --with pyyaml python build_html.py <src.md> <out.html> <font_dir> [--internal]

Generalised from the EV3 and TMA doc-pack builders. Branding comes from the
pack's docpack.yaml (brand, subtitle, footer, color, audience); optional
`scrub_patterns` (regexes) remove pack-specific internal references from the
client edition. Client-edition
output drops `แหล่งข้อมูล`/Source sections and source columns and strips
repository paths; `--internal` or `audience: internal` keeps them.
"""
import html
import pathlib
import re
import sys

import markdown
import yaml

src, out, fontdir = map(pathlib.Path, sys.argv[1:4])
cfg_path = src.parent / "docpack.yaml"
cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
brand = str(cfg.get("brand", "SOLO EMPIRE"))
subtitle = str(cfg.get("subtitle", ""))
footer = str(cfg.get("footer", f"{brand} — ฉบับร่างเพื่อพิจารณา"))
color = str(cfg.get("color", "#1f4e79"))
if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
    color = "#1f4e79"
internal = "--internal" in sys.argv or cfg.get("audience") == "internal"

text = src.read_text(encoding="utf-8")
meta = {}
m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
if m:
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    text = text[m.end():]

# ---------- client-edition sanitising (markdown keeps internal refs; client PDF does not) ----------
DROP_HDR = re.compile(r"^(แหล่งอ้างอิง|แหล่ง|แหล่งข้อมูล|แหล่งที่มา|ที่มา|Source|Sources|อ้างอิงโค้ด|หลักฐานในโค้ด|หลักฐาน \(path\)|แหล่ง \(file:line\))$", re.I)
SOURCE_SECTION = re.compile(r"แหล่งข้อมูล|Source list|^[\d.]*\s*Sources?\b|แหล่งที่มาของเอกสาร|แหล่งอ้างอิง")
PATHLIKE = re.compile(r"`[^`\n]*(?:/|\.(?:ts|tsx|js|mjs|sql|md|py|sh|json|yaml|yml|toml)\b)[^`\n]*`")


def split_row(line):
    return re.split(r"(?<!\\)\|", line.strip().strip("|"))


def drop_sections(t):
    res, skip = [], None
    for line in t.split("\n"):
        h = re.match(r"^(#{2,4})\s+(.*)", line)
        if h:
            lvl = len(h.group(1))
            if skip and lvl <= skip:
                skip = None
            if not skip and SOURCE_SECTION.search(h.group(2)):
                skip = lvl
                continue
        if not skip:
            res.append(line)
    return "\n".join(res)


def drop_cols(t):
    lines, i, res = t.split("\n"), 0, []
    while i < len(lines):
        if lines[i].startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|\s*$", lines[i + 1]):
            hdr = [c.strip().replace("*", "") for c in split_row(lines[i])]
            drop = {j for j, c in enumerate(hdr) if DROP_HDR.search(c)}
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                cells = split_row(lines[j])
                if drop and len(cells) == len(hdr):
                    cells = [c for k, c in enumerate(cells) if k not in drop]
                res.append("|" + "|".join(cells) + "|")
                j += 1
            i = j
        else:
            res.append(lines[i])
            i += 1
    return "\n".join(res)


EXTRA_SCRUB = [re.compile(p) for p in cfg.get("scrub_patterns") or []]


def scrub(t):
    for pattern in EXTRA_SCRUB:
        t = pattern.sub("", t)
    t = drop_cols(drop_sections(t))
    t = PATHLIKE.sub("", t)
    t = re.sub(r"\(\s*[,;·/]*\s*\)", "", t)
    t = re.sub(r"(?<=\|)\s*[,;·/]\s*", " ", t)
    t = re.sub(r"\s*[,;·/]\s*(?=\|)", " ", t)
    t = re.sub(r"(?<=\S)[ \t]{2,}", " ", t)
    return t


if not internal:
    text = scrub(text)

# blank line before lists that directly follow a paragraph (python-markdown needs it)
lines, fixed = text.split("\n"), []
for i, x in enumerate(lines):
    prev = lines[i - 1] if i else ""
    if i and re.match(r"^\s*(\d+\.|[-*])\s", x) and prev.strip() and not re.match(r"^\s*(\d+\.|[-*])\s|^\s{2,}\S", prev) and not prev.startswith("|"):
        fixed.append("")
    fixed.append(x)
text = "\n".join(fixed)
title = meta.get("title", src.stem)

blocks = []


def keep(mm):
    blocks.append(mm.group(1))
    return f"\n\nMERMAIDBLOCK{len(blocks) - 1}\n\n"


text = re.sub(r"```mermaid\n(.*?)```", keep, text, flags=re.S)
md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "toc"], extension_configs={"toc": {"toc_depth": "2-2"}})
body, toc = md.convert(text), md.toc
for i, b in enumerate(blocks):
    body = body.replace(f"<p>MERMAIDBLOCK{i}</p>", f'<figure class="diagram"><pre class="mermaid">{html.escape(b)}</pre></figure>')


def svgfile(mm):
    fp = src.parent / mm.group(1)
    try:
        sv = re.sub(r"^<\?xml[^>]*>\s*", "", fp.read_text(encoding="utf-8"))
    except OSError:
        return ""
    return f'<figure class="swim">{sv}<figcaption>{html.escape(fp.stem)}</figcaption></figure>'


body = re.sub(r"<!-- svgfile:([\w./-]+) -->", svgfile, body)
body = body.replace("<h1", '<h1 class="doc-h1"', 1)


def tcls(mm):
    t = mm.group(0)
    n = len(re.findall(r"<th", t.split("</tr>")[0]))
    cls = "t-wide" if n >= 8 else ("t-mid" if n >= 6 else "")
    return f'<div class="tbl {cls}">{t}</div>'


body = re.sub(r"<table>.*?</table>", tcls, body, flags=re.S)
landscape = len(re.findall(r'class="tbl t-wide"', body)) >= 3
short = re.sub(r"^.{1,60}?\s+[—-]\s+", "", title, count=1)
rows = "".join(f"<tr><th>{lab}</th><td>{html.escape(meta[k])}</td></tr>" for k, lab in
               [("doc_id", "รหัสเอกสาร"), ("version", "ฉบับ"), ("date", "วันที่"), ("status", "สถานะ")] if k in meta)
sub = f'<div class="sub">{html.escape(subtitle)}</div>' if subtitle else ""
note = ("เอกสารภายในฉบับร่าง — ใช้ประกอบการพัฒนาและดูแลระบบ" if internal else
        "เอกสารฉบับร่างเพื่อพิจารณา — เนื้อหาไม่ถือเป็นข้อตกลงด้านขอบเขต ราคา หรือการตรวจรับ จนกว่าจะลงนามรับรอง")
cover = f'''<section class="cover"><div class="band"></div><div class="brand">{html.escape(brand)}</div>{sub}
<h1 class="ct">{html.escape(short)}</h1><table class="meta">{rows}</table>
<p class="note">{note}</p></section>'''
tocsec = f'<section class="toc"><h2 class="toch">สารบัญ</h2>{toc}</section>' if toc.count("<li") > 2 else ""
mer = ('<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script><script>'
       'mermaid.initialize({startOnLoad:true,theme:"base",fontFamily:"Sarabun",securityLevel:"loose",'
       'themeVariables:{primaryColor:"#eef2f7",primaryBorderColor:"' + color + '",lineColor:"#52514e",fontSize:"17px"},'
       'flowchart:{useMaxWidth:true,nodeSpacing:28,rankSpacing:28,padding:8,wrappingWidth:240},'
       'gantt:{barHeight:22,fontSize:13,sectionFontSize:13,leftPadding:200,useWidth:1000},'
       'state:{useMaxWidth:true}});</script>') if blocks else ""
size = "A4 landscape" if landscape else "A4"
docid = meta.get("doc_id", "")
ver = meta.get("version", "").split()[0].lstrip("v") if meta.get("version") else ""


def css_str(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


css = f"""
@font-face{{font-family:Sarabun;src:url('file://{fontdir}/Sarabun-Regular.ttf');font-weight:400}}
@font-face{{font-family:Sarabun;src:url('file://{fontdir}/Sarabun-Bold.ttf');font-weight:700}}
@page{{size:{size};margin:18mm 16mm 18mm;
 @top-left{{content:'{css_str(short)}';font-family:Sarabun;font-size:8pt;color:#8a8984}}
 @top-right{{content:'{css_str(docid)}  ·  v{css_str(ver)}';font-family:Sarabun;font-size:8pt;color:#8a8984}}
 @bottom-right{{content:'หน้า ' counter(page) ' / ' counter(pages);font-family:Sarabun;font-size:8pt;color:#8a8984}}
 @bottom-left{{content:'{css_str(footer)}';font-family:Sarabun;font-size:8pt;color:#8a8984}}}}
@page:first{{@top-left{{content:none}}@top-right{{content:none}}@bottom-left{{content:none}}@bottom-right{{content:none}}}}
:root{{--ink:#1f1f1d;--muted:#5d5c58;--brand:{color};--brand-soft:color-mix(in srgb,{color} 10%,white);--line:#d9dce2}}
body{{font-family:Sarabun,sans-serif;font-size:11pt;line-height:1.65;color:var(--ink);background:#fff;margin:0}}
p,li{{orphans:3;widows:3}}
h1.doc-h1{{font-size:20pt;color:var(--brand);margin:0 0 10px;border-bottom:3px solid var(--brand);padding-bottom:6px}}
h2{{font-size:15.5pt;color:var(--brand);margin:28px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--line);break-after:avoid}}
h3{{font-size:12.5pt;margin:20px 0 6px;break-after:avoid}}
h4{{font-size:11.5pt;color:var(--muted);margin:16px 0 4px;break-after:avoid}}
.tbl{{margin:8px 0 14px}}
table{{border-collapse:collapse;width:100%;font-size:9.4pt;line-height:1.45}}
.t-mid table{{font-size:8.8pt}} .t-wide table{{font-size:8.2pt}}
thead{{display:table-header-group}} tr{{break-inside:avoid}}
th{{background:var(--brand);color:#fff;font-weight:700;text-align:left;padding:5px 6px;border:1px solid var(--brand)}}
td{{padding:4px 6px;border:1px solid var(--line);vertical-align:top;overflow-wrap:break-word}}
tbody tr:nth-child(even) td{{background:#f6f8fa}}
code{{font-family:'DejaVu Sans Mono',monospace;font-size:0.82em;background:#eef1f5;padding:0 3px;border-radius:3px;overflow-wrap:anywhere}}
pre{{background:#f4f5f7;padding:10px;font-size:8.5pt;white-space:pre-wrap;border-radius:6px}}
blockquote{{margin:12px 0;padding:8px 14px;background:var(--brand-soft);border-left:4px solid var(--brand);border-radius:0 6px 6px 0}}
blockquote p{{margin:4px 0}}
@page land{{size:A4 landscape;margin:10mm 10mm 12mm}}
figure.swim{{page:land;break-before:page;break-after:page;margin:0;padding:0}} figure.swim svg{{width:100%;height:auto;max-height:178mm}}
figure{{margin:16px 0;break-inside:avoid}}
figcaption{{font-size:9pt;color:var(--muted);margin-top:6px;text-align:center}}
pre.mermaid{{background:none;text-align:center;padding:0}} pre.mermaid svg{{max-width:100%!important;max-height:225mm!important;width:auto!important;height:auto}}
.cover{{break-after:page;padding-top:55mm;position:relative}}
.band{{position:absolute;top:-18mm;left:-16mm;right:-16mm;height:16mm;background:var(--brand)}}
.brand{{color:var(--brand);font-weight:700;letter-spacing:3px;font-size:11pt}}
.sub{{color:var(--muted);font-size:10pt;margin-top:2px}}
.ct{{font-size:26pt;line-height:1.3;color:var(--ink);margin:14px 0 26px;border:none}}
.meta{{width:auto;min-width:300px;font-size:10.5pt}} .meta th{{width:120px;background:var(--brand-soft);color:var(--ink);border-color:var(--line)}}
.note{{margin-top:40px;color:var(--muted);font-size:9.5pt;max-width:520px}}
.toc{{break-after:page}} .toch{{border:none}} .toc ul{{list-style:none;padding-left:0}} .toc li{{padding:5px 0;border-bottom:1px dotted var(--line)}} .toc a{{color:var(--ink);text-decoration:none}}
a{{color:var(--brand)}}
"""
out.write_text(f'<!doctype html><html lang="th"><head><meta charset="utf-8"><title>{html.escape(short)}</title><style>{css}</style>{mer}</head><body>{cover}{tocsec}{body}</body></html>', encoding="utf-8")
print(src.name, "landscape" if landscape else "portrait", "internal" if internal else "client")
