import json
import os
import smtplib
import statistics
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import requests

KST = timezone(timedelta(hours=9))
TARGETS = ["H100", "A100"]
VAST_NAMES = [
    "H100 SXM", "H100 NVL", "H100 PCIE",
    "A100 SXM4", "A100 SXM", "A100 PCIE", "A100X",
]
HISTORY_FILE = "history.json"
PERIODS = [(1, 2), (7, 3), (30, 5)]

TABLE = "border-collapse:collapse;width:100%;font-size:12px"
TH = "padding:6px 4px;background:#f1f3f4;border-bottom:2px solid #bbb;text-align:right"
TH_L = TH.replace("right", "left")
TD = "padding:6px 4px;border-bottom:1px solid #e0e0e0;text-align:right"
TD_L = TD.replace("right", "left")


def vast_prices():
    headers = {"Authorization": f"Bearer {os.environ['VAST_API_KEY'].strip()}"}
    result = {}
    for name in VAST_NAMES:
        q = {
            "gpu_name": {"eq": name},
            "rentable": {"eq": True},
            "limit": 300,
        }
        r = requests.get(
            "https://cloud.vast.ai/api/v0/bundles/",
            params={"q": json.dumps(q)},
            headers=headers,
            timeout=60,
        )
        if r.status_code != 200:
            raise RuntimeError(f"{name}: HTTP {r.status_code} {r.text[:150]}")
        offers = r.json().get("offers", [])
        prices = [
            o["dph_total"] / o["num_gpus"] for o in offers if o.get("num_gpus")
        ]
        if prices:
            result[name] = (min(prices), statistics.median(prices), len(prices))
        else:
            result[name] = None
    return result


def runpod_prices():
    q = "query { gpuTypes { displayName securePrice communityPrice } }"
    params = {}
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if key:
        params["api_key"] = key
    r = requests.post(
        "https://api.runpod.io/graphql",
        params=params,
        json={"query": q},
        timeout=60,
    )
    r.raise_for_status()
    out = []
    for g in r.json()["data"]["gpuTypes"]:
        if any(t in g["displayName"] for t in TARGETS):
            out.append((g["displayName"], g["securePrice"], g["communityPrice"]))
    return sorted(out)


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_history(history, today):
    cutoff = today - timedelta(days=120)
    history = {
        d: v
        for d, v in history.items()
        if datetime.strptime(d, "%Y-%m-%d").date() >= cutoff
    }
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1, sort_keys=True)


def baseline(history, key, today, days, tol):
    target = today - timedelta(days=days)
    best = None
    for d, vals in history.items():
        dd = datetime.strptime(d, "%Y-%m-%d").date()
        if dd >= today or key not in vals:
            continue
        gap = abs((dd - target).days)
        if gap <= tol and (best is None or gap < best[0]):
            best = (gap, vals[key])
    return best[1] if best else None


def change_cells(history, key, cur, today):
    cells = ""
    for days, tol in PERIODS:
        old = baseline(history, key, today, days, tol)
        if old is None or old == 0:
            cells += f'<td style="{TD};color:#999">-</td>'
            continue
        diff = cur - old
        pct = diff / old * 100
        if abs(diff) < 0.005:
            cells += f'<td style="{TD};color:#777">변동없음</td>'
            continue
        up = diff > 0
        color = "#d32f2f" if up else "#1565c0"
        arrow = "▲" if up else "▼"
        cells += (
            f'<td style="{TD};color:{color}">{arrow}{abs(diff):.2f}'
            f"<br>({pct:+.1f}%)</td>"
        )
    return cells


def build_html(history, today):
    vals = {}
    h = [
        '<div style="font-family:-apple-system,\'Malgun Gothic\',sans-serif;max-width:680px">',
        f"<h3>GPU 임대료 · {today}</h3>",
        '<p style="font-size:12px;color:#666">단위: $/시간 (GPU 1장 기준) · '
        '<span style="color:#d32f2f">▲상승</span> '
        '<span style="color:#1565c0">▼하락</span> · 증감은 전일/전주/전월 대비</p>',
        "<h4>Vast.ai (증감: 중앙값 기준)</h4>",
        f'<table style="{TABLE}"><tr>'
        f'<th style="{TH_L}">GPU</th><th style="{TH}">최저</th><th style="{TH}">중앙값</th>'
        f'<th style="{TH}">오퍼</th><th style="{TH}">전일</th><th style="{TH}">전주</th>'
        f'<th style="{TH}">전월</th></tr>',
    ]
    try:
        for name, v in vast_prices().items():
            if v is None:
                h.append(
                    f'<tr><td style="{TD_L}">{name}</td>'
                    f'<td style="{TD};color:#999" colspan="6">오퍼 없음</td></tr>'
                )
                continue
            lo, med, n = v
            key = f"vast|{name}|median"
            vals[key] = round(med, 4)
            h.append(
                f'<tr><td style="{TD_L}">{name}</td><td style="{TD}">{lo:.2f}</td>'
                f'<td style="{TD}"><b>{med:.2f}</b></td><td style="{TD}">{n}건</td>'
                + change_cells(history, key, med, today)
                + "</tr>"
            )
    except Exception as e:
        h.append(f'<tr><td colspan="7" style="{TD_L};color:#d32f2f">조회 실패: {e}</td></tr>')
    h.append("</table>")

    h.append(
        "<h4>RunPod</h4>"
        f'<table style="{TABLE}"><tr>'
        f'<th style="{TH_L}">GPU</th><th style="{TH_L}">유형</th><th style="{TH}">가격</th>'
        f'<th style="{TH}">전일</th><th style="{TH}">전주</th><th style="{TH}">전월</th></tr>'
    )
    try:
        for name, sec, com in runpod_prices():
            for label, val, tag in (
                ("Secure", sec, "secure"),
                ("Community", com, "community"),
            ):
                if not val:
                    continue
                key = f"runpod|{name}|{tag}"
                vals[key] = val
                h.append(
                    f'<tr><td style="{TD_L}">{name}</td><td style="{TD_L}">{label}</td>'
                    f'<td style="{TD}"><b>{val:.2f}</b></td>'
                    + change_cells(history, key, val, today)
                    + "</tr>"
                )
    except Exception as e:
        h.append(f'<tr><td colspan="6" style="{TD_L};color:#d32f2f">조회 실패: {e}</td></tr>')
    h.append("</table>")
    h.append(
        '<p style="font-size:11px;color:#888">Secure=전문 데이터센터(안정형), '
        "Community=승인된 개인/소규모 제공자(저가형)</p></div>"
    )
    return "".join(h), vals


def send_mail(html):
    user = os.environ["SMTP_USER"].strip()
    pw = os.environ["SMTP_APP_PASSWORD"].replace(" ", "").strip()
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = f"GPU 임대료 {datetime.now(KST):%m/%d}"
    msg["From"] = user
    msg["To"] = os.environ["MAIL_TO"].strip()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(user, pw)
        s.send_message(msg)


if __name__ == "__main__":
    today = datetime.now(KST).date()
    history = load_history()
    html, vals = build_html(history, today)
    if vals:
        history[str(today)] = vals
        save_history(history, today)
    print(f"수집 항목 {len(vals)}개")
    send_mail(html)
