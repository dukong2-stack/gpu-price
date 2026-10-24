import csv
import io
import json
import os
import re
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests

try:
    from market_summary import summary_section
except Exception:
    summary_section = None

KST = timezone(timedelta(hours=9))
LOOKBACK = 140
CHART_DAYS = 90
UA = {"User-Agent": "Mozilla/5.0"}

GROUPS = [
    ("미국채 10년물 ETF", [
        ("KODEX 미국10년국채액티브(H)", "kr", ["KODEX미국10년국채액티브"]),
        ("ACE 미국10년국채액티브", "kr", ["ACE미국10년국채액티브"]),
        ("IEF (미국 7~10년 국채, 달러)", "us", "IEF"),
    ]),
    ("미국채 30년물 ETF", [
        ("KODEX 미국30년국채액티브(H)", "kr", ["KODEX미국30년국채액티브(H)"]),
        ("ACE 미국30년국채액티브(H)", "kr", ["ACE미국30년국채액티브(H)"]),
        ("TIGER 미국30년국채스트립액티브", "kr",
         ["TIGER미국30년국채스트립액티브", "TIGER미국채30년스트립액티브"]),
        ("TLT (미국 20년+ 국채, 달러)", "us", "TLT"),
    ]),
    ("국고채 10년물 ETF", [
        ("ACE 국고채10년", "kr", ["ACE국고채10년"]),
    ]),
    ("국고채 30년물 ETF", [
        ("KODEX 국고채30년액티브", "kr", ["KODEX국고채30년액티브"]),
        ("KBSTAR 국고채30년", "kr", ["KBSTAR국고채30년"]),
    ]),
]

TABLE = "border-collapse:collapse;width:100%;font-size:12px"
TH = "padding:6px 4px;background:#f1f3f4;border-bottom:2px solid #bbb;text-align:right"
TH_L = TH.replace("right", "left")
TD = "padding:6px 4px;border-bottom:1px solid #e0e0e0;text-align:right"
TD_L = TD.replace("right", "left")


# ---------- 공통 ----------
def add_chart(charts, png):
    charts.append(png)
    return (
        f'<img src="cid:mk{len(charts) - 1}" '
        'style="width:100%;max-width:640px;margin:6px 0">'
    )


def cell_bp(v):
    if v is None:
        return f'<td style="{TD};color:#999">-</td>'
    if abs(v) < 0.05:
        return f'<td style="{TD};color:#777">변동없음</td>'
    arrow, color = ("▲", "#d32f2f") if v > 0 else ("▼", "#1565c0")
    return f'<td style="{TD};color:{color}">{arrow}{abs(v):.1f}bp</td>'


def cell_pct(v):
    if v is None:
        return f'<td style="{TD};color:#999">-</td>'
    if abs(v) < 0.005:
        return f'<td style="{TD};color:#777">0.00%</td>'
    arrow, color = ("▲", "#d32f2f") if v > 0 else ("▼", "#1565c0")
    return f'<td style="{TD};color:{color}">{arrow}{abs(v):.2f}%</td>'


def base_value(s, days):
    last = max(s)
    if days == 1:
        prev = [d for d in s if d < last]
        return s[max(prev)] if prev else None
    cand = [d for d in s if d <= last - timedelta(days=days)]
    return s[max(cand)] if cand else None


# ---------- 국채금리 ----------
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
        raise RuntimeError(f"FRED {series_id} 데이터 없음")
    return out


def ecos_codes(key):
    for stat in ("060Y001", "817Y002"):
        r = requests.get(
            f"https://ecos.bok.or.kr/api/StatisticItemList/{key}/json/kr/1/200/{stat}/",
            timeout=60,
        )
        try:
            rows = r.json()["StatisticItemList"]["row"]
        except Exception:
            continue
        found = {}
        for x in rows:
            nm = x["ITEM_NAME"].replace(" ", "")
            if "국고채" not in nm:
                continue
            if "(10년" in nm and 10 not in found:
                found[10] = x["ITEM_CODE"]
            if "(30년" in nm and 30 not in found:
                found[30] = x["ITEM_CODE"]
        if len(found) == 2:
            return stat, found
    raise RuntimeError("ECOS에서 국고채 10년/30년 항목을 찾지 못함 (키 확인 필요)")


def ecos_series(key, stat, item, start, end):
    last = None
    for cycle in ("D", "DD"):
        url = (
            f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/1000/"
            f"{stat}/{cycle}/{start:%Y%m%d}/{end:%Y%m%d}/{item}/"
        )
        last = requests.get(url, timeout=60).json()
        rows = last.get("StatisticSearch", {}).get("row")
        if rows:
            out = {}
            for x in rows:
                try:
                    out[datetime.strptime(x["TIME"], "%Y%m%d").date()] = float(
                        x["DATA_VALUE"]
                    )
                except (ValueError, KeyError):
                    continue
            return out
    raise RuntimeError(f"ECOS 데이터 없음: {str(last)[:120]}")


def yield_changes(s):
    last = max(s)
    cur = s[last]
    out = []
    for days in (1, 7, 30):
        base = base_value(s, days)
        out.append(None if base is None else (cur - base) * 100)
    return last, cur, out


def make_yield_chart(title, series_list, today):
    cutoff = today - timedelta(days=CHART_DAYS)
    fig, ax = plt.subplots(figsize=(7, 3.4), dpi=110)
    drawn = 0
    for label, s in series_list:
        pts = sorted((d, v) for d, v in s.items() if d >= cutoff)
        if len(pts) >= 2:
            ax.plot(
                [p[0] for p in pts], [p[1] for p in pts],
                linewidth=1.6, label=label,
            )
            drawn += 1
    if not drawn:
        plt.close(fig)
        return None
    ax.set_title(title, fontsize=11)
    ax.set_ylabel("yield (%)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def yield_section(today, charts):
    start = today - timedelta(days=LOOKBACK)
    data = {}
    errors = []
    for label, sid in (("미국 10년", "DGS10"), ("미국 30년", "DGS30")):
        try:
            data[label] = fred(sid, start)
        except Exception as e:
            errors.append(f"{label}: {e}")
    try:
        key = os.environ["ECOS_API_KEY"].strip()
        stat, codes = ecos_codes(key)
        for yrs in (10, 30):
            try:
                data[f"한국 {yrs}년"] = ecos_series(key, stat, codes[yrs], start, today)
            except Exception as e:
                errors.append(f"한국 {yrs}년: {e}")
    except Exception as e:
        errors.append(f"한국: {e}")

    h = [
        '<p style="font-size:12px;color:#666">금리 %, 변동 bp(1bp=0.01%p) · '
        '<span style="color:#d32f2f">▲금리상승</span> '
        '<span style="color:#1565c0">▼금리하락</span></p>',
        f'<table style="{TABLE}"><tr><th style="{TH_L}">국채</th>'
        f'<th style="{TH}">기준일</th><th style="{TH}">금리</th>'
        f'<th style="{TH}">전일</th><th style="{TH}">전주</th>'
        f'<th style="{TH}">전월</th></tr>',
    ]
    for label in ("미국 10년", "미국 30년", "한국 10년", "한국 30년"):
        s = data.get(label)
        if not s:
            h.append(
                f'<tr><td style="{TD_L}">{label}</td>'
                f'<td style="{TD};color:#999" colspan="5">조회 실패</td></tr>'
            )
            continue
        last, cur, ch = yield_changes(s)
        h.append(
            f'<tr><td style="{TD_L}">{label}</td>'
            f'<td style="{TD}">{last:%m/%d}</td>'
            f'<td style="{TD}"><b>{cur:.3f}</b></td>'
            + "".join(cell_bp(v) for v in ch)
            + "</tr>"
        )
    h.append("</table>")
    for e in errors:
        h.append(f'<p style="font-size:11px;color:#d32f2f">오류: {e}</p>')
    for title, keys in (
        ("US Treasury yield (%)", ("미국 10년", "미국 30년")),
        ("Korea Treasury yield (%)", ("한국 10년", "한국 30년")),
    ):
        png = make_yield_chart(title, [(k, data[k]) for k in keys if k in data], today)
        if png:
            h.append(add_chart(charts, png))
    h.append(
        '<p style="font-size:11px;color:#888">출처: 미국 FRED(DGS10, DGS30), '
        "한국은행 ECOS. 통상 전 영업일 기준이며 휴장일에는 직전 값이 표시됩니다.</p>"
    )
    return h


# ---------- 채권 ETF ----------
def load_kr_list():
    r = requests.get(
        "https://finance.naver.com/api/sise/etfItemList.nhn",
        params={"etfType": "0", "targetColumn": "market_sum", "sortOrder": "desc"},
        headers=UA,
        timeout=60,
    )
    r.raise_for_status()
    try:
        text = r.content.decode("utf-8")
    except UnicodeDecodeError:
        text = r.content.decode("cp949", errors="ignore")
    return json.loads(text)["result"]["etfItemList"]


def find_kr(items, patterns):
    for p in patterns:
        best = None
        for it in items:
            nm = it["itemname"].replace(" ", "").upper()
            if p.upper() in nm:
                if best is None or it.get("marketSum", 0) > best.get("marketSum", 0):
                    best = it
        if best:
            return best["itemcode"], best["itemname"]
    return None


def kr_series(code, start, end):
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
    out = {}
    for row in rows:
        out[datetime.strptime(row[0], "%Y%m%d").date()] = float(row[4])
    if not out:
        raise RuntimeError("가격 데이터 없음")
    return out


def us_series(sym):
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
            params={"range": "6mo", "interval": "1d"},
            headers=UA,
            timeout=60,
        )
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        closes = res["indicators"]["quote"][0]["close"]
        out = {}
        for ts, c in zip(res["timestamp"], closes):
            if c is not None:
                out[datetime.fromtimestamp(ts, tz=timezone.utc).date()] = float(c)
        if out:
            return out
    except Exception:
        pass
    r = requests.get(
        "https://stooq.com/q/d/l/",
        params={"s": f"{sym.lower()}.us", "i": "d"},
        headers=UA,
        timeout=60,
    )
    r.raise_for_status()
    out = {}
    for row in list(csv.reader(io.StringIO(r.text)))[1:]:
        try:
            out[date.fromisoformat(row[0])] = float(row[4])
        except (ValueError, IndexError):
            continue
    if not out:
        raise RuntimeError("가격 데이터 없음 (Yahoo/Stooq 모두 실패)")
    return out


def pct_changes(s):
    last = max(s)
    cur = s[last]
    out = []
    for days in (1, 7, 30):
        base = base_value(s, days)
        out.append(None if not base else (cur / base - 1) * 100)
    return last, cur, out


def make_etf_chart(title, items, today):
    cutoff = today - timedelta(days=CHART_DAYS)
    fig, ax = plt.subplots(figsize=(7, 3.4), dpi=110)
    drawn = 0
    for label, s in items:
        pts = sorted((d, v) for d, v in s.items() if d >= cutoff)
        if len(pts) >= 2 and pts[0][1]:
            base = pts[0][1]
            ax.plot(
                [p[0] for p in pts], [p[1] / base * 100 for p in pts],
                linewidth=1.5, label=label,
            )
            drawn += 1
    if not drawn:
        plt.close(fig)
        return None
    ax.set_title(title + " (rebased = 100)", fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=6, loc="best")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def etf_section(today, charts):
    start = today - timedelta(days=120)
    errors = []
    try:
        kr_items = load_kr_list()
    except Exception as e:
        kr_items = []
        errors.append(f"네이버 ETF 목록 조회 실패: {e}")

    h = [
        '<p style="font-size:12px;color:#666">종가 기준 · 변동은 전일/전주/전월 대비 가격 등락률 · '
        '<span style="color:#d32f2f">▲상승</span> '
        '<span style="color:#1565c0">▼하락</span></p>'
    ]
    used = set()
    for gname, entries in GROUPS:
        h.append(
            f'<h4 style="margin:12px 0 4px">{gname}</h4>'
            f'<table style="{TABLE}"><tr><th style="{TH_L}">종목</th>'
            f'<th style="{TH}">기준일</th><th style="{TH}">종가</th>'
            f'<th style="{TH}">전일</th><th style="{TH}">전주</th>'
            f'<th style="{TH}">전월</th></tr>'
        )
        chart_items = []
        for label, kind, ident in entries:
            try:
                if kind == "kr":
                    found = find_kr(kr_items, ident)
                    if not found:
                        raise RuntimeError("종목 찾지 못함")
                    code, name = found
                    if code in used:
                        continue
                    used.add(code)
                    s = kr_series(code, start, today)
                    label = name
                else:
                    s = us_series(ident)
                last, cur, ch = pct_changes(s)
                price = f"{cur:,.0f}원" if kind == "kr" else f"${cur:,.2f}"
                h.append(
                    f'<tr><td style="{TD_L}">{label}</td>'
                    f'<td style="{TD}">{last:%m/%d}</td>'
                    f'<td style="{TD}"><b>{price}</b></td>'
                    + "".join(cell_pct(v) for v in ch)
                    + "</tr>"
                )
                chart_items.append((label, s))
            except Exception as e:
                h.append(
                    f'<tr><td style="{TD_L}">{label}</td>'
                    f'<td style="{TD};color:#d32f2f" colspan="5">조회 실패: {e}</td></tr>'
                )
        h.append("</table>")
        png = make_etf_chart(gname, chart_items, today)
        if png:
            h.append(add_chart(charts, png))
    for e in errors:
        h.append(f'<p style="font-size:11px;color:#d32f2f">오류: {e}</p>')
    h.append(
        '<p style="font-size:11px;color:#888">(H)는 환헤지 상품입니다. IEF/TLT는 해외 상장 달러 상품이며 '
        "TLT는 만기 20년 이상 국채라 30년물 대용입니다. 그래프는 90일 전 가격을 100으로 맞춘 비교 그래프입니다. "
        "출처: 네이버 금융, Yahoo Finance.</p>"
    )
    return h


# ---------- 메일 ----------
def main():
    today = datetime.now(KST).date()
    charts = []
    parts = [
        '<div style="font-family:-apple-system,\'Malgun Gothic\',sans-serif;max-width:680px">',
        f"<h2>국채금리 · 채권ETF · {today}</h2>",
    ]
    sections = [("1. 국채금리", yield_section), ("2. 채권 ETF 시세", etf_section)]
    if summary_section:
        sections.insert(0, ("시장 한눈에", summary_section))
    for title, fn in sections:
        parts.append(
            f'<h3 style="margin:20px 0 6px;padding-top:10px;border-top:2px solid #444">{title}</h3>'
        )
        try:
            parts.extend(fn(today, charts))
        except Exception as e:
            parts.append(f'<p style="color:#d32f2f">조회 실패: {e}</p>')
    parts.append("</div>")

    user = os.environ["SMTP_USER"].strip()
    pw = os.environ["SMTP_APP_PASSWORD"].replace(" ", "").strip()
    msg = MIMEMultipart("related")
    msg["Subject"] = f"국채금리·채권ETF {today:%m/%d}"
    msg["From"] = user
    msg["To"] = os.environ["MAIL_TO"].strip()
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("".join(parts), "html", "utf-8"))
    msg.attach(alt)
    for i, png in enumerate(charts):
        img = MIMEImage(png, "png")
        img.add_header("Content-ID", f"<mk{i}>")
        img.add_header("Content-Disposition", "inline", filename=f"mk{i}.png")
        msg.attach(img)
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(user, pw)
        s.send_message(msg)
    print(f"완료: 그래프 {len(charts)}개")


if __name__ == "__main__":
    main()
