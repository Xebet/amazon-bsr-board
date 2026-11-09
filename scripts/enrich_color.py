# -*- coding: utf-8 -*-
"""补齐历史数据的「原始颜色属性」

用法:
    python enrich_color.py --workdir . [--workers 4] [--dates 2026-10-21,2026-10-22]

做的事:
1. 扫描 data/<日期>/items.json，收集所有 ASIN（跨日期去重）
2. 用浏览器逐个抓商品页，取亚马逊自己的 Color 属性原文（"Light Hot Pink" / "Off Black 1B"）
3. 结果缓存到 data/_color_attrs.json，重复运行不重复抓
4. 写回每份 items.json 的 color_raw / color_src / color（色系），不碰品牌、价格、图片

抓不到的（页面被拦 / 该商品无 Color 字段）留给 run.py 的颜色阶段按标题兜底。
"""
import argparse, asyncio, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run as R  # noqa: E402  复用 UA / DP_JS / 缓存目录 / 日志

UA = R.UA
CACHE_NAME = "_color_attrs.json"


async def _worker(queue, results, browser, market, pid):
    ctx = await browser.new_context(locale="en-US", user_agent=UA,
                                    viewport={"width": 1400, "height": 1000},
                                    **R._state_kw())
    await ctx.add_cookies([{"name": "i18n-prefs", "value": "USD",
                            "domain": "." + market, "path": "/"}])
    page = await ctx.new_page()
    while True:
        try:
            asin = queue.pop(0)
        except IndexError:
            break
        for attempt in (1, 2):
            try:
                await page.goto(f"https://www.{market}/dp/{asin}",
                                wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(1200 if attempt == 1 else 2500)
                r = await page.evaluate(R.DP_JS)
                color = R._clean_color(r.get("color") or "")
                title = (r.get("title") or "").strip()
                if not title and not color:          # 被拦 / 页面没渲染出来
                    if attempt == 2:
                        results[asin] = {"color": "", "ok": False}
                    else:
                        await page.wait_for_timeout(2500)
                    continue
                results[asin] = {"color": color, "ok": True}
                break
            except Exception as e:
                if attempt == 2:
                    results[asin] = {"color": "", "ok": False, "err": str(e)[:80]}
                else:
                    await page.wait_for_timeout(1500)
    await ctx.close()


async def scrape(asins, market, workers):
    from playwright.async_api import async_playwright
    results = {}
    queue = list(asins)
    async with async_playwright() as p:
        try:
            br = await p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            br = await p.chromium.launch(headless=True)
        t0 = time.time()
        tasks = [_worker(queue, results, br, market, i) for i in range(workers)]
        done = 0

        async def report():
            nonlocal done
            while len(results) < len(asins):
                await asyncio.sleep(10)
                if len(results) - done >= 15:
                    done = len(results)
                    R.log(f"  {done}/{len(asins)}（{time.time() - t0:.0f}s）")

        await asyncio.gather(*tasks, report())
        await br.close()
    return results


ROOT = os.path.dirname(HERE)                                  # workflow/ 或 <skill>/


def _default_workdir():
    """同一份代码在两种位置都能跑对，因此技能版与项目版可以字节一致：
    - 项目内  <root>/workflow/pipeline/<脚本>.py  ->  <root>
    - 技能内  <skill>/scripts/<脚本>.py           ->  当前工作目录
    """
    if os.path.basename(ROOT) == "workflow":
        return os.path.dirname(ROOT)
    return os.getcwd()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=None,
                    help="工程根目录（含 data/ 与 output/），默认：项目内=项目根，技能内=当前目录")
    ap.add_argument("--market", default="amazon.com")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dates", default="", help="只处理指定日期，逗号分隔")
    args = ap.parse_args()

    wd = os.path.abspath(args.workdir or _default_workdir())
    data_dir = os.path.join(wd, "data")
    want = [d.strip() for d in args.dates.split(",") if d.strip()]

    dates = []
    for d in sorted(os.listdir(data_dir)):
        p = os.path.join(data_dir, d, "items.json")
        if os.path.isdir(os.path.join(data_dir, d)) and os.path.exists(p):
            if not want or d in want:
                dates.append(d)
    if not dates:
        raise SystemExit("没有可处理的 items.json")

    per_date = {d: json.load(open(os.path.join(data_dir, d, "items.json"), encoding="utf-8"))
                for d in dates}
    asins = []
    seen = set()
    for items in per_date.values():
        for it in items:
            if it["asin"] not in seen:
                seen.add(it["asin"])
                asins.append(it["asin"])
    R.log(f"待处理 {len(dates)} 天，去重 ASIN {len(asins)} 个")

    cache_path = os.path.join(data_dir, CACHE_NAME)
    cache = {}
    if os.path.exists(cache_path):
        cache = json.load(open(cache_path, encoding="utf-8"))
        R.log(f"已有缓存 {sum(1 for a in asins if cache.get(a, {}).get('ok'))} 条")

    todo = [a for a in asins if not cache.get(a, {}).get("ok")]
    if todo:
        R.log(f"浏览器抓取 {len(todo)} 条（并发 {args.workers}）…")
        asyncio.run(R.prime_us_session(args.market))   # 带美国收货地址，避免价格被隐藏
        res = asyncio.run(scrape(todo, args.market, max(1, args.workers)))
        cache.update(res)
        json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        ok = sum(1 for a in todo if res.get(a, {}).get("ok"))
        R.log(f"  完成：成功 {ok} / 失败 {len(todo) - ok}")
    else:
        R.log("全部命中缓存，无需重抓")

    total_dp = total_ti = total_no = 0
    for d, items in per_date.items():
        for it in items:
            c = cache.get(it["asin"], {})
            # 只有「确实是颜色」的缓存值才覆盖：否则卖家把变体位填成规格/货号时，
            # 脏值会顶掉 items.json 里本来合法的 color_raw（2026-11-06 #26/#61 血案）。
            if c.get("color") and R.is_color_like(c["color"]):
                it["color_raw"], it["color_src"] = c["color"], "dp"
                it["color_dp"] = c["color"]
            # 抓不到的交给 stage_color 按标题兜底
        items = R.stage_color(items)
        json.dump(items, open(os.path.join(data_dir, d, "items.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        n_dp = sum(1 for x in items if x["color_src"] == "dp")
        n_ti = sum(1 for x in items if x["color_src"] == "title")
        n_no = sum(1 for x in items if x["color_src"] == "none")
        total_dp += n_dp
        total_ti += n_ti
        total_no += n_no
        R.log(f"  {d}：属性 {n_dp} / 标题 {n_ti} / 无 {n_no}")

    R.log(f"合计：商品页属性 {total_dp} 条，标题兜底 {total_ti} 条，无颜色 {total_no} 条")
    if total_no:
        R.log("  ⚠ 有商品既无 Color 属性、标题也无线索，卡片会显示 —")


if __name__ == "__main__":
    main()
