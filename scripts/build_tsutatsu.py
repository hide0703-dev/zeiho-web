#!/usr/bin/env python3
"""data/raw_tsutatsu/{key}/（国税庁の通達ページ）を、ビューア用の JSON に変換して docs/data/{key}/ に出力する。

国税庁の1ページ（章・節・款など）を表示の単位とし、本文を「見出し・段落（字下げ段数つき）・表・箇条」の
並びに変換する。各項目の番号（法基通 9-2-9、評基通 213-2、徴基通 第47条関係1 → 47-1 など）を索引にする。

変換と同時に、各ページについて「原文の本文の文字」と「変換後の文字」を空白を除いて照合し、
1ページでも食い違えば終了コード1で止まる。

  python scripts/build_tsutatsu.py [--only key ...]

出力:
  docs/data/{key}/toc.json   目次（グループ見出しとページ）、項目番号 → ページ の索引、各ページの原文URL
  docs/data/{key}/t{N}.json  ページN の本文ブロック
  docs/data/index.json の "tsutatsu" に一覧を追記
"""
import argparse
import datetime
import hashlib
import html.parser
import json
import pathlib
import re
import shutil
import sys
import unicodedata
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw_tsutatsu"
OUT = ROOT / "docs" / "data"

# 項目番号の付け方
#   hier    : 9-2-9 のように「-」で区切る番号だけを項目とする（法基通・消基通・相基通・措通など）
#   article : 「第47条関係」の下の 1, 2 … を 47-1, 47-2 とする（徴基通・通基通）
#   plain   : 1, 13, 213-2 のような番号を項目とする（評基通）
#   jo      : 「第2条」や 2 を項目とする（印基通）
NUMBERING = {
    "choshu_kt": "article", "tsusoku_kt": "article",
    "hyoka_kt": "plain", "inshi_kt": "jo",
}

VOID = {"br", "img", "hr", "meta", "link", "input", "col", "wbr", "area", "base", "source"}
SKIP = {"script", "style", "noscript"}


# ── 小さな DOM ─────────────────────────────────────────────

class Node:
    __slots__ = ("tag", "attrs", "kids", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.kids, self.parent = tag, dict(attrs or {}), [], parent


class DOM(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.kids.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.kids.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.kids.append(data)


def parse(html_text):
    d = DOM()
    d.feed(html_text)
    d.close()
    return d.root


def find(node, pred):
    if isinstance(node, str):
        return None
    if pred(node):
        return node
    for k in node.kids:
        r = find(k, pred)
        if r is not None:
            return r
    return None


WS = re.compile(r"[ \t\r\n\f\v]+")


IMG_BASE = ["https://www.nta.go.jp/"]


def text(node):
    if isinstance(node, str):
        return node
    if node.tag in SKIP or is_noise(node):
        return ""
    if node.tag == "br":
        return "\n"
    if node.tag == "img":
        alt = (node.attrs.get("alt") or "").strip().replace("］", "]").replace("｜", "|")
        src = urllib.parse.urljoin(IMG_BASE[0], node.attrs.get("src") or "")
        return f"［図｜{src}｜{alt}］"
    return "".join(text(k) for k in node.kids)


def clean(s):
    s = s.replace(" ", " ")
    lines = [WS.sub(" ", x).strip() for x in s.split("\n")]
    return "\n".join(x for x in lines if x)


def raw_chars(node):
    """照合用：本文の文字（空白類と画像の代替表記を除く）。"""
    if isinstance(node, str):
        return node
    if node.tag in SKIP or node.tag == "img":
        return ""
    return "".join(raw_chars(k) for k in node.kids)


# ── ページ → ブロック ───────────────────────────────────────

def body_region(root):
    """本文の範囲。テンプレートの bodyArea を使い、ぱんくずと「先頭へ」リンクを除く。"""
    b = find(root, lambda n: n.attrs.get("id") == "bodyArea")
    if b is None:
        b = find(root, lambda n: n.attrs.get("id") == "contents") or find(root, lambda n: n.tag == "body") or root
    return b


def is_noise(n):
    if isinstance(n, str):
        return False
    cls = n.attrs.get("class", "")
    return (n.tag == "ol" and "breadcrumb" in cls) or "skip" in cls.split() or "page-top-link" in cls


def is_link_entry(n, s):
    """目次の行（段落の文字がほぼリンク1つだけ）。項目の索引には入れない。"""
    a = find(n, lambda x: x.tag == "a" and x.attrs.get("href") and not x.attrs["href"].startswith("#"))
    if a is None:
        return False
    at = re.sub(r"\s", "", clean(text(a)))
    st = re.sub(r"\s", "", s)
    return bool(at) and len(at) >= len(st) * 0.6


def level_of(n):
    cls = n.attrs.get("class", "")
    m = re.search(r"indent(\d)", cls) or re.search(r"marginLeft(\d)em", cls) or re.search(r"indent(\d)em", cls)
    return int(m.group(1)) if m else 0


def lead_strong(p):
    """段落の先頭にある <strong> の文字（項目番号の候補）。"""
    for k in p.kids:
        if isinstance(k, str):
            if k.strip():
                return None
            continue
        if k.tag in ("strong", "b"):
            return clean(text(k))
        if k.tag in ("a", "span") and not k.kids:
            continue
        if k.tag == "a" and k.attrs.get("name") is not None:
            continue
        return None
    return None


def _wrap(n):
    d = Node("div")
    d.kids = [n]
    return d


def table_rows(t):
    """表の行。<tr> が抜けているセル（原文の書き方の誤り）は、その位置で1行として扱う。"""
    rows = []

    def walk(n):
        loose = []
        for k in n.kids:
            if isinstance(k, str):
                continue
            if k.tag == "tr":
                if loose:
                    rows.append(loose)
                    loose = []
                rows.append(k)
            elif k.tag in ("td", "th"):
                loose.append(k)
            elif k.tag in ("thead", "tbody", "tfoot"):
                if loose:
                    rows.append(loose)
                    loose = []
                walk(k)
        if loose:
            rows.append(loose)

    walk(t)
    return rows


def table_block(t):
    rows = []
    for tr in table_rows(t):
        row = []
        for td in (tr if isinstance(tr, list) else tr.kids):
            if isinstance(td, str) or td.tag not in ("td", "th"):
                continue
            c = {"t": clean(text(td))}
            if td.tag == "th":
                c["h"] = 1
            for a, k in (("rowspan", "r"), ("colspan", "c")):
                v = td.attrs.get(a)
                if v and v.isdigit() and v != "1":
                    c[k] = int(v)
            row.append(c)
        rows.append(row)
    cap = find(t, lambda n: n.tag == "caption")
    b = {"table": rows}
    if cap is not None:
        b["cap"] = clean(text(cap))
    return b


def iter_tags(node, tag):
    if isinstance(node, str):
        return
    for k in node.kids:
        if isinstance(k, str):
            continue
        if k.tag == tag:
            yield k
        elif k.tag != "table":
            yield from iter_tags(k, tag)


def blocks_of(node, out, lv=0):
    for k in node.kids:
        if isinstance(k, str):
            s = clean(k)
            if s:
                out.append({"p": s, "lv": lv})
            continue
        if k.tag in SKIP or is_noise(k):
            continue
        tag = k.tag
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            s = clean(text(k))
            if s:
                out.append({"h": int(tag[1]), "t": s})
        elif tag == "p":
            s = clean(text(k))
            if not s:
                continue
            b = {"p": s, "lv": level_of(k) or lv}
            cls = k.attrs.get("class", "")
            if k.attrs.get("align") == "center" or "center" in cls.split():
                b["ctr"] = 1
            no = lead_strong(k)
            if no:
                b["no"] = no
            if is_link_entry(k, s):
                b["toc"] = 1
            elif find(k, lambda x: x.tag == "a" and (x.attrs.get("href") or "#")[:1] != "#"):
                b["a"] = 1
            out.append(b)
        elif tag == "table":
            out.append(table_block(k))
        elif tag in ("ul", "ol"):
            n = 0
            for li in k.kids:
                if isinstance(li, str):
                    if clean(li):
                        out.append({"p": clean(li), "lv": lv + 1})
                    continue
                if li.tag in ("ul", "ol"):  # 項目に入っていない入れ子の箇条（原文の書き方の誤り）も拾う
                    holder = Node("div")
                    holder.kids = [li]
                    blocks_of(holder, out, lv + 1)
                    continue
                if li.tag != "li":
                    blocks_of(Node("div", None, None) if False else _wrap(li), out, lv + 1)
                    continue
                n += 1
                inner = []
                blocks_of(li, inner, lv + 1)
                if inner and "p" in inner[0] and is_link_entry(li, clean(text(li))):
                    inner[0]["toc"] = 1
                # li の直下の文字を1段落にまとめる（入れ子の箇条はそのまま）
                if inner and "p" in inner[0]:
                    inner[0]["li"] = n if tag == "ol" else 0
                out.extend(inner)
        elif tag in ("div", "section", "blockquote", "center", "dl", "dd", "dt", "span", "font", "a", "strong", "b", "em", "u"):
            if tag in ("div", "section", "blockquote", "center", "dl", "dd", "dt"):
                blocks_of(k, out, lv + (1 if tag in ("blockquote", "dd") else 0))
            else:
                s = clean(text(k))
                if s:
                    out.append({"p": s, "lv": lv})
        elif tag == "img":
            out.append({"p": clean(text(k)), "lv": lv})
        else:
            s = clean(text(k))
            if s:
                out.append({"p": s, "lv": lv})
    return out


# ── 項目番号 ───────────────────────────────────────────────

DASH = re.compile(r"[‐‑‒–—―−ーｰ－-]")


def norm_no(raw):
    s = unicodedata.normalize("NFKC", raw).replace(" ", "").replace("　", "")
    s = DASH.sub("-", s)
    s = s.strip("　 .．")
    return s


HIER = re.compile(r"\d+(?:の\d+)*(?:[・~]\d+(?:の\d+)*)*共?(?:-\d+(?:の\d+)*)+")
ART = re.compile(r"第(\d+)条((?:の\d+)*)\S*関係")


def item_id(raw, mode, ctx_article):
    s = norm_no(raw)
    if not s:
        return None
    if mode == "hier":
        return s if HIER.fullmatch(s) else None
    if mode == "plain":
        if re.fullmatch(r"\d+(?:-\d+)*(?:の\d+)?", s):
            return s
        return None
    if mode == "article":
        if ctx_article and re.fullmatch(r"\d+(?:の\d+)?(?:-\d+(?:の\d+)?)*", s):
            return f"{ctx_article}-{s}"
        return None
    if mode == "jo":
        m = re.fullmatch(r"第(\d+(?:の\d+)?)条", s)
        return m.group(1) if m else None
    return None


# ── 目次のたどり方 ──────────────────────────────────────────

def links_in_order(root, page_url, scope):
    """目次ページ内の、範囲内リンクとグループ見出しを文書順に。"""
    seq = []

    def walk(n):
        if isinstance(n, str):
            return
        if n.tag in SKIP or is_noise(n):
            return
        if n.tag == "a" and n.attrs.get("href"):
            u = urllib.parse.urljoin(page_url, n.attrs["href"]).split("#")[0].replace("http://", "https://")
            if u.startswith(scope) and u.endswith((".htm", ".html")):
                seq.append(("link", u, clean(text(n))))
            return
        if n.tag == "li":
            own = "".join(text(k) for k in n.kids if isinstance(k, str) or k.tag not in ("ul", "ol"))
            has_a = any(not isinstance(k, str) and find(k, lambda x: x.tag == "a") for k in n.kids
                        if isinstance(k, str) or k.tag not in ("ul", "ol"))
            if not has_a and clean(own):
                seq.append(("head", clean(own), "li"))
                for k in n.kids:
                    if not isinstance(k, str) and k.tag in ("ul", "ol"):
                        walk(k)
                return
        if n.tag in ("h2", "h3", "h4") or (n.tag == "p" and n.attrs.get("class", "") in ("center", "txt-big center")):
            if not find(n, lambda x: x.tag == "a"):
                s = clean(text(n))
                if s:
                    seq.append(("head", s, n.tag))
                return
        for k in n.kids:
            walk(k)

    walk(body_region(root))
    return seq


def depth_of(label):
    if re.match(r"第\S+編", label):
        return 0
    if re.match(r"第\S+章", label):
        return 1
    if re.match(r"第\S+節", label):
        return 2
    if re.match(r"第\S+款", label):
        return 3
    if re.match(r"第\S+目", label):
        return 4
    return 2


def build_one(t):
    key = t["key"]
    mode = NUMBERING.get(key, "hier")
    meta = json.loads((RAW / key / "_pages.json").read_text(encoding="utf-8"))
    scope = meta["scope"]
    by_url = {p["url"]: p for p in meta["pages"]}
    doms = {}

    def dom(url):
        if url not in doms:
            raw = (RAW / key / "pages" / by_url[url]["path"]).read_bytes()
            doms[url] = parse(decode(raw))
        return doms[url]

    # 目次をたどって、ページの並びとグループ見出しを決める
    order, toc, visited = [], [], set()

    def visit(url, label, depth):
        if url in visited or url not in by_url:
            return
        visited.add(url)
        order.append(url)
        toc.append({"u": url, "t": label, "d": depth})
        seq = links_in_order(dom(url), url, scope)
        n_links = sum(1 for s in seq if s[0] == "link")
        if n_links >= 2:  # 目次の役割をするページ：リンク先を順にたどる
            for kind, a, b in seq:
                if kind == "head" and depth < 3 and len(a) <= 60:
                    toc.append({"g": a, "d": depth_of(a)})
                elif kind == "link":
                    visit(a, b, depth_of(b) if depth_of(b) != 2 or not re.match(r"第\d+条", b) else 2)

    visit(meta["entry"], t["title"] + "（目次）", 0)
    for p in meta["pages"]:  # 目次から辿れなかったページも落とさない
        if p["url"] not in visited:
            visited.add(p["url"])
            order.append(p["url"])
            toc.append({"u": p["url"], "t": p["path"], "d": 2})

    out = OUT / key
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    index, pages, bad, n_items, body_hash = {}, [], [], 0, hashlib.sha256()
    page_no = {u: i for i, u in enumerate(order)}
    for u in order:
        n = page_no[u]
        root = dom(u)
        IMG_BASE[0] = u
        region = body_region(root)
        blocks = blocks_of(region, [])
        h1 = next((b["t"] for b in blocks if b.get("h") == 1), None)
        # 項目番号を付ける。目次のページ（番号つき段落の過半がリンク）と、旧版（日付のフォルダ）は索引に入れない
        rel = by_url[u]["path"]
        old_ver = bool(re.search(r"(^|/)(19|20)\d{6}/", rel))
        numbered = [b for b in blocks if "p" in b and (b.get("no") or HIER.match(norm_no(b["p"][:20]) or "-"))]
        is_toc_page = numbered and sum(1 for b in numbered if b.get("toc") or b.get("a")) > len(numbered) / 2
        skip_index = old_ver or is_toc_page
        art = None
        cur_cap = None
        for b in blocks:
            if "h" in b:
                m = ART.search(b["t"])
                if m:
                    art = m.group(1) + m.group(2).replace("の", "の")
                if b["h"] >= 2 and re.fullmatch(r"[（(].*[）)]", b["t"]):
                    cur_cap = b["t"]
                continue
            cand = None
            if "p" in b and not b.get("toc") and not skip_index:
                head = re.split(r"[\s\u3000]", b["p"], maxsplit=1)[0]
                if b.get("no"):
                    nb = norm_no(b["no"])
                    nh = norm_no(head)
                    cand = head if nb and nh.startswith(nb) else b["no"]
                elif mode == "hier" and b.get("lv", 0) <= 1 and HIER.fullmatch(norm_no(head) or "-"):
                    cand = head  # 番号が太字になっていない項目
            if cand:
                iid = item_id(cand, mode, art)
                if iid:
                    k2, j = iid, 2
                    while k2 in index:
                        k2 = f"{iid}#{j}"
                        j += 1
                    b["id"] = k2
                    index[k2] = n
                    n_items += 1
                    if cur_cap:
                        b["cap"] = cur_cap
                    cur_cap = None
        # 照合：原文の本文の文字 と 変換後の文字（空白類を除く）
        def strip_ws(s):
            return re.sub(r"[\s　]", "", s)

        src = strip_ws(raw_chars_region(region))
        got = strip_ws("".join(block_chars(b) for b in blocks))
        if src != got:
            i = next((j for j in range(min(len(src), len(got))) if src[j] != got[j]), min(len(src), len(got)))
            bad.append(f"{by_url[u]['path']}: {i}文字目〜 原文「{src[i:i+20]}」/ 変換「{got[i:i+20]}」")
        body_hash.update(src.encode("utf-8"))
        title = next((e["t"] for e in toc if e.get("u") == u), None)
        page = {"n": n, "url": u, "title": h1 or title, "blocks": blocks}
        if old_ver:
            page["old"] = rel.split("/")[0] if re.match(r"\d{8}", rel) else re.search(r"(\d{8})/", rel).group(1)
        (out / f"t{n}.json").write_text(json.dumps(page, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        pages.append({"n": n, "url": u, "t": title or h1 or by_url[u]["path"], "h1": h1,
                      "items": [b["id"] for b in blocks if b.get("id")], "old": page.get("old")})

    toc_out = []
    for e in toc:
        if "g" in e:
            toc_out.append(e)
        else:
            p = pages[page_no[e["u"]]]
            toc_out.append({"n": p["n"], "t": e["t"] or p["t"], "d": e["d"], "k": len(p["items"])})
    tj = {"key": key, "abbr": t["abbr"], "title": t["title"], "kind": "tsutatsu", "numbering": mode,
          "entry": meta["entry"], "fetched_at": meta["fetched_at"], "toc": toc_out,
          "pages": [{k: v for k, v in {"n": p["n"], "url": p["url"], "t": p["t"], "items": p["items"], "old": p["old"]}.items() if v is not None}
                    for p in pages],
          "where": index}
    (out / "toc.json").write_text(json.dumps(tj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"key": key, "abbr": t["abbr"], "title": t["title"], "kind": "tsutatsu", "group": t["group"],
            "entry": meta["entry"], "fetched_at": meta["fetched_at"], "pages": len(pages), "items": n_items,
            "body_sha256": body_hash.hexdigest()}, bad


def raw_chars_region(region):
    parts = []

    def walk(n):
        if isinstance(n, str):
            parts.append(n)
            return
        if n.tag in SKIP or is_noise(n) or n.tag == "img":
            return
        for k in n.kids:
            walk(k)

    walk(region)
    return "".join(parts)


FIG = re.compile(r"［図｜[^］]*］")


def block_chars(b):
    if "table" in b:
        s = (b.get("cap") or "") + "".join(c["t"] for r in b["table"] for c in r)
    elif "h" in b:
        s = b["t"]
    else:
        s = b.get("p", "")
    return FIG.sub("", s)


def decode(b):
    try:
        return b.decode("cp932")
    except UnicodeDecodeError:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("cp932", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "tsutatsu.json").read_text(encoding="utf-8"))
    idx_path = OUT / "index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    old = {x["key"]: x for x in index.get("tsutatsu", [])}
    res, ng = [], 0
    for t in cfg["tsutatsu"]:
        if args.only and t["key"] not in args.only:
            if t["key"] in old:
                res.append(old[t["key"]])
            continue
        r, bad = build_one(t)
        res.append(r)
        mark = "OK" if not bad else "NG"
        print(f"[{mark}] {t['key']:13s} {r['pages']:>4d} ページ 項目 {r['items']:>5,d}  全ページ原文照合" + ("" if not bad else f" 不一致 {len(bad)} ページ"))
        for x in bad[:4]:
            print("      " + x)
        ng += bool(bad)
    index["tsutatsu"] = res
    index["tsutatsu_built_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    idx_path.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n{'全通達 合格' if not ng else f'{ng} 通達で不一致'}")
    sys.exit(1 if ng else 0)


if __name__ == "__main__":
    main()
