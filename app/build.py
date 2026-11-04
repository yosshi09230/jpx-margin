# -*- coding: utf-8 -*-
"""指標計算とWebサイト(_site)の生成"""
import os
import glob
import json
import shutil

import pandas as pd

from common import (log, now_jst, normalize_code, MARGIN_DIR, PRICE_DIR, LISTED_CSV, NIKKEI_CSV,
                    SHARES_CSV, ALERT_DIR, SITE_SRC, SITE_OUT, META_CSV, SPLITS_CSV)

SHARE_COLS = ["b", "s", "bs", "ss", "bg", "sg"]


def load_splits():
    if not os.path.exists(SPLITS_CSV):
        return pd.DataFrame(columns=["code", "date", "ratio"])
    sp = pd.read_csv(SPLITS_CSV, dtype={"code": str, "date": str})
    sp["ratio"] = sp["ratio"].astype(float)
    return sp[sp.ratio > 0]

# 変化を計算する項目: 制度/一般/合計倍率, 制度/一般 買残・売残, 合計 買残・売残
CHG_KEYS = ["rs", "rg", "ra", "bs", "ss", "bg", "sg", "b", "s"]


def _ratio(b, s):
    return (b / s).where(s > 0).round(2)


def load_margin():
    files = sorted(glob.glob(os.path.join(MARGIN_DIR, "*", "*.csv.gz")))
    if not files:
        return pd.DataFrame()
    frames = []
    for p in files:
        d = pd.read_csv(p, dtype={"code": str})
        ymd = os.path.basename(p)[:8]
        d["date"] = "%s-%s-%s" % (ymd[:4], ymd[4:6], ymd[6:])
        frames.append(d)
    m = pd.concat(frames, ignore_index=True)
    m = m.rename(columns={"buy_std": "bs", "sell_std": "ss", "buy_gen": "bg", "sell_gen": "sg",
                          "buy": "b", "sell": "s", "buy_ratio": "br", "sell_ratio": "sr", "lend_type": "lt"})
    # 株式分割の調整: 権利落ち日より前の残高株数 × 分割比率（金額・倍率は調整不要）
    for _, sp in load_splits().iterrows():
        mask = (m.code == sp.code) & (m.date < sp.date)
        if mask.any():
            m.loc[mask, SHARE_COLS] = (m.loc[mask, SHARE_COLS].astype(float) * sp.ratio).round()
    m["rs"] = _ratio(m.bs, m.ss)
    m["rg"] = _ratio(m.bg, m.sg)
    m["ra"] = _ratio(m.b, m.s)
    return m


def load_prices():
    files = sorted(glob.glob(os.path.join(PRICE_DIR, "*", "*.csv")))
    if not files:
        return pd.DataFrame(columns=["date", "code", "close", "volume"])
    p = pd.concat([pd.read_csv(f, dtype={"code": str, "date": str}) for f in files], ignore_index=True)
    p["date"] = p.date.str.slice(0, 4) + "-" + p.date.str.slice(4, 6) + "-" + p.date.str.slice(6, 8)
    return p.sort_values(["code", "date"])


def _clean(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (bool,)):
        return v
    if isinstance(v, (int, float)) or hasattr(v, "item"):
        v = float(v)
        return int(v) if v.is_integer() else round(v, 2)
    return v


def compute(settings):
    """最新日の銘柄ごとの指標を返す: (rows[list[dict]], status, m, prices)"""
    m = load_margin()
    if m.empty:
        return [], {}, m, pd.DataFrame()
    dates = sorted(m.date.unique())
    d0 = dates[-1]
    d1 = dates[-2] if len(dates) >= 2 else None
    d5 = dates[-6] if len(dates) >= 6 else None

    listed = pd.read_csv(LISTED_CSV, dtype=str).set_index("code") if os.path.exists(LISTED_CSV) else pd.DataFrame()
    nk = set(pd.read_csv(NIKKEI_CSV, dtype=str).code) if os.path.exists(NIKKEI_CSV) else set()
    shares = dict(pd.read_csv(SHARES_CSV, dtype={"code": str})[["code", "shares"]].values) if os.path.exists(SHARES_CSV) else {}
    pr = load_prices()

    last_close, prev_close, avgvol = {}, {}, {}
    for code, g in pr.groupby("code"):
        g = g.dropna(subset=["close"])
        if g.empty:
            continue
        last_close[code] = float(g.close.iloc[-1])
        if len(g) >= 2:
            prev_close[code] = float(g.close.iloc[-2])
        v = g.volume.dropna().tail(20)
        if len(v) and v.mean() > 0:
            avgvol[code] = float(v.mean())

    cur = m[m.date == d0].set_index("code")
    p1 = m[m.date == d1].set_index("code") if d1 else None
    p5 = m[m.date == d5].set_index("code") if d5 else None
    watch = {normalize_code(w["code"]) for w in settings.get("watchlist", [])}

    def info(code, col):
        if listed.empty or code not in listed.index:
            return ""
        v = str(listed.at[code, col])
        return "" if v in ("-", "nan") else v

    rows = []
    for code, r in cur.iterrows():
        close = last_close.get(code)
        sh = shares.get(code)
        est = False
        if not sh and pd.notna(r["br"]) and r["br"] >= 1.0 and r["b"]:
            sh, est = r["b"] / (r["br"] / 100.0), True
        mc = round(close * sh / 1e8, 1) if close and sh else None
        mk = info(code, "market").replace("（内国株式）", "").replace("（外国株式）", "(外国)") \
            .replace("REIT・ベンチャーファンド・カントリーファンド・インフラファンド", "REIT等")
        rec = {"c": code, "n": info(code, "name"), "mk": mk, "s33": info(code, "sector33"),
               "sz": info(code, "size"), "nk": code in nk, "lt": r["lt"], "w": code in watch,
               "px": close, "pxc": round((close / prev_close[code] - 1) * 100, 1) if close and prev_close.get(code) else None,
               "mc": mc, "mce": bool(est and mc),
               "br": _clean(r["br"]), "sr": _clean(r["sr"]),
               "days": round(r["b"] / avgvol[code], 1) if avgvol.get(code) and pd.notna(r["b"]) else None}
        for k in CHG_KEYS:
            rec[k] = _clean(r[k])
        for suf, prev in (("1", p1), ("5", p5)):
            for k in CHG_KEYS:
                a = rec[k]
                b = _clean(prev.at[code, k]) if prev is not None and code in prev.index else None
                if k in ("rs", "rg", "ra"):
                    rec[k + "_d" + suf] = round(a - b, 2) if a is not None and b is not None else None
                rec[k + "_pct" + suf] = round((a - b) / b * 100, 1) if a is not None and b not in (None, 0) else None
        # 容量削減: 値の無い項目は省略（画面側は「―」表示）
        rows.append({k: v for k, v in rec.items() if v is not None and v is not False and v != ""})

    pub = dict(pd.read_csv(META_CSV, dtype=str).dropna().values) if os.path.exists(META_CSV) else {}
    status = {"updated": now_jst().strftime("%Y-%m-%d %H:%M"), "latest": d0, "prev": d1, "week": d5,
              "published": pub,
              "n_days": len(dates), "first": dates[0],
              "price_date": pr.date.max() if not pr.empty else None, "n_shares": len(shares)}
    return rows, status, m, pr


def build_site(rows, status, m, pr, settings, errors):
    if os.path.exists(SITE_OUT):
        shutil.rmtree(SITE_OUT)
    shutil.copytree(SITE_SRC, SITE_OUT)
    dd = os.path.join(SITE_OUT, "data")
    hd = os.path.join(dd, "hist")
    os.makedirs(hd, exist_ok=True)
    status = dict(status, errors=errors,
                  watch=[{"code": normalize_code(w["code"]), "memo": w.get("memo", "")} for w in settings.get("watchlist", [])],
                  rules={"watch": settings.get("watch_alerts", {}).get("rules", []),
                         "market": settings.get("market_alerts", {})})
    with open(os.path.join(dd, "status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False)
    with open(os.path.join(dd, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    # 通知履歴（直近30件）
    al = []
    for p in sorted(glob.glob(os.path.join(ALERT_DIR, "*.json")))[-30:]:
        with open(p, encoding="utf-8") as f:
            al.append(json.load(f))
    with open(os.path.join(dd, "alerts.json"), "w", encoding="utf-8") as f:
        json.dump(al[::-1], f, ensure_ascii=False)
    # 銘柄ごとの履歴
    if m.empty:
        return
    price_hist = {c: g for c, g in pr.dropna(subset=["close"]).groupby("code")} if not pr.empty else {}
    splits = load_splits()
    for code, g in m.sort_values("date").groupby("code"):
        h = {"d": list(g.date)}
        for k in ["rs", "rg", "ra", "bs", "ss", "bg", "sg", "b", "s"]:
            h[k] = [_clean(x) for x in g[k]]
        # 金額（億円）
        for k, col in (("vb", "val_buy"), ("vs", "val_sell"), ("vbs", "val_buy_std"), ("vss", "val_sell_std"),
                       ("vbg", "val_buy_gen"), ("vsg", "val_sell_gen")):
            h[k] = [None if pd.isna(x) else round(float(x) / 1e8, 2) for x in g[col]]
        # 残高の基準日（申込日）の終値
        if code in price_hist:
            pc = dict(zip(price_hist[code].date, price_hist[code].close))
            h["px"] = [_clean(pc.get(d)) for d in g.date]
        sp = splits[(splits.code == code) & (splits.date > g.date.iloc[0])]
        if len(sp):
            h["splits"] = [{"date": a, "ratio": float(b)} for a, b in zip(sp.date, sp.ratio)]
        with open(os.path.join(hd, "%s.json" % code), "w", encoding="utf-8") as f:
            json.dump(h, f, separators=(",", ":"))
    log.info("サイト生成: %d 銘柄（最新 %s、蓄積 %d 日）", len(rows), status.get("latest"), status.get("n_days", 0))
