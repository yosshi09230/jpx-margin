# -*- coding: utf-8 -*-
"""JPX「銘柄別信用取引残高」PDF (YYYYMMDD_mtall.pdf) の読み取り"""
import re
import pdfplumber

# 数値14列の右端x座標（A4横 842pt 基準）
COL_RIGHT = [292, 333, 364, 405, 446, 477, 518, 559, 601, 642, 683, 725, 766, 807]
COL_NAMES = [
    "sell", "sell_chg", "sell_ratio", "buy", "buy_chg", "buy_ratio",
    "sell_gen", "sell_gen_chg", "sell_std", "sell_std_chg",
    "buy_gen", "buy_gen_chg", "buy_std", "buy_std_chg",
]
CODE_X = (180, 198)
TYPE_X = (154, 162)
NUM_X0 = 250
TYPE_MAP = {"貸": "貸借", "制": "制度信用", "他": "その他"}
UNIT_MAP = {"A": 1, "J": 10, "K": 50, "B": 100, "M": 200, "C": 500, "T": 1000, "F": 3000}


def _col_index(x_center, scale):
    prev = NUM_X0 * scale
    for i, r in enumerate(COL_RIGHT):
        if prev <= x_center <= r * scale + 2:
            return i
        prev = r * scale + 2
    return None


def _to_num(s):
    s = s.replace(" ", "").replace(",", "")
    if s in ("", "*", "-", "－"):
        return None
    neg = s.startswith("▲")
    s = s.lstrip("▲")
    if s.endswith("%"):
        try:
            v = float(s[:-1])
        except ValueError:
            return None
        return -v if neg else v
    try:
        v = int(s)
    except ValueError:
        try:
            v = float(s)
        except ValueError:
            return None
    return -v if neg else v


def _parse_numbers(row_chars, scale):
    cols = [[] for _ in COL_RIGHT]
    for c in row_chars:
        if c["x0"] < NUM_X0 * scale or not re.match(r"[0-9,.%▲*\-]", c["text"]):
            continue
        i = _col_index((c["x0"] + c["x1"]) / 2, scale)
        if i is not None:
            cols[i].append(c)
    out = {}
    for name, cs in zip(COL_NAMES, cols):
        cs.sort(key=lambda c: c["x0"])
        out[name] = _to_num("".join(c["text"] for c in cs))
    return out


def parse_pdf(path):
    """戻り値: (as_of 'YYYY-MM-DD', rows[list[dict]], totals dict or None)"""
    rows, totals, as_of = [], None, None
    pending = None  # 株数行（ページをまたぐ場合あり）
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            scale = page.width / 842.0
            if as_of is None:
                t = page.extract_text() or ""
                m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})\s*申込み現在", t)
                if m:
                    as_of = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
            # 行ごとにまとめる
            lines = {}
            for c in page.chars:
                key = round(c["top"] * 2) / 2
                lines.setdefault(key, []).append(c)
            keys = sorted(lines)
            merged, cur, last = [], [], None
            for k in keys:
                if last is not None and k - last > 1.5:
                    merged.append(cur)
                    cur = []
                cur.extend(lines[k])
                last = k
            if cur:
                merged.append(cur)

            for lc in merged:
                text = "".join(c["text"] for c in lc)
                is_shs = "株数" in text and "Shs" in text
                is_val = "金額" in text and "Val" in text
                if not (is_shs or is_val):
                    continue
                code_chars = [c for c in lc if CODE_X[0] * scale <= c["x0"] < CODE_X[1] * scale
                              and re.match(r"[0-9A-Z]", c["text"])]
                code = "".join(c["text"] for c in sorted(code_chars, key=lambda c: c["x0"]))
                nums = _parse_numbers(lc, scale)
                if is_shs:
                    lend = None
                    for c in lc:
                        if TYPE_X[0] * scale <= c["x0"] < TYPE_X[1] * scale and c["text"] in TYPE_MAP:
                            lend = TYPE_MAP[c["text"]]
                    pending = {"code5": code, "unit": UNIT_MAP.get(min(lc, key=lambda c: c["x0"])["text"]),
                               "lend": lend, "shs": nums, "text": text}
                else:
                    if pending is None:
                        continue
                    c5 = pending["code5"] if len(pending["code5"]) == 5 else code
                    if "総合計" in pending["text"]:
                        totals = {"shs": pending["shs"], "val": nums}
                    elif "小計" in pending["text"] or len(c5) != 5:
                        pass
                    else:
                        rec = {"code": c5[:4] if c5.endswith("0") else c5, "lend_type": pending["lend"], "unit": pending["unit"]}
                        for k, v in pending["shs"].items():
                            rec[k] = v
                        for k, v in nums.items():
                            if not k.endswith("ratio"):
                                rec["val_" + k] = v
                        rows.append(rec)
                    pending = None
    return as_of, rows, totals


def validate(rows, totals):
    """総合計行と照合。問題なければ空リスト"""
    errs = []
    if not totals:
        return ["総合計行が見つかりません"]
    for k in ["sell", "buy", "sell_gen", "sell_std", "buy_gen", "buy_std"]:
        s = sum((r.get(k) or 0) for r in rows)
        t = totals["shs"].get(k)
        if t is not None and s != t:
            errs.append("%s: 合計 %s != 総合計 %s" % (k, s, t))
    for r in rows:
        if r["sell"] is None or r["buy"] is None:
            errs.append("欠損: %s" % r["code"])
            continue
        if (r.get("sell_gen") or 0) + (r.get("sell_std") or 0) != r["sell"]:
            errs.append("売内訳不一致: %s" % r["code"])
        if (r.get("buy_gen") or 0) + (r.get("buy_std") or 0) != r["buy"]:
            errs.append("買内訳不一致: %s" % r["code"])
    return errs


def parse_published(path):
    """PDF見出しの公表日 'YYYY-MM-DD'（例: '2026/10/30申込み現在 ... 2026/11/2'）"""
    with pdfplumber.open(path) as pdf:
        t = pdf.pages[0].extract_text() or ""
    for line in t.split("\n"):
        if "申込み現在" in line:
            m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})\s*$", line.strip())
            if m:
                return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    return None
