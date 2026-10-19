"""H100/A100 시간당 임대료를 Vast.ai, RunPod에서 조회해 메일/카톡으로 발송.

환경변수:
  VAST_API_KEY, RUNPOD_API_KEY
  메일: SMTP_USER(Gmail 주소), SMTP_APP_PASSWORD(앱 비밀번호), MAIL_TO
  카톡(선택): KAKAO_REST_KEY, KAKAO_REFRESH_TOKEN
"""
import os
import smtplib
import statistics
from datetime import datetime
from email.mime.text import MIMEText

import requests

TARGETS = ["H100", "A100"]


def vast_prices():
    """Vast.ai 온디맨드 오퍼의 GPU 1장당 시간 가격 (중앙값/최저가)."""
    r = requests.post(
        "https://console.vast.ai/api/v0/bundles/",
        headers={"Authorization": f"Bearer {os.environ['VAST_API_KEY']}"},
        json={
            "rented": {"eq": False},
            "verified": {"eq": True},
            "type": "on-demand",
            "limit": 1000,
        },
        timeout=60,
    )
    r.raise_for_status()
    buckets = {}
    for o in r.json().get("offers", []):
        name = o.get("gpu_name", "")
        for t in TARGETS:
            if t in name and o.get("num_gpus"):
                buckets.setdefault(name, []).append(o["dph_total"] / o["num_gpus"])
    return {
        k: (min(v), statistics.median(v), len(v)) for k, v in sorted(buckets.items())
    }


def runpod_prices():
    """RunPod GPU 타입별 시간 가격 (Secure / Community)."""
    q = "query { gpuTypes { displayName securePrice communityPrice } }"
    r = requests.post(
        "https://api.runpod.io/graphql",
        params={"api_key": os.environ["RUNPOD_API_KEY"]},
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
    lines = [f"[GPU 임대료] {datetime.now():%Y-%m-%d}", "", "■ Vast.ai (장당 $/h, 최저/중앙값/오퍼수)"]
    try:
        for name, (lo, med, n) in vast_prices().items():
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
    msg = MIMEText(text, _charset="utf-8")
    msg["Subject"] = f"GPU 임대료 {datetime.now():%m/%d}"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_APP_PASSWORD"])
        s.send_message(msg)


def send_kakao(text):
    tok = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": os.environ["KAKAO_REST_KEY"],
            "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"],
        },
        timeout=30,
    ).json()
    template = {
        "object_type": "text",
        "text": text[:1000],
        "link": {"web_url": "https://vast.ai", "mobile_web_url": "https://vast.ai"},
    }
    import json

    requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {tok['access_token']}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=30,
    ).raise_for_status()


if __name__ == "__main__":
    text = build_message()
    print(text)
    if os.environ.get("SMTP_USER"):
        send_mail(text)
    if os.environ.get("KAKAO_REST_KEY"):
        send_kakao(text)
