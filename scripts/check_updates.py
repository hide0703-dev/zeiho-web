#!/usr/bin/env python3
"""公開中のデータ（docs/data/index.json）と、e-Gov の現在の版（data/revisions.json）を比べる。

先に `python scripts/fetch_laws.py --revisions-only` で data/revisions.json を作っておく。
改正のあった法令の key を表示し、GitHub Actions では出力変数 changed / keys / summary を書く。
"""
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
pub = {x["key"]: x for x in json.loads((ROOT / "docs/data/index.json").read_text(encoding="utf-8"))["laws"]}
now = json.loads((ROOT / "data/revisions.json").read_text(encoding="utf-8"))

changed = []
lines = [f"=== 更新チェック（e-Gov と公開中データの比較）==="]
for key, cur in now.items():
    old = pub.get(key, {})
    if old.get("revision_id") != cur.get("revision_id"):
        changed.append(key)
        lines.append(f"  {cur['title']}　施行 {old.get('enforced', '－')} → {cur.get('enforced')}")
        if cur.get("amendment_law_title"):
            lines.append(f"      改正法令：{cur['amendment_law_title']}")
if not changed:
    lines.append("  改正はありませんでした。")
summary = "・".join(now[k]["title"] for k in changed)
lines.append(f"\n改正あり {len(changed)} 本" if changed else "")
print("\n".join(lines))
(ROOT / "check.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"changed={'true' if changed else 'false'}\n")
        f.write(f"keys={' '.join(changed)}\n")
        f.write(f"summary={summary[:200]}\n")
