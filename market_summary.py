import csv
import io
import re
from datetime import date, datetime, timedelta, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0"}
NAVER_INDEX = {"^KS11": "KOSPI", "^KQ11": "KOSDAQ"}
BLOCKS = "▁▂▃▄▅▆▇█"

# (표시이름, 야후 심볼, FRED 대체 코드, 값 표기, 변동 단위: pct 또는 bp)
GROUPS = [
    ("주식", [
        ("코스피", "^KS11", None, "{:,.2f}", "pct"),
        ("코스닥", "^KQ11", None, "{:,.2f}", "pct"),
        ("S&P 500", "^GSPC", "SP500", "{:,.2f}", "pct"),
        ("나스닥", "^IXIC", "NASDAQCOM", "{:,.2f}", "pct"),
    ]),
    ("변동성 · 환율", [
        ("VIX 공포지수", "^VIX", "VIXCLS", "{:,.2f}", "pct"),
        ("원/달러", "KRW=X", "DEXKOUS", "{:,.1f}", "pct"),
        ("달러인덱스", "DX-Y.NYB", None, "{:,.2f}", "pct"),
    ]),
    ("원자재 · 코인", [
        ("WTI 유가", "CL=F", "DCOILWTICO", "${:,.2f}", "pct"),
        ("금", "GC=F", None, "${:,.0f}", "pct"),
        ("구리", "HG=F", None, "${:,.2f}", "pct"),
        ("비트코인", "BTC-USD", None, "${:,.0f}", "pct"),
    ]),
    ("금리 · 신용", [
        ("미국 10Y-2Y 금리차", None, "T10Y2Y", "{:+.2f}%p", "bp"),
        ("하이일드 스프레드", None, "BAMLH0A0HYM2", "{:.2f}%p", "bp"),
    ]),
]

TABLE = "border-collapse:collapse;width:100%;font-size:11px"
TH = "padding:5px 3px;background:#f1f3f4;border-bottom:2px solid #bbb;text-align:right"
TH_L = TH.replace("right", "left")
TD = "padding:5px 3px;border-bottom:1px solid #e0e0e0;text-align:right"
TD_L = TD.replace("right", "left")


def yahoo(sym):
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
        params={"range": "3mo", "interval": "1d"},
        headers=UA,
        timeout=60,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    out = {}
    for ts, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
        if c is not None:
            out[datetime.fromtimestamp(ts, tz=timezone.utc).date()] = float(c)
    if not out:
        raise RuntimeError("빈 데이터")
    return out


def naver_index(code, start, end):
    r = requests.get(
        "https://api.finance.naver.com/siseJson.naver",
        params={
            "symbol": code,
            "requestType": "1",
            "startTime": start.strftime("%Y%m%d"),
            "endTime": end.strftime("%Y%m%d"),
            "timeframe": "day",
        },
        headers=UA,
        timeout=60,
    )
    r.raise_for_status()
    text = r.content.decode("utf-8", errors="ignore")
    rows = re.findall(
        r'\[\s*"?(\d{8})"?\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)',
        text,
    )
    out = {datetime.strptime(x[0], "%Y%m%d").date(): float(x[4]) for x in rows}
    if not out:
        raise RuntimeError("빈 데이터")
    return out


def fred(series_id, start):
    r = requests.get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv",
        params={"id": series_id, "cosd": start.isoformat()},
        timeout=60,
    )
    r.raise_for_status()
    out = {}
    for row in list(csv.reader(io.StringIO(r.text)))[1:]:
        if len(row) < 2:
            continue
        try:
            out[date.fromisoformat(row[0])] = float(row[1])
        except ValueError:
            continue
    if not out:
        raise RuntimeError("빈 데이터")
    return out


def get_series(sym, fred_id, today):
    start = today - timedelta(days=120)
    errs = []
    if sym:
        try:
            return yahoo(sym)
        except Exception as e:
            errs.append(f"Yahoo: {e}")
        if sym in NAVER_INDEX:
            try:
                return naver_index(NAVER_INDEX[sym], start, today)
            except Exception as e:
                errs.append(f"Naver: {e}")
    if fred_id:
        try:
            return fred(fred_id, start)
        except Exception as e:
            errs.append(f"FRED: {e}")
    raise RuntimeError(" / ".join(errs) or "데이터 없음")


def base_value(s, days):
    last = max(s)
    if days == 1:
        prev = [d for d in s if d < last]
        return s[max(prev)] if prev else None
    cand = [d for d in s if d <= last - timedelta(days=days)]
    return s[max(cand)] if cand else None


def stats(s, kind):
    last = max(s)
    cur = s[last]
    vals = []
    for days in (1, 7, 30):
        base = base_value(s, days)
        if base is None:
            vals.append(None)
        elif kind == "bp":
            vals.append((cur - base) * 100)
        else:
            vals.append(None if not base else (cur / base - 1) * 100)
    return last, cur, vals


def sparkline(s, n=22):
    pts = [v for _, v in sorted(s.items())][-n:]
    if len(pts) < 3:
        return ""
    lo, hi = min(pts), max(pts)
    if hi == lo:
        chars = BLOCKS[3] * len(pts)
    else:
        chars = "".join(
            BLOCKS[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in pts
        )
    color = "#d32f2f" if pts[-1] >= pts[0] else "#1565c0"
    return f'<span style="color:{color};font-size:13px;letter-spacing:0">{chars}</span>'


def chg_cell(v, kind):
    if v is None:
        return f'<td style="{TD};color:#999">-</td>'
    if kind == "bp":
        zero, strong, txt = abs(v) < 0.05, abs(v) >= 5, f"{abs(v):.1f}bp"
    else:
        zero, strong, txt = abs(v) < 0.005, abs(v) >= 1.0, f"{abs(v):.2f}%"
    if zero:
        return f'<td style="{TD};color:#777">0</td>'
    up = v > 0
    color = "#d32f2f" if up else "#1565c0"
    bg = ("background:#fdecea;" if up else "background:#e8f0fe;") if strong else ""
    weight = "font-weight:bold;" if strong else ""
    return (
        f'<td style="{TD};color:{color};{bg}{weight}">'
        f'{"▲" if up else "▼"}{txt}</td>'
    )


def summary_section(today, charts):
    rows = []
    ups = downs = 0
    for gname, items in GROUPS:
        rows.append(
            f'<tr><td colspan="6" style="padding:5px 4px;background:#eef1f5;'
            f'font-weight:bold;font-size:11px;color:#444">{gname}</td></tr>'
        )
        for label, sym, fred_id, fmt, kind in items:
            try:
                s = get_series(sym, fred_id, today)
                last, cur, ch = stats(s, kind)
                if ch[0] is not None and ch[0] > 0:
                    ups += 1
                elif ch[0] is not None and ch[0] < 0:
                    downs += 1
                rows.append(
                    f'<tr><td style="{TD_L}">{label}</td>'
                    f'<td style="{TD}"><b>{fmt.format(cur)}</b><br>'
                    f'<span style="font-size:9px;color:#999">{last:%m/%d}</span></td>'
                    + "".join(chg_cell(v, kind) for v in ch)
                    + f'<td style="{TD_L}">{sparkline(s)}</td></tr>'
                )
            except Exception as e:
                rows.append(
                    f'<tr><td style="{TD_L}">{label}</td>'
                    f'<td style="{TD};color:#d32f2f;font-size:10px" colspan="5">'
                    f"조회 실패: {str(e)[:80]}</td></tr>"
                )
    head = (
        f'<p style="font-size:13px;margin:4px 0 8px">전일 대비 '
        f'<b style="color:#d32f2f">상승 {ups}</b> · '
        f'<b style="color:#1565c0">하락 {downs}</b></p>'
        f'<table style="{TABLE}"><tr><th style="{TH_L}">지표</th>'
        f'<th style="{TH}">현재</th><th style="{TH}">전일</th>'
        f'<th style="{TH}">1주</th><th style="{TH}">1개월</th>'
        f'<th style="{TH_L}">추세(22일)</th></tr>'
    )
    foot = (
        '<p style="font-size:10px;color:#888">▲상승(빨강) ▼하락(파랑), 진한 배경은 큰 변동'
        "(지수 등 ±1% 이상, 금리차·스프레드 ±5bp 이상). 추세 막대는 최근 22거래일의 "
        "최저~최고 범위 안에서 위치를 보여줍니다. 환율·원자재·비트코인은 조회 시점의 "
        "최근 값이고, 금리차·스프레드는 FRED 기준 전 영업일입니다. "
        "VIX는 오르면 시장 불안이 커졌다는 뜻입니다.</p>"
    )
    return [head] + rows + ["</table>", foot]
