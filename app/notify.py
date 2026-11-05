# -*- coding: utf-8 -*-
"""通知: settings.json の条件で判定し、ntfy でiPhoneへ送る"""
import os
import json

from common import log, ALERT_DIR, now_jst

SITE_URL = "https://yosshi09230.github.io/jpx-margin/"
# 指標 → 足切りに使う残高
BAL_KEY = {"rs": "ss", "rg": "sg", "ra": "s", "bs": "bs", "ss": "ss", "bg": "bg", "sg": "sg", "b": "b", "s": "s"}
LABEL = {"rs": "制度倍率", "rg": "一般倍率", "ra": "合計倍率", "bs": "制度買残", "ss": "制度売残",
         "bg": "一般買残", "sg": "一般売残", "b": "買残", "s": "売残"}


def _hit(v, rule):
    if v is None:
        return False
    if "abs_gte" in rule and abs(v) >= rule["abs_gte"]:
        return True
    if "gte" in rule and v >= rule["gte"]:
        return True
    if "lte" in rule and v <= rule["lte"]:
        return True
    return False


def evaluate(rows, settings):
    """戻り値: (ウォッチ該当, 買残上位該当)"""
    watch_rules = settings.get("watch_alerts", {}).get("rules", [])
    top_rules = settings.get("top_buy_alerts", {}).get("rules", [])
    out_w, out_t = [], []
    for r in rows:
        if r.get("w"):
            for rule in watch_rules:
                v = r.get(rule["metric"])
                if _hit(v, rule):
                    out_w.append(_item(r, rule, v))
        if r.get("tb"):
            for rule in top_rules:
                v = r.get(rule["metric"])
                if _hit(v, rule):
                    out_t.append(_item(r, rule, v))
    out_w.sort(key=lambda x: -abs(x["value"]))
    out_t.sort(key=lambda x: x.get("tb") or 999)
    return out_w, out_t


def _item(r, rule, v):
    return {"code": r["c"], "name": r.get("n", ""), "rule": rule["name"], "metric": rule["metric"],
            "value": v, "rs": r.get("rs"), "ra": r.get("ra"), "px": r.get("px"), "pxc": r.get("pxc"),
            "tb": r.get("tb"), "vb": r.get("vb")}


def _line(it):
    base = it["metric"].split("_")[0]
    span = "前日比" if it["metric"].endswith("1") else "前週比"
    s = "%s %s %s %+.1f%%" % (it["code"], it["name"][:10], LABEL.get(base, base), it["value"])
    s += "（%s）" % span
    if it.get("tb"):
        s = "%d位 " % it["tb"] + s
    if it.get("ra") is not None and base not in ("rs", "rg", "ra"):
        s += " 倍率%.1f" % it["ra"]
    return s


def run(rows, status, settings):
    latest = status.get("latest")
    if not latest:
        return
    os.makedirs(ALERT_DIR, exist_ok=True)
    path = os.path.join(ALERT_DIR, "%s.json" % latest.replace("-", ""))
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if json.load(f).get("sent"):
                log.info("通知: %s 分は送信済み", latest)
                return
    w, m = evaluate(rows, settings)
    rec = {"date": latest, "created": now_jst().strftime("%Y-%m-%d %H:%M"), "watch": w, "top": m, "sent": False}
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    cfg = settings.get("notify", {})
    if (w or m) and topic and cfg.get("enabled", True):
        n = cfg.get("max_items_per_message", 15)
        lines = []
        if w:
            lines.append("【ウォッチ】")
            lines += [_line(x) for x in w[:n]]
        if m:
            lines.append("【買残上位%d】" % settings.get("top_buy_alerts", {}).get("top_n", 30))
            lines += [_line(x) for x in m[:n]]
            if len(m) > n:
                lines.append("…ほか %d 件（アプリで確認）" % (len(m) - n))
        d = latest[5:].replace("-", "/")
        title = "信用残 %s分 ウォッチ%d件・買残上位%d件" % (d, len(w), len(m))
        try:
            import requests
            r = requests.post("https://ntfy.sh/", json={
                "topic": topic, "title": title, "message": "\n".join(lines),
                "click": SITE_URL + "#alerts", "tags": ["chart_with_upwards_trend"]}, timeout=30)
            r.raise_for_status()
            rec["sent"] = True
            log.info("通知送信: %s", title)
        except Exception as e:
            log.error("通知の送信に失敗: %s", e)
    else:
        rec["sent"] = bool(topic)  # 該当なし → 送信不要として完了扱い
        log.info("通知: 該当 ウォッチ%d件・買残上位%d件%s", len(w), len(m), "" if topic else "（NTFY_TOPIC未設定のため送信なし）")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
