#!/usr/bin/env python3
"""通達の取り直し・変換のあとに実行する。公開中（直前のコミット）のデータと本文を比べ、
変わっていない通達は公開中のデータに戻す（取得日時だけの差分を出さないため）。

変わった通達を check.txt に追記し、GitHub Actions では出力変数 tsu_changed / tsu_summary を書く。
"""
import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
IDX = ROOT / "docs/data/index.json"
old = json.loads(subprocess.run(["git", "show", "HEAD:docs/data/index.json"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout)
new = json.loads(IDX.read_text(encoding="utf-8"))
old_t = {t["key"]: t for t in old.get("tsutatsu", [])}

changed, kept = [], []
for i, t in enumerate(new.get("tsutatsu", [])):
    o = old_t.get(t["key"])
    if o and o.get("body_sha256") == t.get("body_sha256"):
        subprocess.run(["git", "checkout", "--", f"docs/data/{t['key']}"], cwd=ROOT, check=True)
        subprocess.run(["git", "clean", "-fdq", "--", f"docs/data/{t['key']}"], cwd=ROOT, check=True)
        new["tsutatsu"][i] = o
        kept.append(t["key"])
    else:
        changed.append(t)
if not changed:
    new["tsutatsu_built_at"] = old.get("tsutatsu_built_at", new.get("tsutatsu_built_at"))
IDX.write_text(json.dumps(new, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

lines = ["", "=== 通達の更新チェック（国税庁ホームページと公開中データの比較）==="]
if changed:
    for t in changed:
        o = old_t.get(t["key"], {})
        lines.append(f"  {t['title']}：本文に変更あり（項目 {o.get('items', '－')} → {t['items']}、ページ {o.get('pages', '－')} → {t['pages']}）")
else:
    lines.append("  変更はありませんでした。")
with open(ROOT / "check.txt", "a", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"tsu_changed={'true' if changed else 'false'}\n")
        f.write("tsu_summary=" + "・".join(t["abbr"] for t in changed)[:150] + "\n")
