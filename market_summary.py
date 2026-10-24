import csv
import io
import re
from datetime import date, datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

UA = {"User-Agent": "Mozilla/5.0"}
NAVER_INDEX = {"^KS11": "KOSPI", "^KQ11": "KOSDAQ"}

# (표시이름, 영문이름, 야후 심볼, FRED 대체 코드, 값 표기, 변동 단위)
GROUPS = [
    ("주식", [
        ("코스피", "KOSPI", "^KS11", None, "{:,.2f}", "pct"),
        ("코스닥", "KOSDAQ", "^KQ11", None, "{:,.2f}", "pct"),
        ("S&P 500", "S&P 500", "^GSPC", "SP500", "{:,.2f}", "pct"),
        ("나스닥", "NASDAQ", "^IXIC", "NASDAQCOM", "{:,.2f}", "pct"),
    ]),
    ("변동성 · 환율", [
        ("VIX 공포지수", "VIX", "^VIX", "VIXCLS", "{:,.2f}", "pct"),
        ("원/달러", "USD/KRW", "KRW=X", "DEXKOUS", "{:,.1f}", "pct"),
        ("달러인덱스", "Dollar Index", "DX-Y.NYB", None, "{:,.2f}", "pct"),
    ]),
    ("원자재 · 코인", [
        ("WTI 유가", "WTI Oil", "CL=F", "DCOILWTICO", "${:,.2f}", "pct"),
        ("금", "Gold", "GC=F", None, "${:,.0f}", "pct"),
        ("구리", "Copper", "HG=F", None, "${:,.2f}", "pct"),
        ("비트코인", "Bitcoin", "BTC-USD", None, "${:,.0f}", "pct"),
    ]),
    ("금리 · 신용", [
        ("미국 10Y-2Y 금리차", "US 10Y-2Y", None, "T10Y2Y", "{:+.2f}%p", "bp"),
        ("하이일드 스프레드", "HY Spread", None, "BAMLH0A0HYM2", "{:.2f}%p", "bp"),
    ]),
]

TABLE = "border-collapse:collapse;width:100%;font-size:13px"
TH = "padding:7px 4px;background:#f1f3f4;border-bottom:2px solid #bbb;text-align:right;font-size:12px"
TH_L = TH.replace("right", "left")
TD = "padding:8px 4px;border-bottom:1px solid #e0e0e0;text-align:right"
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


def make_grid(panels):
    n = len(panels)
    if n == 0:
        return None
    cols = 2
    rows = (n + 1) // 2
    fig, axes = plt.subplots(rows, cols, figsize=(5.4, 2.05 * rows), dpi=130)
    axes = list(axes.flatten()) if n > 1 else [axes]
    for ax, (en, s, fmt, kind, cur) in zip(axes, panels):
        pts = sorted(s.items())[-22:]
        if len(pts) < 3:
            ax.axis("off")
            continue
        xs = list(range(len(pts)))
        ys = [v for _, v in pts]
        up = ys[-1] >= ys[0]
        color = "#d32f2f" if up else "#1565c0"
        ax.plot(xs, ys, color=color, linewidth=2.4)
        ax.fill_between(xs, ys, min(ys), color=color, alpha=0.12)
        ax.scatter([xs[-1]], [ys[-1]], color=color, s=30, zorder=3)
        if kind == "bp":
            chg = f"{ys[-1] - ys[0]:+.2f}%p"
        else:
            chg = f"{(ys[-1] / ys[0] - 1) * 100:+.1f}%" if ys[0] else ""
        val = fmt.format(cur).replace("$", r"\$")
        ax.set_title(
            f"{en}\n{val}   {chg}", fontsize=10, fontweight="bold",
            loc="left", color="#222",
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ("top", "right", "left", "bottom"):
            ax.spines[sp].set_visible(False)
        ax.margins(x=0.02, y=0.15)
    for ax in axes[n:]:
        ax.axis("off")
    fig.tight_layout(h_pad=1.2)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def summary_section(today, charts):
    rows = []
    panels = []
    ups = downs = 0
    for gname, items in GROUPS:
        rows.append(
            f'<tr><td colspan="5" style="padding:6px 4px;background:#eef1f5;'
            f'font-weight:bold;font-size:12px;color:#444">{gname}</td></tr>'
        )
        for label, en, sym, fred_id, fmt, kind in items:
            try:
                s = get_series(sym, fred_id, today)
                last, cur, ch = stats(s, kind)
                if ch[0] is not None and ch[0] > 0:
                    ups += 1
                elif ch[0] is not None and ch[0] < 0:
                    downs += 1
                panels.append((en, s, fmt, kind, cur))
                rows.append(
                    f'<tr><td style="{TD_L}">{label}</td>'
                    f'<td style="{TD}"><b>{fmt.format(cur)}</b><br>'
                    f'<span style="font-size:10px;color:#999">{last:%m/%d}</span></td>'
                    + "".join(chg_cell(v, kind) for v in ch)
                    + "</tr>"
                )
            except Exception as e:
                rows.append(
                    f'<tr><td style="{TD_L}">{label}</td>'
                    f'<td style="{TD};color:#d32f2f;font-size:11px" colspan="4">'
                    f"조회 실패: {str(e)[:80]}</td></tr>"
                )
    head = (
        f'<p style="font-size:14px;margin:4px 0 8px">전일 대비 '
        f'<b style="color:#d32f2f">상승 {ups}</b> · '
        f'<b style="color:#1565c0">하락 {downs}</b></p>'
        f'<table style="{TABLE}"><tr><th style="{TH_L}">지표</th>'
        f'<th style="{TH}">현재</th><th style="{TH}">전일</th>'
        f'<th style="{TH}">1주</th><th style="{TH}">1개월</th></tr>'
    )
    out = [head] + rows + ["</table>"]
    try:
        png = make_grid(panels)
    except Exception as e:
        png = None
        out.append(f'<p style="font-size:11px;color:#d32f2f">추세 그래프 오류: {e}</p>')
    if png:
        charts.append(png)
        out.append(
            '<p style="font-size:13px;font-weight:bold;margin:14px 0 4px">'
            "추세 (최근 22거래일)</p>"
            f'<img src="cid:mk{len(charts) - 1}" style="width:100%;max-width:640px">'
        )
    out.append(
        '<p style="font-size:11px;color:#888">▲상승(빨강) ▼하락(파랑), 진한 배경은 큰 변동'
        "(지수 등 ±1% 이상, 금리차·스프레드 ±5bp 이상). 그래프 위 퍼센트는 22거래일 전 대비 "
        "변동이며, 선 색은 그 기간의 방향입니다. VIX는 오르면 시장 불안이 커졌다는 뜻입니다.</p>"
    )
    return out
