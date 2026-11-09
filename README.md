# 税務六法Web（zeiho-web）

国税・地方税の法律・施行令・施行規則を、e-Gov法令検索の原文から読みやすく表示する条文ビューア。

公開URL：https://hide0703-dev.github.io/zeiho-web/

## 収録

所得税・法人税・地方法人税・相続税・消費税・租税特別措置・国税通則・国税徴収・印紙税・地方税の、
法律・施行令・施行規則 計30本（一覧は `laws.json`）。通達は準備中。

## 改正への追随（確認後公開）

`.github/workflows/update.yml` が毎月2日、改正の多い3〜4月・9〜10月は毎週月曜にも e-Gov を確認する。
改正があれば取り直し・変換・原文との全文照合を行い、合格したら「確認待ち」のプルリクエストを立てる。
**マージした時点でサイトに反映**される。照合で1条でも食い違えば止まり、Issue で知らせる。

## 手元での作業

```sh
python scripts/fetch_laws.py        # e-Gov から原文XMLを data/raw/ に取得
python scripts/build_laws.py        # docs/data/ に表示用JSONを出力
python scripts/verify_laws.py       # 全条文を原文XMLと照合
python -m http.server 8000 --directory docs   # http://127.0.0.1:8000/
```

標準ライブラリだけで動く（Python 3.10 以上）。

## 出典

法令データは e-Gov法令検索（デジタル庁）の法令APIから取得し、出典を明示して加工・表示している。
法令は著作権法第13条により著作権の目的とならない。正確な内容は必ず原文・官報で確認すること。
