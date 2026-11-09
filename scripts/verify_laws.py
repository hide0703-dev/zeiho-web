#!/usr/bin/env python3
"""変換結果を原文XMLと突き合わせる。1項目でも食い違えば終了コード1。

照合すること（法令ごと）:
  1. 本則の条: 原文 MainProvision 内の Article の Num の並びと、出力の条IDの並びが一致する
  2. 本則の条文: 各条について、原文の全文字（空白を除く）と、出力の見出し・項・号・表の文字を
     つなげたものが一致する（文字の抜け・重複・順序の狂いを検出する）
  3. 附則の本数、別表等の本数が一致する
  4. 目次から全ての条に到達でき、条→チャンクの対応が正しい

  python scripts/verify_laws.py [--only key ...]
"""
import argparse
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "docs" / "data"
WS = re.compile(r"\s+")


def raw_text(el):
    parts = []

    def walk(e):
        if e.tag == "Rt":
            return
        if e.text:
            parts.append(e.text)
        for c in e:
            walk(c)
            if c.tail:
                parts.append(c.tail)

    walk(el)
    return WS.sub("", "".join(parts))


def out_text(obj):
    """出力JSONの文字列値を、出現順に全部つなげる（id・番号属性などは除く）。"""
    skip = {"id", "n", "path", "del", "r", "c_span", "law", "kind", "f"}
    parts = []

    order = {"title": 0, "c": 1, "t": 2, "s": 3, "p": 4, "i": 5, "head": 6, "rows": 7, "x": 8, "remarks": 9}

    def walk(o, k=None):
        if isinstance(o, dict):
            for kk in sorted(o, key=lambda z: order.get(z, 50)):
                if kk in skip:
                    continue
                walk(o[kk], kk)
        elif isinstance(o, list):
            for v in o:
                walk(v, k)
        elif isinstance(o, str):
            parts.append(o)

    walk(obj)
    return WS.sub("", "".join(parts))


def leaves(nodes):
    for n in nodes:
        if n["k"] == "A":
            yield n["id"]
        else:
            yield from leaves(n.get("c", []))


def verify(key):
    errs = []
    root = ET.fromstring((RAW / f"{key}.xml").read_text(encoding="utf-8"))
    body = root.find("LawBody")
    main = body.find("MainProvision")
    raw_arts = [a for a in main.iter("Article")]
    toc = json.loads((OUT / key / "toc.json").read_text(encoding="utf-8"))

    chunks = {}

    def chunk(prefix, n):
        if (prefix, n) not in chunks:
            chunks[(prefix, n)] = json.loads((OUT / key / f"{prefix}{n}.json").read_text(encoding="utf-8"))
        return chunks[(prefix, n)]

    out_arts = []
    n = 0
    while (OUT / key / f"m{n}.json").exists():
        out_arts += chunk("m", n)
        n += 1

    # 1. 条の並び
    raw_ids = [a.get("Num") for a in raw_arts]
    out_ids = [a["id"] for a in out_arts if not a["id"].startswith("p")]
    if raw_ids != out_ids:
        errs.append(f"条の並びが不一致（原文 {len(raw_ids)} / 出力 {len(out_ids)}）")

    # 2. 条文の文字
    out_by = {a["id"]: a for a in out_arts}
    bad = 0
    for a in raw_arts:
        o = out_by.get(a.get("Num"))
        if o is None:
            continue
        rt = raw_text(a)
        ot = out_text({k: v for k, v in o.items() if k != "path"})
        if rt != ot:
            bad += 1
            if bad <= 3:
                i = next((j for j in range(min(len(rt), len(ot))) if rt[j] != ot[j]), min(len(rt), len(ot)))
                errs.append(f"条 {a.get('Num')} の文字が不一致（{i}文字目〜 原文「{rt[i:i+25]}」/ 出力「{ot[i:i+25]}」）")
    if bad > 3:
        errs.append(f"…ほか {bad - 3} 条で不一致")

    # 3. 附則・別表等の本数
    n_sup = len(body.findall("SupplProvision"))
    if n_sup != len(toc["suppl"]):
        errs.append(f"附則の本数が不一致（原文 {n_sup} / 出力 {len(toc['suppl'])}）")
    appdx_tags = ("AppdxTable", "AppdxStyle", "AppdxFormat", "AppdxFig", "AppdxNote", "Appdx")
    n_ap = sum(1 for e in body if e.tag in appdx_tags)
    if n_ap != len(toc["appdx"]):
        errs.append(f"別表等の本数が不一致（原文 {n_ap} / 出力 {len(toc['appdx'])}）")

    # 4. 目次と対応表
    tl = list(leaves(toc["toc"]))
    if tl != [a["id"] for a in out_arts]:
        errs.append("目次の条の並びが本文と不一致")
    for aid, f in toc["where"].items():
        if not any(x["id"] == aid for x in chunk("m", f)):
            errs.append(f"条 {aid} が対応表のチャンク m{f} に無い")
            break
    return len(raw_arts), bad, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    index = json.loads((OUT / "index.json").read_text(encoding="utf-8"))
    ng = 0
    for law in index["laws"]:
        if args.only and law["key"] not in args.only:
            continue
        n, bad, errs = verify(law["key"])
        mark = "OK" if not errs else "NG"
        print(f"[{mark}] {law['key']:18s} 本則 {n:>5,d} 条を全文照合" + ("" if not errs else f"  不一致 {bad} 条"))
        for e in errs:
            print("      " + e)
        ng += bool(errs)
    print(f"\n{'全法令 合格' if not ng else f'{ng} 法令で不一致'}")
    sys.exit(1 if ng else 0)


if __name__ == "__main__":
    main()
