#!/usr/bin/env python3
"""闪电.skill 测速：冷缓存下测「打开到能用」。

支持网络与 CPU 限速、「能用」判定表达式、打字校验、A/B 两个版本交替配对测量。
依赖：pip install playwright && python3 -m playwright install chromium
"""
import argparse
import asyncio
import json
import statistics
import sys
import time

from playwright.async_api import async_playwright

# 网络档：延迟单位 ms，吞吐单位 字节/秒
PROFILES = {
    "none": None,
    "fast4g": {"offline": False, "latency": 20,
               "downloadThroughput": 4 * 1024 * 1024 / 8,
               "uploadThroughput": 3 * 1024 * 1024 / 8},
    "slow4g": {"offline": False, "latency": 150,
               "downloadThroughput": 1.6 * 1024 * 1024 / 8,
               "uploadThroughput": 750 * 1024 / 8},
}

INIT_JS = """
(() => {
  const READY = () => { try { return !!(%s); } catch (e) { return false; } };
  const f = window.__flash = { ready: null, lcp: null, longtask: 0 };
  try {
    new PerformanceObserver(l => { for (const e of l.getEntries()) f.lcp = e.startTime; })
      .observe({ type: 'largest-contentful-paint', buffered: true });
  } catch (e) {}
  try {
    new PerformanceObserver(l => { for (const e of l.getEntries()) f.longtask += e.duration; })
      .observe({ type: 'longtask', buffered: true });
  } catch (e) {}
  const tick = () => {
    if (f.ready === null && READY()) { f.ready = performance.now(); return; }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})();
"""

COLLECT_JS = """
() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const fcp = performance.getEntriesByName('first-contentful-paint')[0];
  const res = performance.getEntriesByType('resource');
  const bytes = {};
  for (const r of res) {
    const k = r.initiatorType || 'other';
    bytes[k] = (bytes[k] || 0) + (r.transferSize || 0);
  }
  const slowest = res.slice().sort((a, b) => b.responseEnd - a.responseEnd).slice(0, 5)
    .map(r => ({ name: r.name.slice(0, 120), responseEnd: Math.round(r.responseEnd) }));
  return {
    ready: window.__flash.ready, lcp: window.__flash.lcp, longtask: window.__flash.longtask,
    fcp: fcp ? fcp.startTime : null,
    dcl: nav.domContentLoadedEventEnd || null, load: nav.loadEventEnd || null,
    requests: res.length + 1,
    bytes_total: res.reduce((s, r) => s + (r.transferSize || 0), 0) + (nav.transferSize || 0),
    bytes_by_type: bytes, slowest: slowest,
  };
}
"""


def pct(values, p):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    if len(v) == 1:
        return round(v[0], 1)
    return round(statistics.quantiles(v, n=100, method="inclusive")[p - 1], 1)


async def measure_once(browser, url, args, ready_expr):
    ctx = await browser.new_context(viewport={"width": args.width, "height": args.height})
    page = await ctx.new_page()
    cdp = await ctx.new_cdp_session(page)
    await cdp.send("Network.enable")
    await cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    if PROFILES[args.profile]:
        await cdp.send("Network.emulateNetworkConditions", PROFILES[args.profile])
    if args.cpu > 1:
        await cdp.send("Emulation.setCPUThrottlingRate", {"rate": args.cpu})
    await page.add_init_script(INIT_JS % ready_expr)
    out = {"url": url, "ok": True}
    try:
        await page.goto(url, wait_until="commit", timeout=args.timeout * 1000)
        await page.wait_for_function("window.__flash && window.__flash.ready !== null",
                                     timeout=args.timeout * 1000, polling=50)
        if args.type_check:
            await page.click(args.type_check)
            await page.keyboard.type("Z")
            typed = await page.evaluate(
                "(s) => { const el = document.querySelector(s); if (!el) return false;"
                " const v = 'value' in el ? el.value : el.innerText; return (v || '').includes('Z'); }",
                args.type_check)
            out["type_check"] = bool(typed)
            if not typed:
                out["ok"] = False
        await page.wait_for_timeout(args.settle)
        out.update(await page.evaluate(COLLECT_JS))
    except Exception as e:  # 超时或页面出错：如实记录，不计入分位数
        out["ok"] = False
        out["error"] = str(e)[:300]
    await ctx.close()
    return out


def summarize(runs):
    good = [r for r in runs if r.get("ok")]
    s = {"n": len(runs), "n_ok": len(good)}
    for k in ("ready", "lcp", "fcp", "dcl", "load", "longtask", "requests", "bytes_total"):
        vals = [r.get(k) for r in good]
        s[k] = {"p50": pct(vals, 50), "p75": pct(vals, 75), "p95": pct(vals, 95)}
    return s


async def main():
    ap = argparse.ArgumentParser(description="冷缓存下测「打开到能用」")
    ap.add_argument("--url", required=True, help="版本 A 的地址")
    ap.add_argument("--url-b", help="版本 B 的地址；给了就 A、B 交替配对测")
    ap.add_argument("--ready", default="document.readyState !== 'loading' && !!document.body && document.body.innerText.trim().length > 0",
                    help="「能用」的判定 JS 表达式")
    ap.add_argument("--type-check", help="判定为真后往这个选择器打一个字，确认真能输入")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--profile", choices=list(PROFILES), default="fast4g")
    ap.add_argument("--cpu", type=int, default=1, help="CPU 限速倍数")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--timeout", type=int, default=30, help="单次测量超时秒数")
    ap.add_argument("--settle", type=int, default=500, help="判定为真后再等多少毫秒收集 LCP 等数据")
    ap.add_argument("--proxy", help="访问外网用的代理，如 http://127.0.0.1:7890；本地地址自动直连")
    ap.add_argument("--out", help="结果 JSON 路径")
    args = ap.parse_args()

    launch_args = []
    if args.proxy:
        launch_args += [f"--proxy-server={args.proxy}", "--proxy-bypass-list=127.0.0.1,localhost,<local>"]
    ready_expr = args.ready.replace("%", "%%")

    urls = {"A": args.url}
    if args.url_b:
        urls["B"] = args.url_b
    runs = {k: [] for k in urls}
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=launch_args)
        for i in range(args.runs):
            order = list(urls) if i % 2 == 0 else list(reversed(list(urls)))
            for k in order:
                r = await measure_once(browser, urls[k], args, ready_expr)
                runs[k].append(r)
                flag = "" if r.get("ok") else "  失败: " + r.get("error", "打字校验没通过")
                print(f"[{i + 1}/{args.runs}] {k} ready={r.get('ready') and round(r['ready'])}ms "
                      f"lcp={r.get('lcp') and round(r['lcp'])}ms{flag}", file=sys.stderr)
        await browser.close()

    result = {
        "started": started, "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {k: getattr(args, k) for k in ("runs", "profile", "cpu", "width", "height", "ready", "type_check")},
        "paired": bool(args.url_b),
        "results": {k: {"url": urls[k], "summary": summarize(runs[k]), "runs": runs[k]} for k in urls},
    }
    if args.url_b:
        a = result["results"]["A"]["summary"]["ready"]["p75"]
        b = result["results"]["B"]["summary"]["ready"]["p75"]
        if a and b:
            result["ready_p75_reduction"] = round(1 - b / a, 4)

    for k, v in result["results"].items():
        s = v["summary"]
        print(f"{k}  {v['url']}  成功 {s['n_ok']}/{s['n']}  "
              f"能用 p50/p75/p95 = {s['ready']['p50']}/{s['ready']['p75']}/{s['ready']['p95']} ms  "
              f"LCP p75 = {s['lcp']['p75']} ms  请求数 p75 = {s['requests']['p75']}  "
              f"字节 p75 = {s['bytes_total']['p75']}")
    if "ready_p75_reduction" in result:
        print(f"能用 p75 降幅：{result['ready_p75_reduction'] * 100:.1f}%")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
