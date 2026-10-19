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
PERIODS = [("전일", 1, 2), ("전주", 7, 3), ("전월", 30, 5)]


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


def fmt_change(cur, old):
    if old is None or old == 0:
        return "-"
    diff = cur - old
    pct = diff / old * 100
    if abs(diff) < 0.005:
        return "변동없음"
    arrow = "▲" if diff > 0 else "▼"
    return f"{arrow}{abs(diff):.2f}({pct:+.1f}%)"


def change_line(history, key, cur, today, label):
    parts = []
    for p_name, days, tol in PERIODS:
        old = baseline(history, key, today, days, tol)
        parts.append(f"{p_name} {fmt_change(cur, old)}")
    return f"  {label}: " + " | ".join(parts)


def build_message(history, today):
    vals = {}
    lines = [
        f"[GPU 임대료] {today}",
        "",
        "■ Vast.ai (1장 $/h, 최저/중앙값/오퍼수)",
        "(증감은 중앙값 기준)",
    ]
    try:
        for name, v in vast_prices().items():
            if v is None:
                lines.append(f"- {name}: 오퍼 없음")
                continue
            lo, med, n = v
            lines.append(f"- {name}: {lo:.2f} / {med:.2f} / {n}건")
            key = f"vast|{name}|median"
            vals[key] = round(med, 4)
            lines.append(change_line(history, key, med, today, "중앙값"))
    except Exception as e:
        lines.append(f"- 조회 실패: {e}")
    lines += ["", "■ RunPod ($/h, Secure / Community)"]
    try:
        for name, sec, com in runpod_prices():
            lines.append(f"- {name}: {sec or '-'} / {com or '-'}")
            for label, val, tag in (("Secure", sec, "secure"), ("Comm", com, "community")):
                if val:
                    key = f"runpod|{name}|{tag}"
                    vals[key] = val
                    lines.append(change_line(history, key, val, today, label))
    except Exception as e:
        lines.append(f"- 조회 실패: {e}")
    return "\n".join(lines), vals


def send_mail(text):
    user = os.environ["SMTP_USER"].strip()
    pw = os.environ["SMTP_APP_PASSWORD"].replace(" ", "").strip()
    msg = MIMEText(text, _charset="utf-8")
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
    text, vals = build_message(history, today)
    if vals:
        history[str(today)] = vals
        save_history(history, today)
    print(text)
    send_mail(text)
