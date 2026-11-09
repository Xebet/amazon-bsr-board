# -*- coding: utf-8 -*-
"""
亚马逊美国站 Best Sellers 每日看板 · 一键流水线
==================================================
用法：
    python run.py --date 2026-10-21                 # 全流程抓取并生成表格
    python run.py --date 2026-10-21 --skip-fetch    # 复用已抓数据，只重排表格
    python run.py --node 702381011 --name "Hair Replacement Wigs" --date 2026-10-22

产出（相对 --workdir）：
    data/<date>/items.json        结构化数据快照（可用于跨日对比）
    data/<date>/images/*.jpg      商品图
    output/Amazon_BestSellers_<slug>_<date>.xlsx   看板表格

依赖：openpyxl pillow playwright（playwright 用系统 Edge，不需下载 chromium）
"""
import argparse
import asyncio
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import glob
import html
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

__version__ = "1.2.0"      # 与 SKILL.md 的 version 字段保持一致，由 tools/sync_skill.py 统一维护

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
COOKIE = "i18n-prefs=USD; lc-main=en_US"     # 必须：否则价格是人民币

# ---------------------------------------------------------------- 美国收货地址
# 不设美国地址时，Amazon 会对一部分商品显示「cannot be shipped to your selected
# delivery location」并隐藏价格（实测 100 条里有 30~37 条无价，且偏向高价品）。
# 做法：建一次带美国 ZIP 的会话，落盘为 storage_state，后续所有浏览器上下文与
# curl 复用同一会话。会话失效时（TTL 到期）自动重建。
US_ZIP = os.environ.get("AMZ_US_ZIP", "10001")
_LOCATION_TTL = 6 * 3600
_PROJ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_PRIMED = [False]

ADDR_JS = r"""async (zip) => {
  const body = new URLSearchParams({locationType:'LOCATION_INPUT', zipCode:zip,
    storeContext:'generic', deviceType:'web', pageType:'Gateway', actionSource:'glow'});
  const r = await fetch('https://www.amazon.com/gp/delivery/ajax/address-change.html',
    {method:'POST', credentials:'include',
     headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8',
              'X-Requested-With':'XMLHttpRequest'},
     body: body.toString()});
  const t = await r.text();
  return {status: r.status, text: t.slice(0, 300)};
}"""

LOC_JS = ("() => { const e = document.querySelector('#glow-ingress-line2');"
          " return e ? e.innerText.trim() : ''; }")


def _state_path():
    return os.path.join(_PROJ, "data", "_amz_session.json")


def _jar_path():
    return os.path.join(_PROJ, "data", "_amz_cookies.txt")


def _state_kw():
    """给 new_context 注入已落盘的会话（含美国收货地址）；没有就不加"""
    p = _state_path()
    return {"storage_state": p} if os.path.exists(p) else {}


def _write_cookie_jar(state):
    """把 storage_state 转成 curl 用的 Netscape cookie 文件，让 curl 也带美国地址"""
    lines = ["# Netscape HTTP Cookie File"]
    for c in state.get("cookies", []):
        dom = c.get("domain", "")
        if "amazon." not in dom:
            continue
        exp = int(c.get("expires") or 0)
        exp = exp if exp > 0 else int(time.time()) + 86400
        flag = "TRUE" if dom.startswith(".") else "FALSE"
        lines.append("\t".join([dom, flag, c.get("path", "/"),
                                "TRUE" if c.get("secure") else "FALSE",
                                str(exp), c["name"], c["value"]]))
    open(_jar_path(), "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")


async def prime_us_session(market="amazon.com", zip_code=None, force=False):
    """建立「美国收货地址」会话并落盘；失败则静默降级为默认地址（价格可能有缺失）"""
    if _PRIMED[0]:
        return _state_path() if os.path.exists(_state_path()) else None
    zip_code = zip_code or US_ZIP
    p = _state_path()
    if not force and os.path.exists(p) and time.time() - os.path.getmtime(p) < _LOCATION_TTL:
        _PRIMED[0] = True
        log(f"  复用已有美国收货地址会话（ZIP {zip_code}）")
        return p

    from playwright.async_api import async_playwright
    _PRIMED[0] = True
    try:
        async with async_playwright() as pw:
            try:
                br = await pw.chromium.launch(channel="msedge", headless=True,
                                              args=["--disable-blink-features=AutomationControlled"])
            except Exception:
                br = await pw.chromium.launch(headless=True)
            ctx = await br.new_context(locale="en-US", user_agent=UA,
                                       viewport={"width": 1400, "height": 1000})
            await ctx.add_cookies([
                {"name": "i18n-prefs", "value": "USD", "domain": "." + market, "path": "/"},
                {"name": "lc-main", "value": "en_US", "domain": "." + market, "path": "/"},
            ])
            page = await ctx.new_page()
            await page.goto(f"https://www.{market}/", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1200)
            r = await page.evaluate(ADDR_JS, zip_code)
            ok = '"isAddressUpdated":1' in (r.get("text") or "")
            loc = ""
            if ok:
                await page.goto(f"https://www.{market}/", wait_until="domcontentloaded",
                                timeout=60000)
                await page.wait_for_timeout(900)
                loc = await page.evaluate(LOC_JS)
            state = await ctx.storage_state()
            await br.close()
    except Exception as e:
        log(f"  ! 设置美国收货地址失败，按默认地址继续（价格可能有缺失）：{e}")
        return None

    if not ok or zip_code not in (loc or ""):
        log(f"  ! 美国收货地址未生效（当前 {loc or '未知'}），价格可能仍有缺失")
        if os.path.exists(p):
            os.remove(p)                      # 别让坏会话被后续复用
        if os.path.exists(_jar_path()):
            os.remove(_jar_path())
        return None

    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(state, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    _write_cookie_jar(state)
    log(f"  美国收货地址就绪：{loc}（会话已缓存，6 小时内复用）")
    return p

# ---------------------------------------------------------------- 版式常量
# 改动这些值 = 改版式；已按 Excel 真实列宽(90px/列)校准过图片居中
LAYOUT = dict(
    per_row=10,          # 每行卡片数
    rows_per_band=6,     # 每张卡片占 5 行 + 1 行间隔
    col_w=11.2,          # 单列宽（字符）→ 实测算 90px
    gut_w=1.5,           # 卡片之间留白列宽
    start_col=2,         # B 列起排
    card_cols=2,         # 每张卡占 2 列
    card_step=3,         # 2 列卡片 + 1 列间距
    header_rows=6,       # 卡片起始行（1标题 2数据源 3价格品牌 4颜色分布 5留白）
    img_row_h=112,       # 图片行高（pt）
    img_max_w=126,       # 图片最大宽（px）
    img_max_h=138,       # 图片最大高（px）
    mdw=8,               # 默认字体最大数字宽度（本机默认字体为 8，非 7）
)

INK, MUTED, LINE = "FF1F2937", "FF6B7280", "FFDCE1E8"
CARD_BG, AMZ, TOP3 = "FFFFFFFF", "FFFF9900", "FFC2410C"

STYLE_COLOR = {
    "黑色": ("FF1F2937", "FFFFFFFF"),
    "金色": ("FFC99A2E", "FFFFFFFF"),
    "渐变 / 挑染": ("FF0F9488", "FFFFFFFF"),
    "白色 / 银色": ("FFE5E7EB", "FF374151"),
    "粉色": ("FFEC4899", "FFFFFFFF"),
    "红 / 酒红": ("FFB91C1C", "FFFFFFFF"),
    "蓝色": ("FF2563EB", "FFFFFFFF"),
    "棕色": ("FF92400E", "FFFFFFFF"),
    "橙色": ("FFEA580C", "FFFFFFFF"),
    "紫色": ("FF7C3AED", "FFFFFFFF"),
    "绿色": ("FF059669", "FFFFFFFF"),
    "其他 / 未标注": ("FFF3F4F6", "FF6B7280"),
}
COLOR_SHORT = {"渐变 / 挑染": "渐变挑染", "白色 / 银色": "白/银",
               "红 / 酒红": "红/酒红", "其他 / 未标注": "其他"}
COLOR_ORDER = ["黑色", "金色", "渐变 / 挑染", "白色 / 银色", "粉色", "红 / 酒红",
               "蓝色", "棕色", "橙色", "紫色", "绿色", "其他 / 未标注"]


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- 阶段 1：抓榜单
GRID_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('#gridItemRoot').forEach(c => {
    const a = c.querySelector('a[href*="/dp/"]');
    if (!a) return;
    const m = a.getAttribute('href').match(/\/dp\/([A-Z0-9]{10})/);
    if (!m || seen.has(m[1])) return;
    seen.add(m[1]);
    const badge = c.querySelector('.zg-bdg-text');
    const img = c.querySelector('img');
    const pEl = c.querySelector('span[class*="p13n-sc-price"], .p13n-sc-price, span.a-color-price');
    const tEl = c.querySelector('a.a-link-normal > span > div, .p13n-sc-truncate-desktop-type2, [class*="line-clamp"]');
    out.push({
      rank: badge ? parseInt(badge.textContent.replace(/[^0-9]/g, ''), 10) : null,
      asin: m[1],
      title: tEl ? tEl.textContent.trim() : (img ? (img.getAttribute('alt') || '') : ''),
      grid_price: pEl ? pEl.textContent.trim() : '',
      img: img ? (img.src || img.getAttribute('data-src') || '') : ''
    });
  });
  return out;
}
"""


async def _scrape_grid(node_id, market="amazon.com", want=100):
    from playwright.async_api import async_playwright
    items = {}
    async with async_playwright() as p:
        kwargs = dict(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            browser = await p.chromium.launch(channel="msedge", **kwargs)
        except Exception:
            browser = await p.chromium.launch(**kwargs)      # 没装 Edge 就用自带 chromium
        ctx = await browser.new_context(locale="en-US", viewport={"width": 1500, "height": 1100},
                                        user_agent=UA, **_state_kw())
        await ctx.add_cookies([{"name": "i18n-prefs", "value": "USD",
                                "domain": "." + market, "path": "/"}])
        page = await ctx.new_page()
        for pg in (1, 2):
            url = f"https://www.{market}/gp/bestsellers/beauty/{node_id}/?pg={pg}"
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2200)                 # 等首屏渲染完
            # 榜单每页只直出 30 条，其余靠滚动触发 AJAX 补齐，必须连续多轮无增长才算到底
            last, stable = -1, 0
            for _ in range(60):
                batch = await page.evaluate(GRID_JS)
                if len(batch) == last:
                    stable += 1
                    if stable >= 4:
                        break
                else:
                    stable, last = 0, len(batch)
                await page.mouse.wheel(0, 2200)
                await page.wait_for_timeout(500)
                await page.evaluate(
                    "() => { const g = document.querySelectorAll('#gridItemRoot');"
                    " if (g.length) g[g.length-1].scrollIntoView({block:'center'}); }")
                await page.wait_for_timeout(450)
            for it in await page.evaluate(GRID_JS):
                if it["rank"] and it["asin"] not in items:
                    items[it["asin"]] = it
            log(f"  第 {pg} 页累计 {len(items)} 条")
            if len(items) >= want:
                break
        await browser.close()
    return sorted([v for v in items.values() if v["rank"]], key=lambda x: x["rank"])[:want]


def stage_fetch(node_id, workdir, date_s, want=100):
    d = os.path.join(workdir, "data", date_s)
    os.makedirs(d, exist_ok=True)
    log("① 抓取榜单 …")
    asyncio.run(prime_us_session())          # 先落一个带美国收货地址的会话，价格才完整
    items = asyncio.run(_scrape_grid(node_id, want=want))
    log(f"  榜单 {len(items)} 条，排名 {items[0]['rank']}-{items[-1]['rank']}")
    for it in items:
        it["title"] = html.unescape(it.get("title") or "")
    # 原始榜单先落 raw_items.json：抓取中断时不会把「只有 rank/asin」的半成品写成 items.json
    path = os.path.join(d, "items.json")
    json.dump(items, open(os.path.join(d, "raw_items.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({"date": date_s, "node": node_id, "count": len(items),
               "crawled_at": f"{dt.datetime.now():%Y-%m-%d %H:%M}（北京时间）"},
              open(os.path.join(d, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return items, path


# ---------------------------------------------------------------- 阶段 2：补品牌/价格
def _curl(url, out):
    jar = _jar_path()
    subprocess.run(["curl", "-s", "--compressed", "-m", "20", "-A", UA,
                    "-H", "Accept-Language: en-US,en;q=0.9",
                    "-b", jar if os.path.exists(jar) else COOKIE,
                    "-o", out, url], check=False)
    return os.path.exists(out) and os.path.getsize(out) > 50000


def _clean_color(v):
    """规范化商品页取到的颜色原文：去标签、去 'Color:' 前缀、压缩空白"""
    v = html.unescape(re.sub(r"<[^>]+>", " ", v or ""))
    v = re.sub(r"\s+", " ", v).strip()
    v = re.sub(r"^(?:Color|Colour)\s*[:：]\s*", "", v, flags=re.I)
    return v[:40]


def parse_color_html(h):
    """从商品页 HTML 提取「原始颜色属性」：变体选择器 > 详情表 > detail bullets。
    这是亚马逊商品自己的 Color 字段（如 'Light Hot Pink' / 'Off Black 1B'），不是推断的色系。"""
    if not h:
        return ""
    pats = [
        r'id="variation_color_name"[\s\S]{0,900}?class="selection"[^>]*>\s*([^<]{1,60}?)\s*<',
        r'id="variation_color_name"[\s\S]{0,900}?<span[^>]*>\s*([^<]{1,60}?)\s*</span>',
        r'>\s*Color\s*<[\s\S]{0,300}?<span[^>]*>\s*([^<]{1,60}?)\s*<',
        r'prodDetAttrValue"[^>]*>\s*([^<]{1,60}?)\s*<',
        r'Color\s*[:：]\s*</span>[\s\S]{0,160}?<span[^>]*>\s*([^<]{1,60}?)\s*<',
    ]
    for pat in pats:
        m = re.search(pat, h, re.S)
        if m:
            v = _clean_color(m.group(1))
            # 过滤明显不是颜色的值（尺寸、材质、纯数字以外的型号串）
            if v and not re.match(r'^(?:\d+(?:\.\d+)?\s*(?:inch|in|cm|mm|oz|g)\b|one size|small|medium|large|x-?large)$', v, re.I):
                return v
    return ""


def _parse_dp(h):
    brand = ""
    m = re.search(r'id="bylineInfo"[^>]*>(.*?)</(?:a|span)>', h, re.S)
    if m:
        brand = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    brand = re.sub(r"^Visit the\s+", "", brand)
    brand = re.sub(r"\s+Store$", "", brand)
    brand = re.sub(r"^Brand:\s*", "", brand).strip()
    price = ""
    w = re.search(r'class="a-price-whole">([\d,]+)', h)
    f = re.search(r'class="a-price-fraction">(\d+)', h)
    if w:
        price = round(float(w.group(1).replace(",", "") + "." + (f.group(1) if f else "00")), 2)
    if not price:
        m = re.search(r'"priceAmount"\s*:\s*([\d.]+)', h)
        if m:
            price = round(float(m.group(1)), 2)
    return brand, price, parse_color_html(h)


DP_JS = r"""() => {
  const q = s => { const e = document.querySelector(s); return e ? e.innerText.trim() : ''; };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  let b = q('#bylineInfo');
  b = b.replace(/^Visit the\s+/, '').replace(/\s+Store$/, '').replace(/^Brand:\s*/, '').trim();
  // 原始颜色属性：变体选择器 > 详情表 > detail bullets
  let color = '';
  const vc = document.querySelector('#variation_color_name');
  if (vc) { const sel = vc.querySelector('.selection'); color = clean(sel ? sel.innerText : vc.innerText); }
  if (!color) {
    for (const tr of document.querySelectorAll('#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr, table.a-normal.a-spacing-micro tr')) {
      const tds = tr.querySelectorAll('td');
      const key = clean((tr.querySelector('th') || tds[0] || {}).innerText);
      if (/^color$/i.test(key) && tds.length) { color = clean(tds[tds.length-1].innerText); break; }
    }
  }
  if (!color) {
    const li = [...document.querySelectorAll('#detailBullets_feature_div li')]
      .find(e => /^\s*Color\s*[:：]/i.test(e.innerText || ''));
    if (li) color = clean(li.innerText);
  }
  color = color.replace(/^Color\s*[:：]\s*/i, '').trim();
  return {brand: b, price: q('#corePrice_feature_div') || q('#corePriceDisplay_desktop_feature_div'), color: color};
}"""


async def _patch_by_browser(targets, market="amazon.com"):
    from playwright.async_api import async_playwright
    fixed = {}
    async with async_playwright() as p:
        try:
            br = await p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            br = await p.chromium.launch(headless=True)
        ctx = await br.new_context(locale="en-US", user_agent=UA,
                                   viewport={"width": 1400, "height": 1000}, **_state_kw())
        await ctx.add_cookies([{"name": "i18n-prefs", "value": "USD",
                                "domain": "." + market, "path": "/"}])
        page = await ctx.new_page()
        for asin in targets:
            try:
                await page.goto(f"https://www.{market}/dp/{asin}",
                                wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(1500)
                r = await page.evaluate(DP_JS)
                m = re.search(r"\$([\d,]+\.\d\d)", r.get("price") or "")
                fixed[asin] = {"brand": r.get("brand") or "",
                               "price": round(float(m.group(1).replace(",", "")), 2) if m else "",
                               "color": _clean_color(r.get("color") or "")}
            except Exception as e:
                log(f"    ! {asin} 浏览器补抓失败：{e}")
        await br.close()
    return fixed


def _cache_dir():
    """商品页缓存放系统临时目录：不撑大工程目录，也不会撞上工作区的批量删除保护"""
    d = os.path.join(tempfile.gettempdir(), "bsboard_cache_dp")
    os.makedirs(d, exist_ok=True)
    return d


def _prune_cache(d, max_mb=250, max_delete=30):
    """缓存超过上限时，按最旧优先删掉一部分（单次最多删 max_delete 个）"""
    try:
        files = [(os.path.getmtime(f), os.path.getsize(f), f)
                 for f in glob.glob(os.path.join(d, "*.html"))]
        total = sum(x[1] for x in files)
        if total <= max_mb * 1024 * 1024:
            return
        for _, size, f in sorted(files):
            if total <= max_mb * 1024 * 1024 or max_delete <= 0:
                break
            try:
                os.remove(f)
                total -= size
                max_delete -= 1
            except OSError:
                pass
    except Exception:
        pass


def _fetch_dp(it, cache, market, stamp):
    """单条商品页：先看当天缓存，再 curl。返回 (asin, html, ok)
    缓存 key 带日期——当天重跑免抓，跨天不串用（否则会拿昨天的价格）"""
    p = os.path.join(cache, f"{it['asin']}_{stamp}.html")
    if os.path.exists(p) and os.path.getsize(p) > 100000:
        h = open(p, encoding="utf-8", errors="ignore").read()
        if "bylineInfo" in h:
            return it["asin"], h, True
    time.sleep(random.uniform(0, 0.5))          # 打散并发，避免同一瞬间齐发
    if not _curl(f"https://www.{market}/dp/{it['asin']}", p):
        return it["asin"], "", False
    h = open(p, encoding="utf-8", errors="ignore").read()
    if "bylineInfo" not in h and "productTitle" not in h:
        return it["asin"], "", False            # 被限流，交给浏览器补
    return it["asin"], h, True


def stage_enrich(items, workdir, date_s, market="amazon.com", workers=5):
    log(f"② 补品牌与美元价（并发 {workers}）…")
    asyncio.run(prime_us_session(market))     # 复用榜单阶段建立的会话（含美国地址）
    cache = _cache_dir()
    _prune_cache(cache)
    blocked, done = [], 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_fetch_dp, it, cache, market, date_s): it for it in items}
        for fut in as_completed(futs):
            it = futs[fut]
            try:
                asin, h, ok = fut.result()
            except Exception:
                asin, h, ok = it["asin"], "", False
            if ok:
                it["brand"], it["price_usd"], it["color_raw"] = _parse_dp(h)
                it["color_dp"] = it["color_raw"]          # 商品页 Color 原文（源头，供重算时判源）
                it["price_src"] = "dp" if it["price_usd"] else ""
                it["color_src"] = "dp" if it.get("color_raw") else ""
            else:
                blocked.append(asin)
            done += 1
            if done % 20 == 0:
                log(f"  {done}/{len(items)}（{time.time() - t0:.0f}s）")
    log(f"  商品页耗时 {time.time() - t0:.0f}s，被限流 {len(blocked)} 条")

    if blocked:
        log(f"  改用浏览器补 {len(blocked)} 条 …")
        fixed = asyncio.run(_patch_by_browser(blocked, market))
        for it in items:
            r = fixed.get(it["asin"])
            if r:
                it["brand"] = it.get("brand") or r["brand"]
                if not it.get("price_usd") and r["price"]:
                    it["price_usd"], it["price_src"] = r["price"], "dp"
                if not it.get("color_raw") and r.get("color"):
                    it["color_raw"], it["color_src"] = r["color"], "dp"
                    it["color_dp"] = r["color"]

    # 兜底：商品页因「无法配送至当前地区」不显示价格 → 用榜单卡片价
    n = 0
    for it in items:
        if not it.get("price_usd"):
            gp = re.sub(r"[^\d.]", "", (it.get("grid_price") or "").replace(",", ""))
            if gp:
                it["price_usd"], it["price_src"] = round(float(gp), 2), "grid"
                n += 1
    log(f"  品牌缺失 {sum(1 for x in items if not x.get('brand'))} 条，"
        f"价格用榜单价兜底 {n} 条，"
        f"颜色属性缺失 {sum(1 for x in items if not x.get('color_raw'))} 条")
    return items


# ---------------------------------------------------------------- 阶段 3：图片
def _img_ok(path):
    """真正解码一遍，能抓出「下了一半」的截断图（curl 中断常见，8KB 左右）"""
    from PIL import Image as PILImage
    try:
        with PILImage.open(path) as im:
            im.load()
        return True
    except Exception:
        return False


def _dl_one(it, d, market="amazon.com"):
    """下载单张图：文件名 001_ASIN.jpg，坏图换规格重试。返回 (rank, ok)"""
    out = os.path.join(d, f"{it['rank']:03d}_{it['asin']}.jpg")
    it["local_img"] = out.replace("\\", "/")
    if os.path.exists(out) and os.path.getsize(out) > 3000 and _img_ok(out):
        return it["rank"], True
    m = re.search(r"/images/I/([A-Za-z0-9+\-_]+)", it.get("img") or "")
    if not m:
        return it["rank"], False
    for size in ("SL400", "SL500", "UL600"):
        subprocess.run(["curl", "-s", "--compressed", "-m", "20", "-A", UA, "-o", out,
                        f"https://m.media-amazon.com/images/I/{m.group(1)}._AC_{size}_.jpg"],
                       check=False)
        if os.path.exists(out) and os.path.getsize(out) > 3000 and _img_ok(out):
            return it["rank"], True
    if os.path.exists(out):
        os.remove(out)                  # 坏图删掉，下次重跑会重下
    return it["rank"], False


def stage_images(items, workdir, date_s, workers=8):
    log(f"③ 下载商品图（并发 {workers}）…")
    d = os.path.join(workdir, "data", date_s, "images")
    os.makedirs(d, exist_ok=True)
    t0 = time.time()
    bad = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(_dl_one, it, d) for it in items]
        for fut in as_completed(futs):
            try:
                rank, ok = fut.result()
            except Exception:
                rank, ok = -1, False
            if not ok:
                bad.append(rank)
    log(f"  图片耗时 {time.time() - t0:.0f}s，缺图 {len(bad)} 张 {bad if bad else ''}")
    return items


# ---------------------------------------------------------------- 阶段 4：颜色
# 展示用：保留亚马逊原文（如 "Light Hot Pink" / "Off Black 1B"）。
# 统计用：再归入色系（黑/金/渐变…），仅用于顶部色条。
MULTI_RE = (r"ombre|ombré|balayage|highlight|two[ -]?tone|mixed colou?r|multi[ -]?colou?r|colorful"
            r"|rainbow|gradient|peekaboo|black\s*(?:and|&|/)\s*white|white\s*(?:and|&|/)\s*black"
            r"|\bto\s+(?:black|blonde|brown|purple|blue|pink|red|silver|grey|gray|white|orange|green)\b")
FAMILIES = [
    ("白色 / 银色", r"\bwhite\b|snow white|silver|\bgrey\b|\bgray\b|icy"),
    ("黑色",        r"\bblack\b|\b1b\b|off ?black|jet black|natural black|natural colou?r"),
    ("金色",        r"\bblonde\b|\bblond\b|\bgolden\b|\b613\b|platinum|honey|sandy"),
    ("棕色",        r"\bbrown\b|chocolate|chestnut|caramel|champagne"),
    ("红 / 酒红",   r"\bred\b|auburn|ginger|copper|burgundy|wine red|cherry"),
    ("粉色",        r"\bpink\b|\brose\b|rose gold"),
    ("紫色",        r"\bpurple\b|violet|lavender|\blilac\b|magenta"),
    ("蓝色",        r"\bblue\b|navy|denim"),
    ("橙色",        r"\borange\b|tangerine"),
    ("绿色",        r"\bgreen\b|mint|emerald"),
]
# 标题兜底：抓取标题里的颜色短语原文（含修饰词，如 "Light Hot Pink" / "Mixed Blonde"）
_TONE = (r"light|dark|natural|mixed|hot|pastel|navy|snow|platinum|honey|icy|rose\s+gold|jet|sandy|ash|gloden|"
         r"strawberry|baby|deep|soft|dirty|golden|champagne|auburn|burgundy|ombre|balayage|highlight|"
         r"two[ -]?tone|off")
_BASE = (r"black|blonde|blond|brown|white|silver|grey|gray|pink|red|blue|purple|orange|green|auburn|"
         r"burgundy|copper|ginger|caramel|chestnut|chocolate|golden")
# 补充色名（商品页 Color 原文常见但 _BASE 未覆盖，如 Cherry Calico / Teal）。
# 单独用词边界匹配，避免 tan/rose 之类在 tangle 等词中误命中。
_BASE_EXTRA = (r"teal|turquoise|calico|cherry|wine|mauve|violet|lavender|lilac|olive|beige|cream|ivory|"
               r"magenta|fuchsia|coral|peach|maroon|khaki|indigo|amber|bronze|plum|pearl|mint|emerald|"
               r"tangerine|salmon|apricot|mustard")
PHRASE_RE = re.compile(rf"\b(?:(?:{_TONE})\s+){{0,2}}(?:{_BASE})(?:\s+(?:{_BASE}))?\b", re.I)
# 还没抓到商品页属性时的历史兜底（人工核实过的人发头套默认色）
COLOR_OVERRIDE = {
    "B0CG97N6SH": "Natural Color 1B", "B0BYV3JL3K": "Natural Color 1B",
    "B093S1Z28B": "Natural Color 1B", "B0FGJCTCHC": "Natural Color 1B",
    "B0D5CM8SG3": "Natural Color 1B",
}


def _clean_color_text(v):
    """无损清洗：只去掉「不属于颜色值本身」的东西。
    仅做：HTML 反转义、去标签、合并空白、去 `Color:` / `Colour:` 字段名前缀、去首尾标点。
    绝不：改大小写、按长度截断、提取短语、删尺寸/长度前缀。
    """
    v = html.unescape(v or "")
    v = re.sub(r"<[^>]+>", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    v = re.sub(r"^(?:color|colour)\s*[:：]\s*", "", v, flags=re.I)
    return v.strip(" ,-·|/")


def normalize_color(v):
    """商品页 Color 属性的完整原文，逐字保留（2026-11-09 二次修订：连大小写也不改）。

    三代规则的教训（别再走回去）：
    - v1（<=11-06）：`len(v) > 24` 触发 PHRASE_RE 取首个色短语，把 #10 的
      `Ash Rooted platinum blonde` 截成 `Platinum Blonde`，并抹掉 #45/#88 的 Highlight 挑染信号。
    - v2（11-09 下午）：取消截断，但仍逐词改大小写（`1b`->`1B`、`black`->`Black`），
      与原文每天仍有 19~21 条不一致。
    - v3（当前）：完全无损，目标 `color_raw == color_dp` 逐条 100% 相同。
    """
    return _clean_color_text(v)


def title_color_phrase(title):
    """标题兜底：只从标题里原样截取颜色短语（保留原大小写）。
    仅当商品页没有 Color 属性时才用，不参与主口径。"""
    t = html.unescape(title or "")
    m = PHRASE_RE.search(t)
    if m:
        return re.sub(r"\s+", " ", m.group(0)).strip(" ,-·|")
    # OFFBLACK 连写：先插空格匹配，再回原串取原样子串（保证不改写）
    t2 = re.sub(r"\boff(?=black)", "off ", t, flags=re.I)
    m2 = PHRASE_RE.search(t2)
    if m2:
        pat = r"\s*".join(re.escape(w) for w in m2.group(0).split())
        mm = re.search(pat, t, flags=re.I)
        return re.sub(r"\s+", " ", (mm.group(0) if mm else m2.group(0))).strip(" ,-·|/")
    m = re.search(r"\b(?:1[bB]\b|#\s?\d{1,3}(?:/\d{1,3})?|\d{1,3}/\d{1,3})\b", t)
    return m.group(0) if m else ""


def is_color_like(v):
    """判断取到的值到底是不是颜色（排除 CR6000、13x6 这类型号/尺寸误抓）"""
    if not v:
        return False
    low = v.lower()
    if re.search(_BASE, low) or re.search(rf"\b(?:{_BASE_EXTRA})\b", low) \
            or re.search(r"\b1b\b|colou?rful|multi", low):
        return True
    # nature color：卖家把 Natural Color 拼成 Nature color（2026-11-07 #92 ASIN B0D7Q24CP6 实证）
    if re.fullmatch(r"(?:natural|nature|original)\s+colou?r", low):
        return True
    # 卖家常用整串色名：Natural / Original / Skunk Stripe（黑金挑染）。整串匹配，不误命中规格文案。
    if re.fullmatch(r"natural|original|skunk\s+stripe", low):
        return True
    return False


def classify_color(raw, title=""):
    """把原始颜色描述归入色系，仅用于统计口径——表格里显示的是 raw 原文"""
    for src in (raw, title):
        if not src:
            continue
        t = html.unescape(src).lower()
        if re.search(MULTI_RE, t):
            return "渐变 / 挑染"
        hits = []
        for name, pat in FAMILIES:
            m = re.search(pat, t)
            if m:
                hits.append((m.start(), name))
        if hits:
            hits.sort()
            return hits[0][1]
    return "其他 / 未标注"


def stage_color(items):
    log("④ 颜色：取商品页 Color 属性完整原文，页面无该属性才按标题兜底 …")
    for it in items:
        dp_src = it.get("color_dp")
        has_dp = dp_src is not None and str(dp_src).strip() != ""
        if has_dp:
            # 有原文就一律用原文（不再因「不像色名」而丢弃，如 C08#BH Color / P4/30）
            raw = normalize_color(dp_src)
            it["color_src"] = "dp"
            dp_raw = raw
        else:
            # 2026-11-06 及之前没有 color_dp 字段：沿用已定稿值，不重算、不降级
            dp_raw = ""
            if it.get("color_src") == "dp" and (it.get("color_raw") or "").strip():
                raw = (it.get("color_raw") or "").strip()
            else:
                raw = title_color_phrase(it.get("title")) or COLOR_OVERRIDE.get(it.get("asin"), "")
                it["color_src"] = "title" if raw else "none"
        it["color_raw"] = raw
        # 标题里能取到的颜色短语，仅作对照字段留存，不参与看板显示
        it["color_title"] = title_color_phrase(it.get("title")) or ""
        it["color"] = classify_color(raw, it.get("title"))
    n_dp = sum(1 for x in items if x.get("color_src") == "dp")
    n_ti = sum(1 for x in items if x.get("color_src") == "title")
    n_no = sum(1 for x in items if x.get("color_src") == "none")
    n_name = sum(1 for x in items if x.get("color_src") == "dp" and is_color_like(x.get("color_raw")))
    log(f"  商品页原文 {n_dp} 条（其中本身是色名 {n_name} 条）/ 标题兜底 {n_ti} 条 / 无颜色 {n_no} 条")
    cnt = collections.Counter(it["color"] for it in items)
    log("  色系：" + "  ".join(f"{k}{v}" for k, v in cnt.most_common()))
    return items


# ---------------------------------------------------------------- 阶段 5：出表
def _col_px(w, mdw):
    return round(((256 * w + round(128 / mdw)) / 256) * mdw)


def _thumb(path, cache_dir, box=200):
    """生成 ≤box 的缩略图用于嵌入：控制「一月一文件」的体积（31 天 × 100 图）"""
    from PIL import Image as PILImage, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True      # 万一有半张图，也尽量画出来而不是留白
    if not (path and os.path.exists(path) and os.path.getsize(path) > 3000):
        return None, 0, 0
    os.makedirs(cache_dir, exist_ok=True)
    dst = os.path.join(cache_dir, os.path.basename(path))
    try:
        with PILImage.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            if max(w, h) > box:
                sc = box / max(w, h)
                im = im.resize((max(1, int(w * sc)), max(1, int(h * sc))), PILImage.LANCZOS)
                w, h = im.size
            if (not os.path.exists(dst)) or os.path.getmtime(dst) < os.path.getmtime(path):
                im.save(dst, "JPEG", quality=80)
        return dst, w, h
    except Exception:
        return None, 0, 0


def _fill_sheet(ws, items, date_s, name, node_id, crawled_at, thumb_dir):
    """把一天的数据画成一张卡片看板工作表（调用方负责建表与保存）"""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor, AnchorMarker
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from openpyxl.utils.units import pixels_to_EMU
    from openpyxl.worksheet.properties import PageSetupProperties

    L = LAYOUT
    items = sorted(items, key=lambda x: x["rank"])
    prices = [float(x["price_usd"]) for x in items if x.get("price_usd")]
    if not prices:
        raise SystemExit(
            f"{date_s} 的数据缺少品牌/价格（items.json 可能只是抓取中断留下的原始态）。"
            f"去掉 --skip-fetch 重跑一次该日期即可补全。")
    avg = sum(prices) / len(prices)
    sp = sorted(prices)
    med = sp[len(sp) // 2] if len(sp) % 2 else (sp[len(sp) // 2 - 1] + sp[len(sp) // 2]) / 2
    brands = collections.Counter(x["brand"] for x in items if x.get("brand"))
    top_brand = brands.most_common(1)[0]

    ws.sheet_properties.tabColor = "FF9900"
    ws.sheet_view.showGridLines = False

    start_col, card_cols, step = L["start_col"], L["card_cols"], L["card_step"]
    per_row, per_band = L["per_row"], L["rows_per_band"]
    last_col = start_col + (per_row - 1) * step + card_cols - 1
    card_px = _col_px(L["col_w"], L["mdw"]) * card_cols

    ws.column_dimensions["A"].width = 1.4
    for i in range(per_row):
        base = start_col + i * step
        for k in range(card_cols):
            ws.column_dimensions[get_column_letter(base + k)].width = L["col_w"]
        if i < per_row - 1:
            ws.column_dimensions[get_column_letter(base + card_cols)].width = L["gut_w"]

    thin = Side(style="thin", color=LINE)
    box = Border(left=thin, right=thin, top=thin, bottom=thin)

    # 头部三行
    for r, h in ((1, 34), (2, 18), (3, 18), (4, 26), (5, 10)):
        ws.row_dimensions[r].height = h
    c = ws.cell(row=1, column=start_col,
                value=f"Amazon US Best Sellers  ·  {name}  ·  Top {len(items)}")
    c.font = Font(name="Microsoft YaHei", size=16, bold=True, color=INK)
    c.alignment = Alignment(vertical="center")
    c = ws.cell(row=2, column=start_col,
                value=(f"榜单日期 {date_s}（美西）　|　数据源：amazon.com Best Sellers › {name}"
                       f"（node {node_id}）　|　抓取 {crawled_at}"))
    c.font = Font(name="Microsoft YaHei", size=9, color=MUTED)
    c.alignment = Alignment(vertical="center")
    c = ws.cell(row=3, column=start_col,
                value=(f"均价 ${avg:,.2f}　中位价 ${med:,.2f}　价格带 ${min(prices):,.2f} – "
                       f"${max(prices):,.2f}　品牌数 {len(brands)}　头部品牌 "
                       f"{top_brand[0]}（{top_brand[1]} 席）"))
    c.font = Font(name="Microsoft YaHei", size=9, color=MUTED)
    c.alignment = Alignment(vertical="center")
    for r in (1, 2, 3):
        ws.merge_cells(start_row=r, start_column=start_col, end_row=r, end_column=last_col)

    # 第 4 行：颜色分布色条
    cnt = collections.Counter(x["color"] for x in items)
    citems = [{"name": k, "count": cnt[k]} for k in COLOR_ORDER if cnt.get(k)]
    citems.sort(key=lambda x: (-x["count"], COLOR_ORDER.index(x["name"])))
    for it in citems:
        it["pct"] = round(it["count"] / len(items) * 100)
    gap = 100 - sum(it["pct"] for it in citems)
    if gap:
        citems[0]["pct"] += gap

    lab = ws.cell(row=4, column=start_col, value="色系分布")
    lab.font = Font(name="Microsoft YaHei", size=8.5, bold=True, color=MUTED)
    lab.alignment = Alignment(horizontal="right", vertical="center")
    chip = start_col + 1
    for it in citems:
        fill, fg = STYLE_COLOR.get(it["name"], STYLE_COLOR["其他 / 未标注"])
        cc0 = ws.cell(row=4, column=chip,
                      value=f"{COLOR_SHORT.get(it['name'], it['name'])} {it['count']} · {it['pct']}%")
        cc0.font = Font(name="Microsoft YaHei", size=8.5, bold=True, color=fg)
        cc0.alignment = Alignment(horizontal="center", vertical="center")
        for k in range(card_cols):
            cc = ws.cell(row=4, column=chip + k)
            cc.fill = PatternFill("solid", fgColor=fill)
            cc.border = Border(
                left=Side(style="thin", color="FFFFFFFF") if k == 0 else None,
                right=Side(style="thin", color="FFFFFFFF") if k == card_cols - 1 else None,
                top=Side(style="thin", color="FFFFFFFF"),
                bottom=Side(style="thin", color="FFFFFFFF"))
        ws.merge_cells(start_row=4, start_column=chip, end_row=4, end_column=chip + card_cols - 1)
        chip += card_cols

    # 卡片
    r_head = L["header_rows"]
    for idx, d in enumerate(items):
        band, pos = divmod(idx, per_row)
        r0 = r_head + band * per_band
        c0 = start_col + pos * step
        ws.row_dimensions[r0].height = L["img_row_h"]
        ws.row_dimensions[r0 + 1].height = 21      # 排名 / 价格
        ws.row_dimensions[r0 + 2].height = 21      # 品牌
        # 颜色行高度按原文长度动态算（见下方 cc0），这里不预设固定值
        ws.row_dimensions[r0 + 4].height = 18      # ASIN
        ws.row_dimensions[r0 + 5].height = 12      # 卡片间距

        tp, iw, ih = _thumb(d.get("local_img"), thumb_dir)
        if tp:
            sc = min(L["img_max_w"] / iw, L["img_max_h"] / ih)
            w, h = int(iw * sc), int(ih * sc)
            xi = XLImage(tp)
            xi.width, xi.height = w, h
            an = OneCellAnchor()
            an._from = AnchorMarker(col=c0 - 1, colOff=pixels_to_EMU(max(0, (card_px - w) // 2)),
                                    row=r0 - 1,
                                    rowOff=pixels_to_EMU(max(2, (int(L["img_row_h"] * 4 / 3) - h) // 2)))
            an.ext = XDRPositiveSize2D(pixels_to_EMU(w), pixels_to_EMU(h))
            xi.anchor = an
            ws.add_image(xi)

        ws.cell(row=r0, column=c0).fill = PatternFill("solid", fgColor=CARD_BG)

        rk = ws.cell(row=r0 + 1, column=c0, value=f"#{d['rank']}")
        rk.font = Font(name="Arial", size=12, bold=True,
                       color=(TOP3 if d["rank"] <= 3 else MUTED))
        rk.alignment = Alignment(horizontal="center", vertical="center")

        # 缺价时写「—」而不是 0：0 会被当成真实价格，# 号位也不该出现
        pv = d.get("price_usd")
        pc = ws.cell(row=r0 + 1, column=c0 + 1, value=float(pv) if pv else "—")
        pc.number_format = '"$"#,##0.00'
        pc.font = Font(name="Arial", size=10.5, bold=True, color=INK)
        pc.alignment = Alignment(horizontal="center", vertical="center")

        bc = ws.cell(row=r0 + 2, column=c0, value=d.get("brand") or "—")
        bc.font = Font(name="Microsoft YaHei", size=10, bold=True, color=INK)
        bc.alignment = Alignment(horizontal="center", vertical="center")
        ws.merge_cells(start_row=r0 + 2, start_column=c0, end_row=r0 + 2, end_column=c0 + 1)

        # 颜色：显示商品页 Color 属性的原文（如 "Light Hot Pink" / "Off Black 1B"）
        raw = (d.get("color_raw") or "").strip() or "—"
        fill_c, _ = STYLE_COLOR.get(d.get("color"), STYLE_COLOR["其他 / 未标注"])
        try:
            from openpyxl.cell.rich_text import CellRichText, TextBlock
            from openpyxl.cell.text import InlineFont
            cc_val = CellRichText([
                TextBlock(InlineFont(rFont="Arial", sz=9, color=fill_c), "● "),
                TextBlock(InlineFont(rFont="Microsoft YaHei", sz=8.8, color=MUTED), raw),
            ])
        except Exception:
            cc_val = f"● {raw}"
        cc0 = ws.cell(row=r0 + 3, column=c0, value=cc_val)
        cc0.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        # 原文可能很长（如 Pre-Style Roll Curl｜Black、Silver+Leopard+Black (Pack of 3)）：
        # 按显示宽度折行并撑高该行，保证完整原文可见、不被裁掉
        _w = sum(2 if ord(ch) > 127 else 1 for ch in raw)
        _lines = min(4, max(1, -(-_w // 24)))
        ws.row_dimensions[r0 + 3].height = 13 * _lines + 4
        ws.merge_cells(start_row=r0 + 3, start_column=c0, end_row=r0 + 3, end_column=c0 + 1)

        ac = ws.cell(row=r0 + 4, column=c0, value=d["asin"])
        ac.hyperlink = f"https://www.amazon.com/dp/{d['asin']}"
        ac.font = Font(name="Arial", size=9, color="FF0563C1", underline="single")
        ac.alignment = Alignment(horizontal="center", vertical="center")
        ws.merge_cells(start_row=r0 + 4, start_column=c0, end_row=r0 + 4, end_column=c0 + 1)

        for rr in range(r0, r0 + 5):
            for cc in (c0, c0 + 1):
                cell = ws.cell(row=rr, column=cc)
                cell.border = box
                if cell.fill.fgColor.rgb in (None, "00000000"):
                    cell.fill = PatternFill("solid", fgColor=CARD_BG)

        if d["rank"] <= 3:      # 前三名橙色描边
            for rr in range(r0, r0 + 5):
                for cc in (c0, c0 + 1):
                    ws.cell(row=rr, column=cc).border = Border(
                        left=Side(style="thin", color=AMZ) if cc == c0 else None,
                        right=Side(style="thin", color=AMZ) if cc == c0 + 1 else None,
                        top=Side(style="thin", color=AMZ) if rr == r0 else None,
                        bottom=Side(style="thin", color=AMZ) if rr == r0 + 4 else None)

    # 底部说明
    note_row = r_head + ((len(items) - 1) // per_row + 1) * per_band + 1
    for i, t in enumerate([
        "说明｜每张卡片 = 一个商品：商品图 + 榜单排名 + 售价（USD）+ 品牌 + 颜色 + ASIN（点击跳转亚马逊商品页）。",
        "说明｜颜色取亚马逊商品页 Color 属性的原文（如 Light Hot Pink、Off Black 1B），未经改写；"
        "个别商品无该属性时取标题中的颜色表述；顶部色条是同一批原文归入色系后的统计。",
        "说明｜排名为类目畅销榜自然位次，非搜索排名；价格取商品页实时售价，个别商品因配送区域限制不显示价格时取榜单卡片价。",
        "说明｜每日抓取后新建一个以日期命名的表，历史表保留，可做榜单变动对比（新上榜 / 排名跃升 / 掉榜）。",
    ]):
        c = ws.cell(row=note_row + i, column=start_col, value=t)
        c.font = Font(name="Microsoft YaHei", size=8.5, color=MUTED)
        ws.merge_cells(start_row=note_row + i, start_column=start_col,
                       end_row=note_row + i, end_column=last_col)

    ws.freeze_panes = f"A{r_head}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)


def _slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def _verify(path, expects):
    """expects: {工作表名: 卡片数}"""
    from openpyxl import load_workbook
    wb = load_workbook(path)
    for sheet, n in expects.items():
        ws = wb[sheet]
        imgs = len(ws._images)
        links = sum(1 for r in ws.iter_rows() for c in r if c.hyperlink)
        assert imgs == n, f"[{sheet}] 图片数 {imgs} != {n}"
        assert links == n, f"[{sheet}] 超链数 {links} != {n}"
    return True


def stage_build(items, workdir, date_s, name, node_id, crawled_at):
    """单日一个文件（兼容旧用法）"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = date_s
    thumb_dir = os.path.join(workdir, "data", date_s, "images", "_thumb")
    _fill_sheet(ws, items, date_s, name, node_id, crawled_at, thumb_dir)

    out_dir = os.path.join(workdir, "output")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"Amazon_BestSellers_{_slug(name)}_{date_s}.xlsx")
    wb.save(out)
    _verify(out, {date_s: len(items)})
    log(f"⑤ 出表完成：{out}（{len(items)} 图 / {len(items)} 链）")
    return out


def stage_build_monthly(workdir, month, name, node_id, upto=None, out_path=None):
    """
    一个月一个文件：把当月所有 data/<日期>/items.json 重建成一本多工作表工作簿。
    为什么要重建而不是「打开上月文件追加表」：openpyxl 读回 xlsx 会丢掉已嵌入图片。
    """
    from openpyxl import Workbook
    data_root = os.path.join(workdir, "data")
    dates = []
    if os.path.isdir(data_root):
        for d in sorted(os.listdir(data_root)):
            if re.fullmatch(rf"{month}-\d\d", d) and \
               os.path.exists(os.path.join(data_root, d, "items.json")):
                dates.append(d)
    if upto:
        dates = [d for d in dates if d <= upto]
    if not dates:
        raise SystemExit(f"{month} 没有任何数据快照（data/{month}-*/items.json），无可出表")

    log(f"⑤ 汇总 {month}：{len(dates)} 天 → {dates[0]} … {dates[-1]}")
    wb = Workbook()
    wb.remove(wb.active)
    expects = {}
    for d in dates:
        items = json.load(open(os.path.join(data_root, d, "items.json"), encoding="utf-8"))
        items = stage_color(items)          # 幂等：确保颜色原文已归一化（旧数据/手工改动也能纠正）
        meta_p = os.path.join(data_root, d, "meta.json")
        crawled = ""
        if os.path.exists(meta_p):
            crawled = json.load(open(meta_p, encoding="utf-8")).get("crawled_at", "")
        if not crawled:
            crawled = dt.datetime.fromtimestamp(
                os.path.getmtime(os.path.join(data_root, d, "items.json"))).strftime(
                "%Y-%m-%d %H:%M（北京时间）")
        ws = wb.create_sheet(title=d)
        _fill_sheet(ws, items, d, name, node_id, crawled,
                    os.path.join(data_root, d, "images", "_thumb"))
        expects[d] = len(items)
        log(f"    + {d}：{len(items)} 张卡")

    wb.active = wb.sheetnames.index(dates[-1])       # 默认停在最新一天
    out_dir = os.path.join(workdir, "output")
    os.makedirs(out_dir, exist_ok=True)
    out = out_path or os.path.join(out_dir, f"Amazon_BestSellers_{_slug(name)}_{month}.xlsx")
    wb.save(out)
    _verify(out, expects)
    size_mb = os.path.getsize(out) / 1024 / 1024
    log(f"⑤ 月度表完成：{out}（{len(dates)} 个工作表 / {size_mb:.1f} MB）")
    return out


# ---------------------------------------------------------------- 工作目录
def _default_workdir():
    """同一份代码在两种位置都能跑对，因此技能版与项目版可以字节一致：
    - 项目内  <root>/workflow/pipeline/run.py  ->  <root>
    - 技能内  <skill>/scripts/run.py           ->  当前工作目录
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(os.path.dirname(here)) == "workflow":
        return os.path.dirname(os.path.dirname(here))
    return os.getcwd()


def _sync_hint(workdir):
    """若项目里另有一份同源脚本且与自身不一致，提示同步（不阻断运行）。
    解决「在项目里改了脚本、忘了同步到 Skill」这个隐性问题。"""
    me = os.path.abspath(__file__)
    ws = os.path.join(os.path.abspath(workdir), "workflow", "pipeline", "run.py")
    if not os.path.exists(ws) or os.path.abspath(ws) == me:
        return
    try:
        same = open(me, "rb").read() == open(ws, "rb").read()
    except OSError:
        return
    if not same:
        log("⚠️ 检测到两处流水线脚本不一致（项目改了但 Skill 未同步，或反之）：")
        log(f"   当前运行：{me}")
        log(f"   项目副本：{ws}")
        log("   -> 同步并升版本：python workflow/tools/sync_skill.py")


# ---------------------------------------------------------------- 并发保护
def _acquire_lock(workdir):
    lock = os.path.join(workdir, "data", ".run.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    if os.path.exists(lock):
        age = time.time() - os.path.getmtime(lock)
        if age < 3 * 3600:
            raise SystemExit(f"另一个抓取任务正在运行（锁文件 {lock}，{age / 60:.0f} 分钟前创建）。"
                             f"确认没有在跑就删掉它再试。")
    with open(lock, "w") as f:
        f.write(str(os.getpid()))
    return lock


def _release_lock(lock):
    try:
        os.remove(lock)
    except OSError:
        pass


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="亚马逊 Best Sellers 每日看板流水线")
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="表名/目录用日期，默认今天")
    ap.add_argument("--node", default="702381011", help="类目节点 ID（Hair Replacement Wigs=702381011）")
    ap.add_argument("--name", default="Hair Replacement Wigs", help="类目英文名")
    ap.add_argument("--workdir", default=None,
                    help="工程根目录（含 data/ 与 output/），默认：项目内=项目根，技能内=当前目录")
    ap.add_argument("--version", action="version", version=f"amazon-bsr-board {__version__}")
    ap.add_argument("--want", type=int, default=100, help="抓多少名")
    ap.add_argument("--skip-fetch", action="store_true", help="复用 data/<date>/items.json，只重排表")
    ap.add_argument("--monthly", action="store_true",
                    help="一个月一个文件：把当月所有日期汇总成一本多工作表工作簿")
    ap.add_argument("--month", default=None, help="月份 YYYY-MM，默认取 --date 的月份")
    ap.add_argument("--workers", type=int, default=5,
                    help="商品页并发数（默认 5；网络差可降到 3，被限流多可降到 2）")
    ap.add_argument("--no-html", action="store_true",
                    help="不生成 HTML 看板（默认出表时顺手生成当月 HTML，可切日期）")
    args = ap.parse_args()

    workdir = os.path.abspath(args.workdir or _default_workdir())
    log(f"amazon-bsr-board v{__version__}")
    _sync_hint(workdir)
    lock = _acquire_lock(workdir)
    crawled_at = f"{dt.datetime.now():%Y-%m-%d %H:%M}（北京时间）"
    json_path = os.path.join(workdir, "data", args.date, "items.json")

    if args.skip_fetch:
        if not os.path.exists(json_path):
            raise SystemExit(f"{json_path} 不存在，去掉 --skip-fetch 重新抓取")
        items = json.load(open(json_path, encoding="utf-8"))
        miss = sum(1 for x in items if not x.get("brand") or not x.get("price_usd"))
        if miss:
            raise SystemExit(
                f"{json_path} 有 {miss}/{len(items)} 条缺品牌或价格（疑似抓取中断留下的原始态）。"
                f"去掉 --skip-fetch 重跑 {args.date} 补全后再出表。")
        log(f"复用 {json_path}（{len(items)} 条）")
    else:
        items, json_path = stage_fetch(args.node, workdir, args.date, args.want)
        if len(items) < args.want:
            raise SystemExit(f"只抓到 {len(items)} 条（期望 {args.want}），"
                             f"通常是网络/反爬导致榜单未加载完，请重跑一次")
        items = stage_enrich(items, workdir, args.date, workers=args.workers)
        items = stage_images(items, workdir, args.date, workers=max(4, args.workers + 4))
        items = stage_color(items)
        json.dump(items, open(json_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump({"date": args.date, "node": args.node, "name": args.name,
                   "count": len(items), "crawled_at": crawled_at},
                  open(os.path.join(os.path.dirname(json_path), "meta.json"), "w",
                       encoding="utf-8"), ensure_ascii=False, indent=1)

    items = stage_color(items)

    month = args.month or args.date[:7]
    try:
        if args.monthly:
            out = stage_build_monthly(workdir, month, args.name, args.node, upto=args.date)
            log(f"完成：{out}")
        else:
            stage_build(items, workdir, args.date, args.name, args.node, crawled_at)

        if not args.no_html:
            try:
                from render_html import render as render_board
                render_board(workdir, month=month, name=args.name, node=args.node)
            except Exception as e:      # HTML 只是附加产物，失败不影响表格交付
                log(f"HTML 看板生成失败（表格不受影响）：{e}")
    finally:
        _release_lock(lock)


if __name__ == "__main__":
    main()
