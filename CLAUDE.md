# JPX 信用残トラッカー — Claude向け説明書

JPXの「銘柄別信用取引残高」(日次PDF)を毎日自動取得して蓄積し、信用倍率の変化をiPhoneで見られるWebページにして、条件に合えばntfyでプッシュ通知する仕組み。

## オーナーについて（必ず守ること）
- オーナーはプログラミングをしない。変更は Claude がこのリポジトリに直接コミット＆プッシュして反映する。
- **コードを書く前に、まず仕様を会話で確認する**（オーナーの希望）。小さな設定変更（ウォッチ追加・条件の数値変更）は確認後すぐ反映してよい。
- 説明は日本語・平易に。反映後は「何が変わったか」と「iPhoneで再読み込みすれば見られる」ことを伝える。
- 公開ページ: https://yosshi09230.github.io/jpx-margin/ （公開リポジトリ。秘密情報をコミットしない）

## よくある依頼と変更箇所
| 依頼 | 変更するところ |
|---|---|
| ウォッチ銘柄の追加・削除 | `settings.json` の `watchlist`（`{"code":"7203","memo":"トヨタ"}`）。コードは4桁(英字含む 例 285A) |
| 通知条件の変更 | `settings.json` の `watch_alerts.rules`（ウォッチ・前日比）/ `market_alerts`（全銘柄・前週比、足切り条件） |
| 通知を止める | `settings.json` の `notify.enabled` を false |
| 画面の変更 | `site/index.html`（1ファイル完結。外部ライブラリなし。グラフは自前SVG） |
| 指標の追加 | `app/build.py` の `compute()` で行(`rec`)に項目を追加 → 画面で表示 |

`settings.json`・`site/`・`app/build.py` を main にプッシュすると、ワークフローが **データ取得なしで** サイトを再生成・再公開する（2〜3分）。

### 通知ルールの書き方
`{"name": "表示名", "metric": "<指標>", "abs_gte": 30}` — `abs_gte`(絶対値以上) / `gte`(以上) / `lte`(以下)。
指標名 = `<項目>_pct1`(前日比%) / `<項目>_pct5`(前週比%=5営業日前比) / `<項目>_d1`,`_d5`(倍率のポイント差; rs/rg/raのみ) / `<項目>`(現在値)。
項目: `rs`制度倍率 `rg`一般倍率 `ra`合計倍率 `bs`制度買残 `ss`制度売残 `bg`一般買残 `sg`一般売残 `b`買残計 `s`売残計。その他 `br`買残上場比% `mc`時価総額(億円) `px`株価 `pxc`株価前日比% `days`買残/20日平均出来高。
全銘柄ルールは ETF等(業種なし)を除外し、`min_market_cap_oku` と、指標に対応する残高(倍率なら売残)の `min_balance_shares` で足切り（`app/notify.py` の `BAL_KEY`）。

## 構成
```
settings.json            設定（ウォッチ・通知条件・取得パラメータ）
app/run.py               毎日の処理の入口（--no-fetch / --no-notify）
app/fetch.py             取得: 信用残PDF, 東証上場銘柄一覧(data_j), 日経225, 株価/発行済株数(yfinance)
app/pdf_parser.py        PDF読み取り（文字のx座標で14列に振り分け。総合計行と照合）
app/build.py             指標計算 → _site/data/{latest,status,alerts}.json, hist/<code>.json
app/notify.py            通知判定と ntfy 送信（data/alerts/YYYYMMDD.json に記録・二重送信防止）
site/                    画面（index.html, manifest, アイコン）
data/margin/YYYY/YYYYMMDD.csv.gz   信用残（日付=申込日）。過去分はJPXに5営業日分しか無いので消さないこと
data/prices/YYYY/YYYYMMDD.csv      株価（終値・出来高）
data/listed.csv, nikkei225.csv, shares.csv, state.json
.github/workflows/daily.yml        平日19:00/21:30 JST 実行 → data をコミット → GitHub Pages に公開
```
- 通知先 ntfy のトピック名はリポジトリ Secret `NTFY_TOPIC`（公開リポジトリのためファイルに書かない）。
- `_site/` はコミットしない（Pages artifact として公開）。

## 動作確認のしかた
- ローカル: `pip install -r app/requirements.txt` → `python app/run.py --no-fetch` → `_site/` を `python -m http.server` で表示。
- 本番: `gh workflow run daily-update -R yosshi09230/jpx-margin` → `gh run list` / `gh run view --log` でログ確認。
- 画面を変えたら iPhone 幅（390px）でスクリーンショットを撮って確認する。

## 既知の注意点
- JPX PDF のレイアウトが変わると `pdf_parser.py` の列座標(COL_RIGHT)がずれる → `validate()` の照合警告がログに出る。
- 株価・発行済株数は yfinance（非公式）。取れない日はあっても信用残の蓄積は止まらない。
- 前週比は6営業日分、前日比は2営業日分のデータが溜まってから計算される。
- 新規上場銘柄は前日比が空欄（None）になる。
