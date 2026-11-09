#!/usr/bin/env python3
"""e-Gov 法令API から laws.json の法令を取得し、原文XMLを data/raw/ に保存する。

  python scripts/fetch_laws.py              # 全法令
  python scripts/fetch_laws.py --only houjin_act shohi_act
  python scripts/fetch_laws.py --revisions-only   # 本文は取らず、改正情報だけ data/revisions.json に書く

標準ライブラリだけで動く。e-Gov には直列・1秒間隔で当たる。
法令の特定は「法令名の完全一致」で行い、現行（施行中）の版を選ぶ。
"""
import argparse
import base64
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
V2 = "https://laws.e-gov.go.jp/api/2"
V1_HOSTS = ["https://laws.e-gov.go.jp/api/1", "https://elaws.e-gov.go.jp/api/1"]
UA = "zeiho-web/1.0 (+https://github.com/hide0703-dev/zeiho-web)"
WAIT = 1.0


def get(url, accept="application/json", tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"取得失敗 {url}: {last}")


def load_laws():
    cfg = json.loads((ROOT / "laws.json").read_text(encoding="utf-8"))
    for g in cfg["groups"]:
        for law in g["laws"]:
            yield g, law


def resolve(title):
    """法令名の完全一致で現行の版を探す。見つかった law_info と revision_info を返す。"""
    q = urllib.parse.urlencode({"law_title": title, "response_format": "json"})
    data = json.loads(get(f"{V2}/laws?{q}"))
    cands = []
    for item in data.get("laws", []):
        info = item.get("law_info", {})
        rev = item.get("current_revision_info") or item.get("revision_info") or {}
        if rev.get("law_title") == title and rev.get("repeal_status", "None") in ("None", "", None):
            cands.append((info, rev))
    if not cands:
        names = sorted({(i.get("current_revision_info") or i.get("revision_info") or {}).get("law_title", "?")
                        for i in data.get("laws", [])})
        raise RuntimeError(f"「{title}」が完全一致で見つからない。候補: {names[:10]}")
    if len(cands) > 1:
        ids = [c[0].get("law_id") for c in cands]
        raise RuntimeError(f"「{title}」が複数該当: {ids}")
    return cands[0]


def find_law_xml(text):
    """応答の中から <Law ...> ～ </Law> を取り出す。"""
    s = text.find("<Law ")
    if s < 0:
        s = text.find("<Law>")
    e = text.rfind("</Law>")
    if s < 0 or e < 0:
        return None
    return text[s:e + len("</Law>")]


def fetch_full_text(law_id):
    """本文XMLを取る。v2（XML形式指定）→ v1 の順に試す。"""
    errors = []
    q = urllib.parse.urlencode({"law_full_text_format": "xml", "response_format": "json"})
    try:
        body = json.loads(get(f"{V2}/law_data/{law_id}?{q}"))
        ft = body.get("law_full_text")
        if isinstance(ft, str):
            text = ft
            if not text.lstrip().startswith("<"):
                text = base64.b64decode(text).decode("utf-8")
            xml = find_law_xml(text)
            if xml:
                return xml, "v2", body.get("revision_info") or {}
        errors.append(f"v2: law_full_text の形が想定外 ({type(ft).__name__})")
    except Exception as e:  # noqa: BLE001
        errors.append(f"v2: {e}")
    for host in V1_HOSTS:
        try:
            text = get(f"{host}/lawdata/{law_id}", accept="application/xml").decode("utf-8")
            xml = find_law_xml(text)
            if xml:
                return xml, "v1", {}
            errors.append(f"v1 {host}: Law 要素が無い（先頭: {text[:200]!r}）")
        except Exception as e:  # noqa: BLE001
            errors.append(f"v1 {host}: {e}")
    raise RuntimeError(" / ".join(errors))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--revisions-only", action="store_true")
    args = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    revisions = {}
    failed = []
    for g, law in load_laws():
        key = law["key"]
        if args.only and key not in args.only:
            continue
        try:
            info, rev = resolve(law["title"])
            time.sleep(WAIT)
            meta = {
                "key": key,
                "title": law["title"],
                "law_id": info.get("law_id"),
                "law_num": info.get("law_num"),
                "law_type": info.get("law_type"),
                "promulgation_date": info.get("promulgation_date"),
                "revision_id": rev.get("law_revision_id"),
                "enforced": rev.get("amendment_enforcement_date"),
                "amendment_law_title": rev.get("amendment_law_title"),
            }
            revisions[key] = meta
            print(f"[特定] {key:18s} {meta['law_id']}  施行 {meta['enforced']}  {meta['law_num']}")
            if args.revisions_only:
                continue
            xml, via, rev2 = fetch_full_text(meta["law_id"])
            time.sleep(WAIT)
            if rev2.get("law_revision_id"):
                meta["revision_id"] = rev2["law_revision_id"]
                meta["enforced"] = rev2.get("amendment_enforcement_date") or meta["enforced"]
            meta["via"] = via
            meta["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            (RAW / f"{key}.xml").write_text(xml, encoding="utf-8")
            (RAW / f"{key}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[取得] {key:18s} {len(xml):>10,d} 文字（{via}）")
        except Exception as e:  # noqa: BLE001
            print(f"[失敗] {key:18s} {e}", file=sys.stderr)
            failed.append(key)

    out = ROOT / "data" / ("revisions.json" if args.revisions_only else "raw/_revisions.json")
    out.write_text(json.dumps(revisions, ensure_ascii=False, indent=1), encoding="utf-8")
    if failed:
        print(f"失敗 {len(failed)} 本: {' '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
