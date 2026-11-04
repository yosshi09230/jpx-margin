# -*- coding: utf-8 -*-
"""データ取得: 信用残PDF / 上場銘柄一覧 / 日経225 / 株価 / 発行済株数"""
import os
import re
import io
import glob
import time
import tempfile
import datetime as dt
from urllib.parse import urljoin

import pandas as pd

import pdf_parser
from common import (log, http_get, now_jst, days_since, normalize_code,
                    MARGIN_DIR, PRICE_DIR, LISTED_CSV, NIKKEI_CSV, SHARES_CSV, META_CSV, SPLITS_CSV,
                    MARGIN_PAGE, LISTED_PAGE, NIKKEI_PAGE)

MARGIN_COLS = (["code", "lend_type", "unit"] + pdf_parser.COL_NAMES +
               ["val_" + c for c in pdf_parser.COL_NAMES if not c.endswith("ratio")])


def margin_path(ymd):
    return os.path.join(MARGIN_DIR, ymd[:4], "%s.csv.gz" % ymd)


def have_margin_dates():
    return sorted(os.path.basename(p)[:8] for p in glob.glob(os.path.join(MARGIN_DIR, "*", "*.csv.gz")))


def save_margin(pdf_path):
    as_of, rows, totals = pdf_parser.parse_pdf(pdf_path)
    if not as_of or not rows:
        raise ValueError("PDFの読み取りに失敗")
    errs = pdf_parser.validate(rows, totals)
    if errs:
        log.warning("総合計との照合で差異 %d 件: %s", len(errs), errs[:3])
    ymd = as_of.replace("-", "")
    df = pd.DataFrame(rows)[MARGIN_COLS]
    p = margin_path(ymd)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    df.to_csv(p, index=False, compression={"method": "gzip", "mtime": 0})
    pub = pdf_parser.parse_published(pdf_path)
    meta = pd.read_csv(META_CSV, dtype=str) if os.path.exists(META_CSV) else pd.DataFrame(columns=["date", "published"])
    meta = pd.concat([meta[meta.date != as_of], pd.DataFrame([{"date": as_of, "published": pub}])]).sort_values("date")
    meta.to_csv(META_CSV, index=False)
    log.info("信用残 保存 %s（公表 %s）: %d 銘柄", as_of, pub, len(df))
    return ymd


def fetch_margin():
    html = http_get(MARGIN_PAGE)
    links = sorted(set(re.findall(r'href="([^"]*?(\d{8})_mtall\.pdf)"', html)), key=lambda x: x[1])
    if not links:
        raise RuntimeError("JPXページにPDFリンクが見つかりません（ページ構成が変わった可能性）")
    have = set(have_margin_dates())
    new = []
    for href, ymd in links:
        if ymd in have:
            continue
        url = urljoin(MARGIN_PAGE, href)
        log.info("PDF取得: %s", url)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(http_get(url, binary=True))
            tmp = f.name
        try:
            new.append(save_margin(tmp))
        finally:
            os.remove(tmp)
    log.info("信用残 新規 %d 件（サイト掲載 %d 件）", len(new), len(links))
    return new


def load_listed_excel(content):
    d = pd.read_excel(io.BytesIO(content), dtype=str)
    d.columns = [str(c).strip() for c in d.columns]
    keep = {"コード": "code", "銘柄名": "name", "市場・商品区分": "market",
            "33業種区分": "sector33", "17業種区分": "sector17", "規模区分": "size"}
    for c in keep:
        if c not in d.columns:
            raise ValueError("上場銘柄一覧の列が想定と違います: %s" % c)
    d = d[list(keep)].rename(columns=keep).fillna("-")
    d["code"] = d["code"].map(normalize_code)
    for c in d.columns:
        d[c] = d[c].astype(str).str.strip()
    return d


def fetch_listed(state, cfg):
    if days_since(state.get("listed_updated")) < cfg["listed_refresh_days"] and os.path.exists(LISTED_CSV):
        return
    html = http_get(LISTED_PAGE)
    m = re.search(r'href="([^"]*data_j\.xlsx?)"', html)
    if not m:
        raise RuntimeError("上場銘柄一覧のリンクが見つかりません")
    d = load_listed_excel(http_get(urljoin(LISTED_PAGE, m.group(1)), binary=True))
    d.to_csv(LISTED_CSV, index=False)
    state["listed_updated"] = now_jst().isoformat(timespec="seconds")
    log.info("上場銘柄一覧 更新: %d 銘柄", len(d))


def fetch_nikkei(state, cfg):
    if days_since(state.get("nikkei_updated")) < cfg["nikkei_refresh_days"] and os.path.exists(NIKKEI_CSV):
        return
    html = http_get(NIKKEI_PAGE)
    codes = list(dict.fromkeys(re.findall(r"<td[^>]*>\s*([0-9][0-9A-Z]{3})\s*</td>", html)))
    if not (200 <= len(codes) <= 240) and os.path.exists(LISTED_CSV):
        known = set(pd.read_csv(LISTED_CSV, dtype=str)["code"])
        codes = [c for c in dict.fromkeys(re.findall(r">\s*([0-9][0-9A-Z]{3})\s*<", html)) if c in known]
    if not (200 <= len(codes) <= 240):
        raise RuntimeError("日経225構成銘柄数が異常 (%d)" % len(codes))
    pd.DataFrame({"code": codes}).to_csv(NIKKEI_CSV, index=False)
    state["nikkei_updated"] = now_jst().isoformat(timespec="seconds")
    log.info("日経225 更新: %d 銘柄", len(codes))


def price_files():
    return sorted(glob.glob(os.path.join(PRICE_DIR, "*", "*.csv")))


def fetch_prices(cfg, codes):
    import yfinance as yf
    first = not price_files()
    period = cfg["price_history_period"] if first else "1mo"
    tick = [c for c in codes if len(c) == 4]
    frames, splits = [], []
    for i in range(0, len(tick), 150):
        chunk = tick[i:i + 150]
        try:
            df = yf.download([c + ".T" for c in chunk], period=period, interval="1d",
                             group_by="ticker", auto_adjust=False, actions=True, progress=False, threads=True)
        except Exception as e:
            log.warning("株価取得失敗 (%d-): %s", i, e)
            continue
        if df is None or df.empty:
            continue
        for c in chunk:
            try:
                sub = df[c + ".T"] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                continue
            if "Close" not in sub:
                continue
            if "Stock Splits" in sub:
                sp = sub["Stock Splits"].fillna(0)
                for d, ratio in sp[sp > 0].items():
                    splits.append({"code": c, "date": d.strftime("%Y-%m-%d"), "ratio": float(ratio)})
            sub = sub.dropna(subset=["Close"])
            if sub.empty:
                continue
            frames.append(pd.DataFrame({"date": sub.index.strftime("%Y%m%d"), "code": c,
                                        "close": sub["Close"].round(2).values,
                                        "volume": sub["Volume"].values if "Volume" in sub else None}))
        time.sleep(1)
    if not frames:
        raise RuntimeError("株価を1件も取得できませんでした")
    allp = pd.concat(frames)
    n = 0
    for ymd, g in allp.groupby("date"):
        p = os.path.join(PRICE_DIR, ymd[:4], "%s.csv" % ymd)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            old = pd.read_csv(p, dtype={"code": str, "date": str})
            g = pd.concat([old[~old.code.isin(g.code)], g])
        g.sort_values("code")[["date", "code", "close", "volume"]].to_csv(p, index=False)
        n += 1
    log.info("株価: %d 日分を保存（%d 銘柄）", n, allp.code.nunique())
    if splits:
        cur = pd.read_csv(SPLITS_CSV, dtype=str) if os.path.exists(SPLITS_CSV) else pd.DataFrame(columns=["code", "date", "ratio"])
        new = pd.DataFrame(splits).astype(str)
        allsp = pd.concat([cur, new]).drop_duplicates(["code", "date"], keep="last").sort_values(["date", "code"])
        allsp.to_csv(SPLITS_CSV, index=False)
        log.info("株式分割: %d 件を記録（合計 %d 件）", len(new), len(allsp))


def fetch_shares(cfg, codes, watch):
    import yfinance as yf
    listed = pd.read_csv(LISTED_CSV, dtype=str) if os.path.exists(LISTED_CSV) else pd.DataFrame(columns=["code", "market"])
    market = dict(zip(listed.code, listed.market))
    stocks = [c for c in codes if len(c) == 4 and "株式" in str(market.get(c, ""))]
    cur = pd.read_csv(SHARES_CSV, dtype={"code": str}) if os.path.exists(SHARES_CSV) else pd.DataFrame(columns=["code", "shares", "updated"])
    upd = dict(zip(cur.code, cur.updated))
    stale = [c for c in stocks if days_since(upd.get(c)) >= cfg["shares_refresh_days"]]
    stale.sort(key=lambda c: (c not in watch, c in upd, str(upd.get(c) or "")))
    stale = stale[:int(cfg["shares_refresh_per_day"])]
    got, fails = {}, 0
    for c in stale:
        try:
            sh = yf.Ticker(c + ".T").fast_info["shares"]
            if sh and sh > 0:
                got[c] = float(sh)
        except Exception:
            fails += 1
            if fails >= 30 and not got:
                log.warning("発行済株数の取得が連続失敗のため中断")
                break
        time.sleep(0.2)
    if got:
        ts = now_jst().isoformat(timespec="seconds")
        new = pd.DataFrame({"code": list(got), "shares": list(got.values()), "updated": ts})
        cur = pd.concat([cur[~cur.code.isin(new.code)], new]).sort_values("code")
        cur.to_csv(SHARES_CSV, index=False)
    log.info("発行済株数: %d/%d 件更新（取得済 合計 %d / 対象 %d）", len(got), len(stale), len(cur), len(stocks))
