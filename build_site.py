import json
import os
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import bond_report as br
import gpu_price_report as gp
import market_summary as ms

KST = timezone(timedelta(hours=9))


def series_pack(s, kind, n):
    last, cur, ch = ms.stats(s, kind)
    return {
        "date": last.strftime("%m/%d"),
        "cur": cur,
        "ch": ch,
        "spark": [round(v, 4) for _, v in sorted(s.items())][-n:],
    }


def build_market(today):
    groups = []
    for gname, items in ms.GROUPS:
        out = []
        for label, en, sym, fred_id, fmt, kind in items:
            item = {"name": label, "en": en, "kind": kind,
                    "warn_up": en in ("VIX", "HY Spread")}
            try:
                s = ms.get_series(sym, fred_id, today)
                item.update(series_pack(s, kind, 30))
                item["text"] = fmt.format(item["cur"])
            except Exception as e:
                item["error"] = str(e)[:100]
            out.append(item)
        groups.append({"group": gname, "items": out})
    return groups


def build_yields(today):
    start = today - timedelta(days=br.LOOKBACK)
    data, errors = {}, []
    for label, sid in (("미국 10년", "DGS10"), ("미국 30년", "DGS30")):
        try:
            data[label] = br.fred(sid, start)
        except Exception as e:
            errors.append(f"{label}: {e}")
    try:
        key = os.environ["ECOS_API_KEY"].strip()
        stat, codes = br.ecos_codes(key)
        for yrs in (10, 30):
            try:
                data[f"한국 {yrs}년"] = br.ecos_series(
                    key, stat, codes[yrs], start, today
                )
            except Exception as e:
                errors.append(f"한국 {yrs}년: {e}")
    except Exception as e:
        errors.append(f"한국: {e}")
    items = []
    for label in ("미국 10년", "미국 30년", "한국 10년", "한국 30년"):
        s = data.get(label)
        if not s:
            items.append({"name": label, "error": "조회 실패"})
            continue
        pack = series_pack(s, "bp", 90)
        pack["name"] = label
        pack["text"] = f"{pack['cur']:.3f}%"
        items.append(pack)
    return {"items": items, "errors": [e[:150] for e in errors]}


def build_etf(today):
    start = today - timedelta(days=120)
    errors = []
    try:
        kr_items = br.load_kr_list()
    except Exception as e:
        kr_items = []
        errors.append(f"네이버 ETF 목록: {e}"[:150])
    groups, used = [], set()
    for gname, entries in br.GROUPS:
        out = []
        for label, kind, ident in entries:
            try:
                if kind == "kr":
                    found = br.find_kr(kr_items, ident)
                    if not found:
                        raise RuntimeError("종목 찾지 못함")
                    code, name = found
                    if code in used:
                        continue
                    used.add(code)
                    s = br.kr_series(code, start, today)
                    label = name
                else:
                    s = br.us_series(ident)
                pack = series_pack(s, "pct", 60)
                pack["name"] = label
                pack["text"] = (
                    f"{pack['cur']:,.0f}원" if kind == "kr" else f"${pack['cur']:,.2f}"
                )
                out.append(pack)
            except Exception as e:
                out.append({"name": label, "error": str(e)[:100]})
        groups.append({"group": gname, "items": out})
    return {"groups": groups, "errors": errors}


def build_gpu(today):
    history = gp.load_history()

    def pct(cur, key):
        res = []
        for days, tol in gp.PERIODS:
            old = gp.baseline(history, key, today, days, tol)
            res.append(None if not old else (cur / old - 1) * 100)
        return res

    def hist_spark(key, cur):
        pts = [v[key] for d, v in sorted(history.items())
               if key in v and d < str(today)]
        pts.append(cur)
        return pts[-30:]

    out = {"vast": [], "runpod": [], "errors": []}
    try:
        for name, v in gp.vast_prices().items():
            if v is None:
                continue
            lo, med, n = v
            key = f"vast|{name}|median"
            out["vast"].append({
                "name": name, "low": round(lo, 2), "med": round(med, 2),
                "n": n, "ch": pct(med, key), "spark": hist_spark(key, med),
            })
    except Exception as e:
        out["errors"].append(f"Vast.ai: {e}"[:150])
    try:
        for name, sec, com in gp.runpod_prices():
            for label, val, tag in (("Secure", sec, "secure"),
                                    ("Community", com, "community")):
                if not val:
                    continue
                key = f"runpod|{name}|{tag}"
                out["runpod"].append({
                    "name": name, "type": label, "price": val,
                    "ch": pct(val, key), "spark": hist_spark(key, val),
                })
    except Exception as e:
        out["errors"].append(f"RunPod: {e}"[:150])
    return out


def write_manifest():
    manifest = {
        "name": "시장 브리핑", "short_name": "시장브리핑",
        "start_url": "./", "display": "standalone",
        "background_color": "#0f172a", "theme_color": "#0f172a",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    with open("manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)


def write_icons():
    for size, name in ((192, "icon-192.png"), (512, "icon-512.png"),
                       (180, "apple-touch-icon.png")):
        fig = plt.figure(figsize=(size / 100, size / 100), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        ax.add_patch(plt.Rectangle((0, 0), 1, 1, color="#0f172a"))
        xs = [0.16, 0.34, 0.48, 0.64, 0.84]
        ys = [0.30, 0.46, 0.38, 0.60, 0.74]
        ax.plot(xs, ys, color="#f43f5e", linewidth=size / 22,
                solid_capstyle="round", solid_joinstyle="round")
        ax.scatter([xs[-1]], [ys[-1]], s=size * 1.6, color="#f43f5e", zorder=3)
        fig.savefig(name, dpi=100, facecolor="#0f172a")
        plt.close(fig)


def main():
    now = datetime.now(KST)
    today = now.date()

    def safe(fn):
        try:
            return fn(today)
        except Exception as e:
            return {"error": str(e)[:200]}

    data = {
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "market": safe(build_market),
        "yields": safe(build_yields),
        "etf": safe(build_etf),
        "gpu": safe(build_gpu),
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    write_manifest()
    write_icons()
    print("완료", data["updated"])


if __name__ == "__main__":
    main()
