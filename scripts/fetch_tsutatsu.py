#!/usr/bin/env python3
"""国税庁サイトから tsutatsu.json の通達ページを取得し、data/raw_tsutatsu/{key}/ に保存する。

目次ページ（entry）から、同じ通達のフォルダ（scope）の下にある .htm へのリンクだけを辿る。
直列・1秒間隔。標準ライブラリだけで動く。

  python scripts/fetch_tsutatsu.py [--only key ...]

保存するもの:
  data/raw_tsutatsu/{key}/pages/{scope からの相対パス}   取得したHTML（そのままのバイト列）
  data/raw_tsutatsu/{key}/_pages.json                     取得順・URL・SHA-256
"""
import argparse
import hashlib
import html.parser
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw_tsutatsu"
UA = "zeiho-web/1.0 (+https://github.com/hide0703-dev/zeiho-web)"
WAIT = 1.0
MAX_PAGES = 1500


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.hrefs.append(v)


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read(), r.geturl()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, url
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def decode(b):
    head = b[:2000].lower()
    for enc in ("utf-8", "shift_jis", "cp932", "euc-jp"):
        if enc.encode() in head or enc.replace("_", "-").encode() in head:
            try:
                return b.decode("cp932" if enc in ("shift_jis",) else enc)
            except UnicodeDecodeError:
                pass
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def crawl(t):
    scope = t["scope"]
    queue = [t["entry"]]
    seen = set(queue)
    pages = []
    dest = OUT / t["key"] / "pages"
    dest.mkdir(parents=True, exist_ok=True)
    while queue and len(pages) < MAX_PAGES:
        url = queue.pop(0)
        body, final = get(url)
        time.sleep(WAIT)
        if body is None:
            print(f"  404 {url}")
            continue
        rel = url[len(scope):] or "index.htm"
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        pages.append({"url": url, "path": rel, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
        lp = Links()
        try:
            lp.feed(decode(body))
        except Exception:  # noqa: BLE001
            pass
        for h in lp.hrefs:
            u = urllib.parse.urljoin(url, h).split("#")[0]
            if u.startswith("http://"):
                u = "https://" + u[len("http://"):]
            if u.startswith(scope) and u.endswith((".htm", ".html")) and u not in seen:
                seen.add(u)
                queue.append(u)
    (OUT / t["key"] / "_pages.json").write_text(
        json.dumps({"key": t["key"], "title": t["title"], "entry": t["entry"], "scope": scope,
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pages": pages},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "tsutatsu.json").read_text(encoding="utf-8"))
    failed = []
    for t in cfg["tsutatsu"]:
        if args.only and t["key"] not in args.only:
            continue
        try:
            pages = crawl(t)
            print(f"[取得] {t['key']:14s} {len(pages):>5d} ページ  {sum(p['bytes'] for p in pages):>12,d} バイト  {t['title']}")
        except Exception as e:  # noqa: BLE001
            print(f"[失敗] {t['key']}: {e}", file=sys.stderr)
            failed.append(t["key"])
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
