#!/usr/bin/env python3
"""data/raw/{key}.xml（e-Gov 法令XML）を、ビューア用の JSON に変換して docs/data/ に出力する。

出力:
  docs/data/index.json            収録法令の一覧（グループ・略称・施行日・データ生成日時）
  docs/data/{key}/toc.json        目次（編章節款目 → 条）と、条→チャンクの対応、附則・別表の一覧
  docs/data/{key}/m{N}.json       本則の条文（チャンク分割）
  docs/data/{key}/s{N}.json       附則（チャンク分割）
  docs/data/{key}/x{N}.json       別表・様式など（チャンク分割）

  python scripts/build_laws.py [--only key ...]

標準ライブラリだけで動く。
"""
import argparse
import datetime
import json
import pathlib
import re
import shutil
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "docs" / "data"
CHUNK_BYTES = 280_000

LEVELS = ["Part", "Chapter", "Section", "Subsection", "Division"]
SUBITEMS = [f"Subitem{i}" for i in range(1, 11)]


# ── テキスト ───────────────────────────────────────────────

def text_of(el):
    """要素の文字列。ルビは本文だけ、上付き・下付きはそのまま連結する。"""
    if el is None:
        return ""
    out = []

    def walk(e):
        if e.tag == "Rt":
            return
        if e.text:
            out.append(e.text)
        for c in e:
            walk(c)
            if c.tail:
                out.append(c.tail)

    walk(el)
    return re.sub(r"\s*\n\s*", "", "".join(out)).strip()


def sentences(el):
    """ParagraphSentence / ItemSentence など。Column（列）は全角空白でつなぐ。"""
    if el is None:
        return ""
    cols = el.findall("Column")
    if cols:
        return "　".join(sentences(c) for c in cols)
    sents = el.findall("Sentence")
    if sents:
        return "".join(text_of(s) for s in sents)
    return text_of(el)


# ── 表・図 ─────────────────────────────────────────────────

def table(el):
    rows = []
    for tr in el.iter("TableRow"):
        row = []
        for td in tr.findall("TableColumn"):
            cell = {"t": cell_text(td)}
            for a, k in (("rowspan", "r"), ("colspan", "c")):
                v = td.get(a)
                if v and v != "1":
                    cell[k] = int(v)
            row.append(cell)
        rows.append(row)
    head = []
    for th in el.iter("TableHeaderRow"):
        head.append([{"t": cell_text(c)} for c in th.findall("TableHeaderColumn")])
    t = {"rows": rows}
    if head:
        t["head"] = head
    return t


def cell_text(td):
    parts = []
    for c in td:
        if c.tag == "Sentence":
            parts.append(text_of(c))
        elif c.tag in ("Part", "Chapter", "Section", "Subsection", "Division", "Article", "Paragraph", "Item"):
            parts.append(text_of(c))
        elif c.tag == "Column":
            parts.append(sentences(c))
        else:
            parts.append(text_of(c))
    s = "\n".join(p for p in parts if p)
    return s or text_of(td)


def extras(el):
    """項・号に付く表・図・リスト・様式。"""
    out = []
    for c in el:
        if c.tag == "TableStruct":
            t = table(c.find("Table") if c.find("Table") is not None else c)
            title = c.find("TableStructTitle")
            if title is not None:
                t["title"] = text_of(title)
            rem = [text_of(r) for r in c.findall("Remarks")]
            if rem:
                t["remarks"] = rem
            out.append({"table": t})
        elif c.tag in ("FigStruct", "StyleStruct", "FormatStruct", "NoteStruct"):
            label = text_of(c.find(c.tag.replace("Struct", "StructTitle"))) or {
                "FigStruct": "（図）", "StyleStruct": "（様式）", "FormatStruct": "（書式）", "NoteStruct": "（注記）"}[c.tag]
            body = text_of(c)
            out.append({"note": label if body == label else body or label})
        elif c.tag == "List":
            out.append({"list": [sentences(li.find("ListSentence")) for li in c.findall("ListSentence/..")] or
                        [text_of(x) for x in c]})
        elif c.tag == "AmendProvision":
            out.append({"note": text_of(c)})
    return out


# ── 号・項・条 ──────────────────────────────────────────────

def item(el, depth=0):
    tag_title = "ItemTitle" if depth == 0 else f"Subitem{depth}Title"
    tag_sent = "ItemSentence" if depth == 0 else f"Subitem{depth}Sentence"
    d = {"t": text_of(el.find(tag_title)), "s": sentences(el.find(tag_sent))}
    sub_tag = SUBITEMS[depth] if depth < len(SUBITEMS) else None
    subs = [item(c, depth + 1) for c in el.findall(sub_tag)] if sub_tag else []
    if subs:
        d["i"] = subs
    ex = extras(el)
    if ex:
        d["x"] = ex
    return d


def paragraph(el):
    d = {"n": el.get("Num", ""), "t": text_of(el.find("ParagraphNum")), "s": sentences(el.find("ParagraphSentence"))}
    cap = el.find("ParagraphCaption")
    if cap is not None:
        d["c"] = text_of(cap)
    items = [item(c) for c in el.findall("Item")]
    if items:
        d["i"] = items
    ex = extras(el)
    if ex:
        d["x"] = ex
    return d


def article(el, prefix=""):
    num = el.get("Num", "")
    d = {
        "id": prefix + num,
        "t": text_of(el.find("ArticleTitle")),
        "p": [paragraph(p) for p in el.findall("Paragraph")],
    }
    cap = el.find("ArticleCaption")
    if cap is not None:
        d["c"] = text_of(cap)
    if el.get("Delete") == "true" or (len(d["p"]) == 1 and re.fullmatch(r"削除", d["p"][0]["s"] or "")):
        d["del"] = 1
    return d


# ── 目次 ───────────────────────────────────────────────────

def walk_structure(el, arts, path):
    """本則を辿る。目次ノードを返し、条を arts に集める。"""
    nodes = []
    for c in el:
        if c.tag in LEVELS:
            title = text_of(c.find(c.tag + "Title"))
            node = {"k": c.tag, "t": title, "c": walk_structure(c, arts, path + [title])}
            if node["c"]:
                node["from"] = first_leaf(node)
            nodes.append(node)
        elif c.tag == "Article":
            a = article(c)
            a["path"] = path
            arts.append(a)
            leaf = {"k": "A", "id": a["id"], "t": a["t"]}
            if a.get("c"):
                leaf["c"] = a["c"]
            if a.get("del"):
                leaf["del"] = 1
            nodes.append(leaf)
        elif c.tag == "Paragraph":
            # 条の無い法令（項だけで構成される本則）
            pid = f"p{c.get('Num', len(arts) + 1)}"
            a = {"id": pid, "t": text_of(c.find("ParagraphNum")) or "", "p": [paragraph(c)], "path": path}
            arts.append(a)
            nodes.append({"k": "A", "id": pid, "t": a["t"] or "（本文）"})
    return nodes


def first_leaf(node):
    for c in node.get("c", []):
        if c["k"] == "A":
            return c["id"]
        f = first_leaf(c)
        if f:
            return f
    return None


# ── 附則・別表 ─────────────────────────────────────────────

def suppl(el, idx):
    label = text_of(el.find("SupplProvisionLabel")) or "附則"
    amend = el.get("AmendLawNum")
    sid = f"s{idx}"
    d = {"id": sid, "t": label, "body": []}
    if amend:
        d["law"] = amend
    if el.get("Extract") == "true":
        d["extract"] = 1
    for c in el:
        if c.tag == "Article":
            d["body"].append(article(c, prefix=""))
        elif c.tag == "Paragraph":
            d["body"].append({"id": "p" + c.get("Num", ""), "t": "", "p": [paragraph(c)]})
        elif c.tag in LEVELS:
            for a in c.iter("Article"):
                d["body"].append(article(a))
        elif c.tag in ("SupplProvisionAppdxTable", "SupplProvisionAppdxStyle", "SupplProvisionAppdx"):
            d["body"].append(appendix(c, f"{sid}x{len(d['body'])}"))
    return d


def appendix(el, aid):
    title_el = next((c for c in el if c.tag.endswith("Title")), None)
    d = {"id": aid, "kind": el.tag, "t": text_of(title_el) or el.tag}
    rel = el.find("RelatedArticleNum")
    if rel is not None:
        d["rel"] = text_of(rel)
    blocks = []
    for c in el:
        if c is title_el or c.tag == "RelatedArticleNum":
            continue
        if c.tag == "TableStruct":
            t = table(c.find("Table") if c.find("Table") is not None else c)
            blocks.append({"table": t})
        elif c.tag == "Remarks":
            blocks.append({"note": text_of(c)})
        elif c.tag in ("Item", "Paragraph"):
            blocks.append({"text": text_of(c)})
        else:
            t = text_of(c)
            if t:
                blocks.append({"text": t})
    d["x"] = blocks
    return d


# ── 出力 ───────────────────────────────────────────────────

def chunked(objs, prefix):
    """オブジェクトを順にチャンクへ詰める。[(file, [objs])] と {id: chunk番号}。"""
    chunks, cur, size = [], [], 0
    where = {}
    for o in objs:
        b = len(json.dumps(o, ensure_ascii=False).encode("utf-8"))
        if cur and size + b > CHUNK_BYTES:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(o)
        size += b
        where[o["id"]] = len(chunks)
    if cur:
        chunks.append(cur)
    return [(f"{prefix}{i}.json", c) for i, c in enumerate(chunks)], where


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def build_one(law, group):
    key = law["key"]
    xml_path = RAW / f"{key}.xml"
    meta = json.loads((RAW / f"{key}.meta.json").read_text(encoding="utf-8"))
    root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
    body = root.find("LawBody")
    main = body.find("MainProvision")

    arts = []
    toc = walk_structure(main, arts, [])
    supps = [suppl(s, i) for i, s in enumerate(body.findall("SupplProvision"))]
    appdx_tags = ("AppdxTable", "AppdxStyle", "AppdxFormat", "AppdxFig", "AppdxNote", "Appdx")
    appdx = [appendix(c, f"x{i}") for i, c in enumerate(e for e in body if e.tag in appdx_tags)]

    out = OUT / key
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    m_files, m_where = chunked(arts, "m")
    s_files, s_where = chunked(supps, "s")
    x_files, x_where = chunked(appdx, "x")
    for name, objs in m_files + s_files + x_files:
        dump(out / name, objs)

    tocj = {
        "key": key, "abbr": law["abbr"], "title": law["title"], "kind": law["kind"],
        "law_num": meta.get("law_num"), "law_id": meta.get("law_id"),
        "enforced": meta.get("enforced"), "revision_id": meta.get("revision_id"),
        "toc": toc,
        "where": m_where,
        "suppl": [{"id": s["id"], "t": s["t"], "law": s.get("law"), "f": s_where[s["id"]],
                   "n": len(s["body"])} for s in supps],
        "appdx": [{"id": a["id"], "t": a["t"], "f": x_where[a["id"]]} for a in appdx],
    }
    dump(out / "toc.json", tocj)
    stats = {"articles": sum(1 for a in arts if not a.get("del")), "articles_all": len(arts),
             "suppl": len(supps), "appdx": len(appdx), "files": len(m_files) + len(s_files) + len(x_files) + 1}
    return {
        "key": key, "abbr": law["abbr"], "title": law["title"], "kind": law["kind"], "group": group["id"],
        "law_num": meta.get("law_num"), "enforced": meta.get("enforced"),
        "revision_id": meta.get("revision_id"), "amendment": meta.get("amendment_law_title"), **stats,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "laws.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)

    old = {}
    idx_path = OUT / "index.json"
    if idx_path.exists():
        old = {x["key"]: x for x in json.loads(idx_path.read_text(encoding="utf-8")).get("laws", [])}

    laws, failed = [], []
    for g in cfg["groups"]:
        for law in g["laws"]:
            if args.only and law["key"] not in args.only:
                if law["key"] in old:
                    laws.append(old[law["key"]])
                continue
            try:
                r = build_one(law, g)
                laws.append(r)
                print(f"[変換] {r['key']:18s} 条 {r['articles']:>5,d}（削除含む {r['articles_all']:,d}） 附則 {r['suppl']:>4d} 別表等 {r['appdx']:>3d}  {r['files']} ファイル")
            except Exception as e:  # noqa: BLE001
                print(f"[失敗] {law['key']}: {e}", file=sys.stderr)
                failed.append(law["key"])

    index = {
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": "e-Gov法令API（デジタル庁）",
        "groups": [{"id": g["id"], "name": g["name"]} for g in cfg["groups"]],
        "laws": laws,
    }
    dump(idx_path, index)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
