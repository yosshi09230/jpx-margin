# -*- coding: utf-8 -*-
"""毎日の処理（GitHub Actionsから実行）
  python app/run.py            取得 → 計算 → 通知 → サイト生成
  python app/run.py --no-fetch 取得せず、計算とサイト生成のみ（画面だけ直したとき）
"""
import sys
import argparse

import fetch
import build
import notify
from common import log, load_settings, load_state, save_state, normalize_code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()
    settings = load_settings()
    cfg = settings.get("data", {})
    state = load_state()
    errors = []

    def step(name, fn):
        try:
            return fn()
        except Exception as e:
            log.exception("%s: %s", name, e)
            errors.append("%s: %s" % (name, str(e)[:200]))

    if not args.no_fetch:
        step("信用残PDF", fetch.fetch_margin)
        step("上場銘柄一覧", lambda: fetch.fetch_listed(state, cfg))
        step("日経225", lambda: fetch.fetch_nikkei(state, cfg))
        dates = fetch.have_margin_dates()
        if dates:
            import pandas as pd
            codes = list(pd.read_csv(fetch.margin_path(dates[-1]), dtype={"code": str}).code)
            watch = {normalize_code(w["code"]) for w in settings.get("watchlist", [])}
            step("株価", lambda: fetch.fetch_prices(cfg, codes))
            step("発行済株数", lambda: fetch.fetch_shares(cfg, codes, watch))
        save_state(state)

    rows, status, m, pr = build.compute(settings)
    if rows and not args.no_notify:
        step("通知", lambda: notify.run(rows, status, settings))
    build.build_site(rows, status, m, pr, settings, errors)
    log.info("完了（エラー %d 件）", len(errors))
    # 信用残が1件も無い場合のみ失敗扱い（他の一部失敗は画面に表示して続行）
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
