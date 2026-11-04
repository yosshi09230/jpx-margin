# -*- coding: utf-8 -*-
"""共通: パス・設定・ログ・HTTP"""
import os
import json
import time
import logging
import datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MARGIN_DIR = os.path.join(DATA, "margin")      # 信用残 日別 YYYY/YYYYMMDD.csv.gz
PRICE_DIR = os.path.join(DATA, "prices")       # 株価 日別 YYYY/YYYYMMDD.csv
ALERT_DIR = os.path.join(DATA, "alerts")       # 通知履歴 YYYYMMDD.json
LISTED_CSV = os.path.join(DATA, "listed.csv")  # 東証上場銘柄一覧
NIKKEI_CSV = os.path.join(DATA, "nikkei225.csv")
SHARES_CSV = os.path.join(DATA, "shares.csv")  # 発行済株数
STATE_JSON = os.path.join(DATA, "state.json")  # 最終取得日時など
META_CSV = os.path.join(DATA, "margin_meta.csv")  # 申込日ごとの公表日
SPLITS_CSV = os.path.join(DATA, "splits.csv")     # 株式分割（code, 権利落ち日, 比率）
SITE_SRC = os.path.join(ROOT, "site")
SITE_OUT = os.path.join(ROOT, "_site")
SETTINGS = os.path.join(ROOT, "settings.json")

MARGIN_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/margin/01.html"
LISTED_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
NIKKEI_PAGE = "https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
JST = dt.timezone(dt.timedelta(hours=9))

log = logging.getLogger("jpx")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


def now_jst():
    return dt.datetime.now(JST)


def load_settings():
    with open(SETTINGS, encoding="utf-8-sig") as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_JSON):
        with open(STATE_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(st):
    os.makedirs(DATA, exist_ok=True)
    with open(STATE_JSON, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)


def days_since(iso):
    if not iso:
        return 9999
    try:
        t = dt.datetime.fromisoformat(iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=JST)
        return (now_jst() - t).days
    except Exception:
        return 9999


def http_get(url, binary=False, tries=3):
    import requests
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            r.raise_for_status()
            return r.content if binary else r.content.decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise last


def normalize_code(s):
    s = str(s).strip().upper()
    return s.translate(str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
                                     "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
