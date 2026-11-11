#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Amazon ASIN image-version history from Internet Archive snapshots.

Collects archived product-page snapshots, extracts the embedded `colorImages`
block (MAIN / PT01.. image ids), groups them into versions, downloads the
images, and writes a comparison report.

Run with the bundled Python from `load_workspace_dependencies`.
"""

import argparse
import gzip
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = "curl/8.4.0"
CDX = (
    "https://web.archive.org/cdx/search/cdx?url={site}/dp/{asin}"
    "&matchType=prefix&output=json&fl=timestamp,original,statuscode&limit={limit}"
)
COLOR_RE = re.compile(r"'colorImages':\s*(.*?)'colorToAsin'", re.S)
HIRES_RE = re.compile(r'"hiRes":"https://m\.media-amazon\.com/images/I/([A-Za-z0-9+\-_%]+)\.')
VARIANT_RE = re.compile(r'"variant":"([A-Z0-9]+)"')
TITLE_RE = re.compile(r'id="productTitle"[^>]*>(.*?)</span>', re.S)
PARENT_RE = re.compile(r'"parentAsin"\s*:\s*"([A-Z0-9]{10})"')
IMAGES_RE = re.compile(r'https://m\.media-amazon\.com/images/I/([A-Za-z0-9+\-_%]+)\._SL1500_\.jpg')


def ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch(url, timeout=90, retries=4, backoff=12):
    """GET a URL, transparently gunzipping Wayback responses."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Encoding": "identity"}
            )
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as resp:
                body = resp.read()
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            return body
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 404:
                return None
            time.sleep(backoff * (attempt + 1))
        except Exception as exc:  # noqa: BLE001 - network layer varies by host
            last = exc
            time.sleep(backoff)
    raise RuntimeError("fetch failed: %s (%r)" % (url[:110], last))


def cdx_snapshots(site, asin, limit):
    raw = fetch(CDX.format(site=site, asin=asin, limit=limit))
    rows = json.loads(raw.decode("utf-8", "ignore"))
    out = []
    for row in rows[1:] if rows else []:
        if len(row) >= 3 and row[2] == "200":
            out.append((row[0], row[1]))
    out.sort()
    return out


def sample_monthly(snaps):
    """First and last snapshot per calendar month, plus every distinct day."""
    buckets = {}
    for ts, orig in snaps:
        buckets.setdefault(ts[:6], []).append((ts, orig))
    picked, seen = [], set()
    for month in sorted(buckets):
        items = sorted(buckets[month])
        for cand in (items[0], items[-1]):
            if cand[0] not in seen:
                seen.add(cand[0])
                picked.append(cand)
    picked.sort()
    return picked


def parse_page(html):
    """Return the ordered image list from a product page, or None if unparseable."""
    match = COLOR_RE.search(html)
    if not match:
        return None
    block = match.group(1)
    images, seen = [], set()
    for chunk in re.split(r"\},\s*\{", block):
        hires = HIRES_RE.search(chunk)
        variant = VARIANT_RE.search(chunk)
        if not hires or not variant:
            continue
        pid = hires.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        images.append({"id": pid, "variant": variant.group(1)})
    return images or None


def page_meta(html):
    title = TITLE_RE.search(html)
    parent = PARENT_RE.search(html)
    return {
        "title": re.sub(r"\s+", " ", title.group(1)).strip() if title else None,
        "parent_asin": parent.group(1) if parent else None,
    }


def fmt(ts, tz):
    dt = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def signature(images):
    return tuple(img["id"] for img in images)


def slot_name(index):
    return "MAIN" if index == 0 else "PT%02d" % index


def describe_change(prev, cur):
    if prev is None:
        return "首个可解析快照"
    prev_ids = [img["id"] for img in prev]
    cur_ids = [img["id"] for img in cur]
    parts = []
    for i in range(max(len(prev_ids), len(cur_ids))):
        a = prev_ids[i] if i < len(prev_ids) else None
        b = cur_ids[i] if i < len(cur_ids) else None
        if a == b:
            continue
        name = slot_name(i)
        if a is None:
            parts.append(name + " 新增")
        elif b is None:
            parts.append(name + " 移除")
        else:
            parts.append(name + " 更换")
    return "、".join(parts) if parts else "无变化"


def download(images, pics_dir):
    os.makedirs(pics_dir, exist_ok=True)
    for img in images:
        safe = urllib.parse.quote(img["id"], safe="")
        path = os.path.join(pics_dir, safe + ".jpg")
        img["file"] = path
        if os.path.exists(path) and os.path.getsize(path) > 2000:
            continue
        url = "https://m.media-amazon.com/images/I/%s._SL1500_.jpg" % img["id"]
        try:
            body = fetch(url, timeout=60, retries=3, backoff=8)
            if body:
                with open(path, "wb") as handle:
                    handle.write(body)
        except Exception as exc:  # noqa: BLE001
            img["error"] = str(exc)[:120]
        time.sleep(0.3)


def rel(path, base):
    return os.path.relpath(path, base).replace("\\", "/")


def write_contact_sheet(versions, out_dir):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    tile, pad, label_w = 150, 6, 210
    width = label_w + max(len(v["images"]) for v in versions) * (tile + pad) + pad
    height = len(versions) * (tile + pad) + pad
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 12)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()

    y = pad
    for idx, version in enumerate(versions):
        label = "V%d\n%s\n-> %s\n(%d imgs)" % (
            idx + 1,
            fmt(version["snaps"][0], version["tz"])[:10],
            fmt(version["snaps"][-1], version["tz"])[:10],
            len(version["images"]),
        )
        draw.multiline_text((6, y + 4), label, fill="black", font=font, spacing=2)
        x = label_w
        for j, img in enumerate(version["images"]):
            path = img.get("file")
            if path and os.path.exists(path):
                thumb = Image.open(path).convert("RGB")
                thumb.thumbnail((tile, tile))
                cell = Image.new("RGB", (tile, tile), "white")
                cell.paste(thumb, ((tile - thumb.width) // 2, (tile - thumb.height) // 2))
                canvas.paste(cell, (x, y))
                tag = "MAIN" if j == 0 else "PT%02d" % j
                draw.rectangle([x, y + tile - 13, x + 36, y + tile], fill="black")
                draw.text((x + 2, y + tile - 13), tag, fill="white", font=font)
            x += tile + pad
        y += tile + pad

    path = os.path.join(out_dir, "contact_sheet.png")
    canvas.save(path)
    return path


def write_html(versions, out_dir, asin, site, tz_name, stats):
    def esc(text):
        return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rows = ""
    prev = None
    for idx, version in enumerate(versions):
        first, last = version["snaps"][0], version["snaps"][-1]
        span = fmt(first, version["tz"])
        if last != first:
            span += " ~ " + fmt(last, version["tz"])
        figs = ""
        for j, img in enumerate(version["images"]):
            src = rel(img.get("file", ""), out_dir) if img.get("file") else ""
            figs += (
                '<figure><a href="https://m.media-amazon.com/images/I/%s._SL1500_.jpg" target="_blank">'
                '<img src="%s" loading="lazy"></a><figcaption><b>%s</b><br><code>%s</code></figcaption></figure>'
                % (img["id"], src, slot_name(j), esc(img["id"]))
            )
        meta = version.get("meta") or {}
        note = "<br>".join(
            x for x in [esc(meta.get("title") or ""), "parent " + meta["parent_asin"] if meta.get("parent_asin") else ""] if x
        )
        rows += (
            "<tr><td class=v>V%d</td><td>%s<br><span class=u>%d 个快照</span>%s</td>"
            "<td>%s</td><td class=imgs>%s</td></tr>"
            % (idx + 1, span, len(version["snaps"]), ("<br><span class=u>" + note + "</span>") if note else "",
               describe_change(prev, version["images"]), figs)
        )
        prev = version["images"]

    html = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>%(asin)s 图片版本记录</title>
<style>
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:24px;color:#111}
h1{font-size:20px}table{border-collapse:collapse;width:100%%}
td,th{border:1px solid #ddd;padding:8px;vertical-align:top;font-size:13px}
th{background:#f5f5f5;text-align:left}.v{font-weight:700;white-space:nowrap}
.u{color:#777;font-size:11px}.imgs{display:flex;flex-wrap:wrap;gap:8px}
figure{margin:0;width:110px}figure img{width:110px;height:110px;object-fit:contain;border:1px solid #eee}
figcaption{font-size:10px;color:#444;word-break:break-all}code{font-size:10px;color:#0a5}
.note{background:#fff8e6;border:1px solid #f0d9a0;padding:10px 12px;font-size:13px}
</style>
<h1>%(site)s · ASIN %(asin)s · 页面图片版本记录</h1>
<p>来源：Internet Archive Wayback Machine 快照（含带 ref/tag 参数的变体 URL）。解析页面内嵌
<code>colorImages</code> 的 <code>hiRes</code>/<code>variant</code> 字段，图片取自 <code>m.media-amazon.com</code> 原图。
时间为 %(tz)s。</p>
<p class="note">这是「页面在抓取时刻呈现的图片版本」，不是卖家后台的操作时间戳。同一版本内的多个快照只表示该版本在该区间被重复观测到；两个版本之间的空档即换图发生的时间窗口。</p>
<p class=u>快照 %(total)d 个，解析成功 %(ok)d 个，跳过（验证码/非商品页）%(skip)d 个。</p>
<table><tr><th>版本</th><th>观测时间</th><th>相对上一版的变化</th><th>主图与附图</th></tr>%(rows)s</table></html>""" % {
        "asin": asin, "site": site, "tz": tz_name, "rows": rows,
        "total": stats["total"], "ok": stats["ok"], "skip": stats["skip"],
    }
    path = os.path.join(out_dir, "report.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html)
    return path


def write_summary(versions, out_dir, asin, stats):
    lines = ["ASIN %s 图片版本时间轴" % asin, ""]
    lines.append("快照 %d 个，可解析 %d 个，跳过 %d 个" % (stats["total"], stats["ok"], stats["skip"]))
    lines.append("")
    prev = None
    for idx, version in enumerate(versions):
        first, last = version["snaps"][0], version["snaps"][-1]
        span = fmt(first, version["tz"])
        if last != first:
            span += " ~ " + fmt(last, version["tz"])
        lines.append("V%d  %s  (%d 图, %d 快照)  %s" % (
            idx + 1, span, len(version["images"]), len(version["snaps"]),
            describe_change(prev, version["images"])))
        if prev is not None:
            gap_from = fmt(versions[idx - 1]["snaps"][-1], version["tz"])
            gap_to = fmt(first, version["tz"])
            lines.append("    换图窗口: %s  ->  %s" % (gap_from, gap_to))
        lines.append("    " + " ".join("%s=%s" % (slot_name(j), img["id"]) for j, img in enumerate(version["images"])))
        prev = version["images"]
    path = os.path.join(out_dir, "summary.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser(description="Amazon ASIN image history from Wayback snapshots")
    ap.add_argument("--asin", required=True)
    ap.add_argument("--site", default="amazon.com", help="e.g. amazon.com, amazon.co.uk")
    ap.add_argument("--out", default=None, help="output directory (default asin_history/<ASIN>)")
    ap.add_argument("--tz", default="Asia/Shanghai")
    ap.add_argument("--all", action="store_true", help="fetch every snapshot (slow) instead of monthly sampling")
    ap.add_argument("--limit", type=int, default=5000, help="CDX row limit")
    ap.add_argument("--sleep", type=float, default=2.0, help="seconds between snapshot fetches")
    ap.add_argument("--max-snapshots", type=int, default=0,
                    help="cap the number of snapshots fetched (evenly spaced); 0 = no cap")
    args = ap.parse_args()

    from zoneinfo import ZoneInfo

    tz = ZoneInfo(args.tz)
    out_dir = args.out or os.path.join("asin_history", args.asin)
    os.makedirs(out_dir, exist_ok=True)
    pics_dir = os.path.join(out_dir, "pics")

    print("[1/5] 查询 CDX ...", flush=True)
    snaps = cdx_snapshots(args.site, args.asin, args.limit)
    if not snaps:
        print("没有找到任何 200 快照。该 ASIN 可能未被归档，或站点/ASIN 写错。")
        return 2
    print("      归档快照 %d 个（%s ~ %s UTC）" % (len(snaps), snaps[0][0], snaps[-1][0]))

    targets = snaps if args.all else sample_monthly(snaps)
    if args.max_snapshots and len(targets) > args.max_snapshots:
        step = (len(targets) - 1) / float(args.max_snapshots - 1) if args.max_snapshots > 1 else 0
        idxs = sorted({int(round(i * step)) for i in range(args.max_snapshots)} | {0, len(targets) - 1})
        targets = [targets[i] for i in idxs]
    print("[2/5] 抓取 %d 个快照（每月首末采样%s）..." % (len(targets), "" if args.all else "，--all 可全量"))

    observations, seen_sig, stats = [], set(), {"total": len(targets), "ok": 0, "skip": 0}
    for n, (ts, orig) in enumerate(targets, 1):
        url = "https://web.archive.org/web/%sid_/%s" % (ts, orig)
        try:
            body = fetch(url)
        except Exception as exc:  # noqa: BLE001
            print("      %-14s ERR   %s" % (ts, str(exc)[:70]))
            stats["skip"] += 1
            continue
        if not body:
            stats["skip"] += 1
            continue
        html = body.decode("utf-8", "ignore")
        images = parse_page(html)
        if not images:
            low = html.lower()
            reason = "验证码" if ("captcha" in low or "robot" in low) else "非商品页"
            print("      %-14s skip  %s" % (ts, reason))
            stats["skip"] += 1
            time.sleep(args.sleep)
            continue
        observations.append((ts, images, page_meta(html)))
        stats["ok"] += 1
        print("      %-14s ok    %d 图  %s" % (ts, len(images), fmt(ts, tz)))
        time.sleep(args.sleep)

    if not observations:
        print("\n所有快照都无法解析（几乎全是验证码或跳转页）。")
        print("结论：该 ASIN 在公开归档中没有可用的商品页样本，无法还原图片历史。")
        return 3

    print("[3/5] 归并版本 ...", flush=True)
    versions = []
    for ts, images, meta in sorted(observations):
        sig = signature(images)
        if versions and versions[-1]["sig"] == sig:
            versions[-1]["snaps"].append(ts)
        else:
            versions.append({"sig": sig, "images": images, "snaps": [ts], "meta": meta, "tz": tz})
    print("      %d 个版本" % len(versions))

    print("[4/5] 下载图片 ...", flush=True)
    for version in versions:
        download(version["images"], pics_dir)

    print("[5/5] 生成报告 ...", flush=True)
    sheet = write_contact_sheet(versions, out_dir)
    html_path = write_html(versions, out_dir, args.asin, args.site, args.tz, stats)
    summary = write_summary(versions, out_dir, args.asin, stats)

    payload = {
        "asin": args.asin, "site": args.site, "timezone": args.tz,
        "stats": stats,
        "versions": [
            {
                "index": i + 1,
                "snaps_utc": v["snaps"],
                "first": fmt(v["snaps"][0], tz),
                "last": fmt(v["snaps"][-1], tz),
                "meta": v["meta"],
                "images": [{"slot": slot_name(j), **img} for j, img in enumerate(v["images"])],
            }
            for i, v in enumerate(versions)
        ],
    }
    with open(os.path.join(out_dir, "versions.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)

    print("")
    print("完成：%d 个版本" % len(versions))
    for line in open(summary, encoding="utf-8").read().splitlines()[:40]:
        print(line)
    print("")
    print("报告：%s" % html_path)
    if sheet:
        print("拼图：%s" % sheet)
    print("数据：%s" % os.path.join(out_dir, "versions.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
