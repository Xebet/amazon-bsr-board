# -*- coding: utf-8 -*-
"""
把当月每天的 data/<日期>/items.json 渲染成**一个**单文件 HTML 看板（顶部按日期切换）。

    python render_html.py --month 2026-10                    # 当月全部日期
    python render_html.py --date 2026-10-21                  # 只到这一天（月份自动取）
    python render_html.py --month 2026-10 --embed            # 图片转 base64，可完全离线

产出：output/board_<slug>_<YYYY-MM>.html
"""
import argparse
import base64
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                                  # workflow/ 或 <skill>/
TPL = os.path.join(ROOT, "templates", "board.html")
if not os.path.exists(TPL):                                   # 技能包内模板位置
    TPL = os.path.join(ROOT, "assets", "board.html")


def _slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


RANK_LABELS = {"bestsellers": "Best Sellers", "new-releases": "New Releases",
               "movers-and-shakers": "Movers & Shakers",
               "most-wished-for": "Most Wished For"}
BOARD_PREFIX = {"bestsellers": "board", "new-releases": "board_newreleases",
                "movers-and-shakers": "board_movers",
                "most-wished-for": "board_wished"}

# 只看显示：站点 → 页面标题里用的地名缩写
MARKET_LABELS = {"amazon.com": "US", "amazon.co.uk": "UK", "amazon.ca": "CA", "amazon.de": "DE",
                 "amazon.fr": "FR", "amazon.it": "IT", "amazon.es": "ES", "amazon.nl": "NL",
                 "amazon.se": "SE", "amazon.pl": "PL", "amazon.co.jp": "JP", "amazon.com.au": "AU",
                 "amazon.in": "IN", "amazon.com.mx": "MX", "amazon.com.br": "BR",
                 "amazon.sg": "SG", "amazon.ae": "AE"}


def _data_url(path):
    if not (path and os.path.exists(path)):
        return ""
    ext = (os.path.splitext(path)[1].lower().lstrip(".") or "jpg")
    with open(path, "rb") as f:
        return f"data:image/{'jpeg' if ext == 'jpg' else ext};base64," + \
               base64.b64encode(f.read()).decode()


def _prep_item(it, embed):
    """统一成精简结构：图片走图床并带多级回退链，或转 base64"""
    out = {k: it.get(k) for k in ("rank", "asin", "brand", "price_usd",
                                 "color", "color_raw", "color_src", "dot_color")}
    if embed:
        out["img"] = _data_url(it.get("local_img")) or it.get("img") or ""
        out["chain"] = []
        return out
    orig = it.get("img") or ""
    m = re.search(r"/images/I/([A-Za-z0-9+\-_]+)", orig)
    if m:
        base = f"https://m.media-amazon.com/images/I/{m.group(1)}"
        out["img"] = f"{base}._AC_SL400_.jpg"
        out["chain"] = [f"{base}._AC_SL500_.jpg", f"{base}._AC_UL600_.jpg", orig]
    else:
        out["img"], out["chain"] = orig, []
    return out


def collect(workdir, month, upto=None):
    """收集当月所有日期的快照，返回 [(日期, 抓取时间, items), ...]（按日期升序）"""
    data_root = os.path.join(workdir, "data")
    days = []
    if not os.path.isdir(data_root):
        return days
    for d in sorted(os.listdir(data_root)):
        if not re.fullmatch(rf"{month}-\d\d", d):
            continue
        p = os.path.join(data_root, d, "items.json")
        if not os.path.exists(p):
            continue
        if upto and d > upto:
            continue
        items = json.load(open(p, encoding="utf-8"))
        crawled = ""
        mp = os.path.join(data_root, d, "meta.json")
        if os.path.exists(mp):
            crawled = json.load(open(mp, encoding="utf-8")).get("crawled_at", "")
        days.append((d, crawled, items))
    return days


def render(workdir, month=None, date=None, name="Hair Replacement Wigs",
           node="702381011", embed=False, out_path=None,
           market="amazon.com", currency="USD", rank_type="bestsellers"):
    workdir = os.path.abspath(workdir)
    month = month or (date or "")[:7]
    if not re.fullmatch(r"\d{4}-\d\d", month or ""):
        raise SystemExit("需要 --month YYYY-MM 或 --date YYYY-MM-DD")
    days = collect(workdir, month, upto=date)
    if not days:
        raise SystemExit(f"{month} 没有可渲染的数据（data/{month}-*/items.json 都不存在）")

    payload = {
        "category": name,
        "node": node,
        "month": month,
        "market": market,
        "market_label": MARKET_LABELS.get(market, ""),
        "currency": currency,
        "rank_type": rank_type,
        "rank_label": RANK_LABELS.get(rank_type, "Best Sellers"),
        "dates": [{"date": d, "crawled_at": c or "—",
                   "items": [_prep_item(it, embed) for it in items]}
                  for d, c, items in days],
    }
    html = open(TPL, encoding="utf-8").read()
    html = html.replace("__TITLE__", f"{name} {month}")
    html = html.replace("__DATA__", json.dumps(payload, ensure_ascii=False))

    out_dir = os.path.join(workdir, "output")
    os.makedirs(out_dir, exist_ok=True)
    out = out_path or os.path.join(
        out_dir, f"{BOARD_PREFIX.get(rank_type, 'board')}_{_slug(name)}_{month}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    size_mb = os.path.getsize(out) / 1024 / 1024
    print(f"saved {out} ({len(days)} 天 / "
          f"{sum(len(x['items']) for x in payload['dates'])} 卡 / {size_mb:.1f} MB / "
          f"{'内嵌图' if embed else '图床图'})")
    return out


def _default_workdir():
    """同一份代码在两种位置都能跑对，因此技能版与项目版可以字节一致：
    - 项目内  <root>/workflow/pipeline/<脚本>.py  ->  <root>
    - 技能内  <skill>/scripts/<脚本>.py           ->  当前工作目录
    """
    if os.path.basename(ROOT) == "workflow":
        return os.path.dirname(ROOT)
    return os.getcwd()


def main():
    ap = argparse.ArgumentParser(description="把当月每天的数据渲染成一个可切日期的 HTML 看板")
    ap.add_argument("--month", default=None, help="月份 YYYY-MM（与 --date 二选一）")
    ap.add_argument("--date", default=None, help="只渲染到该日期（YYYY-MM-DD），月份自动推断")
    ap.add_argument("--workdir", default=None,
                    help="工程根目录（含 data/ 与 output/），默认：项目内=项目根，技能内=当前目录")
    ap.add_argument("--name", default="Hair Replacement Wigs")
    ap.add_argument("--node", default="702381011")
    ap.add_argument("--market", default="amazon.com", help="站点域名，决定 ASIN 链接指向哪个站点")
    ap.add_argument("--currency", default="USD", help="币种代码，仅用于标注")
    ap.add_argument("--rank-type", default="bestsellers",
                    choices=list(RANK_LABELS),
                    help="榜单类型（与出表时的 --rank-type 保持一致）")
    ap.add_argument("--embed", action="store_true", help="图片转 base64 内嵌（可离线，文件更大）")
    args = ap.parse_args()
    render(args.workdir or _default_workdir(), month=args.month, date=args.date,
           name=args.name, node=args.node, embed=args.embed, market=args.market,
           currency=args.currency, rank_type=args.rank_type)


if __name__ == "__main__":
    main()
