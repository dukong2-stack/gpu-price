import json
import os
import smtplib
import statistics
from datetime import datetime
from email.mime.text import MIMEText

import requests

TARGETS = ["H100", "A100"]
VAST_NAMES = [
    "H100_SXM", "H100_NVL", "H100_PCIE",
    "A100_SXM4", "A100_SXM", "A100_PCIE", "A100X",
]


def vast_prices():
    headers = {"Authorization": f"Bearer {os.environ['VAST_API_KEY'].strip()}"}
    result = {}
    for name in VAST_NAMES:
        q = {
            "gpu_name": {"eq": name},
            "num_gpus": {"eq": 1},
            "rentable": {"eq": True},
            "verified": {"eq": True},
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
        prices = [o["dph_total"] for o in r.json().get("offers", [])]
        if prices:
            result[name] = (min(prices), statistics.median(prices), len(prices))
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


def build_message():
    lines = [
        f"[GPU 임대료] {datetime.now():%Y-%m-%d}",
        "",
        "■ Vast.ai (1장 $/h, 최저/중앙값/오퍼수)",
    ]
    try:
        data = vast_prices()
        if not data:
            lines.append("- 조회된 오퍼 없음")
        for name, (lo, med, n) in data.items():
            lines.append(f"- {name}: {lo:.2f} / {med:.2f} / {n}건")
    except Exception as e:
        lines.append(f"- 조회 실패: {e}")
    lines += ["", "■ RunPod ($/h, Secure / Community)"]
    try:
        for name, sec, com in runpod_prices():
            lines.append(f"- {name}: {sec or '-'} / {com or '-'}")
    except Exception as e:
        lines.append(f"- 조회 실패: {e}")
    return "\n".join(lines)


def send_mail(text):
    user = os.environ["SMTP_USER"].strip()
    pw = os.environ["SMTP_APP_PASSWORD"].replace(" ", "").strip()
    msg = MIMEText(text, _charset="utf-8")
    msg["Subject"] = f"GPU 임대료 {datetime.now():%m/%d}"
    msg["From"] = user
    msg["To"] = os.environ["MAIL_TO"].strip()
    print(f"메일 로그인 시도: 아이디 {len(user)}자, 비밀번호 {len(pw)}자")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(user, pw)
        s.send_message(msg)


if __name__ == "__main__":
    text = build_message()
    print(text)
    send_mail(text)
