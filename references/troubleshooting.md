# 执行手册 SOP · 从零到交付

> 给「照着做的人 / Agent」看。每一步都写了为什么，以及踩过的坑。
> 只想一键跑：`python pipeline/run.py --date 2026-10-21`，本文用于理解与排错。

## 0. 环境准备（一次性）

```bash
# Python 3.11+，装三个包就够了（不需要下载 chromium，直接用系统 Edge）
pip install openpyxl pillow playwright
```

- Playwright 用 `channel="msedge"` 驱动系统 Edge；没装 Edge 会自动回退到 self-hosted chromium。
- Excel 只在校验环节用到（可选，用于读取真实几何尺寸）。

## 1. 定位类目节点 ID

```
https://www.amazon.com/gp/bestsellers/beauty/   → 逐层点进目标类目
地址栏 .../zgbs/beauty/<节点ID>  → 这个数字就是 node ID
```

验证：打开 `https://www.amazon.com/gp/bestsellers/beauty/<node>/`，页面 `<title>` 里应出现类目名。
一旦拿到就直接用 node 拼 URL，**不要用类目名猜 URL**（`Best-Sellers-Beauty-Hair-Replacement-Wigs` 这类路径经常 404 或跳到别的类目）。

## 2. 抓榜单（坑最多的一步）

```bash
# 直接用浏览器，别用 requests
GET https://www.amazon.com/gp/bestsellers/beauty/<node>/?pg=1
GET https://www.amazon.com/gp/bestsellers/beauty/<node>/?pg=2
```

| 坑 | 现象 | 对策 |
|---|---|---|
| 每页只直出 30 条 | pg=1 只有 #1-30，pg=2 从 #51 开始，中间的 31-50 不见 | 必须滚动触发 AJAX 懒加载；**连续 4 轮数量不增长**才停（只等 1 轮就 break 会漏 40 条） |
| `?pg=3` 返回 400 | 榜单只支持 2 页 | 只取 pg=1、2，靠滚动补满 |
| 价格是人民币 | 显示 `CNY 86.73` | 必须加 cookie `i18n-prefs=USD` |
| 抓完不校验 | 静默少了 40 条 | 结束必须断言排名 1-100 无缺号，不足就重跑 |

核心提取选择器：`#gridItemRoot`（卡片）→ `.zg-bdg-text`（排名）→ `a[href*="/dp/"]`（ASIN）→
`span[class*="p13n-sc-price"]`（价格）→ `img.src`（图片）。

## 3. 补品牌与价格（榜单里没有品牌）

逐条请求 `https://www.amazon.com/dp/<ASIN>`：

- 品牌：`id="bylineInfo"` → 文本形如 `Brand: MapofBeauty` 或 `Visit the LANCAINI Store`，去掉前后缀
- 价格：`class="a-price-whole"` + `class="a-price-fraction"` 拼成浮点数

**通道选择**：用 `curl`。`urllib` / `requests` 会被识别为爬虫，返回 3-4KB 的空壳页（HTTP 200 但不含内容）——这个坑很容易误判成"页面改版"。

| 坑 | 现象 | 对策 |
|---|---|---|
| 请求约 70 次后被限流 | 返回 3-5KB 页面、无 `bylineInfo` | 被限流的收集起来，改用 Playwright 逐条补（浏览器指纹能过） |
| 配送区域限制 | 页面上 `This item cannot be shipped to your selected delivery location`，价格为空 | 用榜单卡片价兜底，数据里标 `price_src=grid` |
| 想改成美国地址解锁 | 弹窗 `#GLUXZipUpdateInput` 结构常变，容易失败 | 不值得依赖，用兜底价更稳 |
| 品牌名不干净 | `Visit the X Store` / `Brand: X` | 正则去掉 `^Visit the`、`\s+Store$`、`^Brand:` |

## 4. 图片

```python
# 从榜单 img.src 里取出图片 ID
ID = re.search(r"/images/I/([A-Za-z0-9+\-_]+)", url).group(1)
f"https://m.media-amazon.com/images/I/{ID}._AC_SL400_.jpg"   # 失败退 SL500 → UL600
```

文件命名 `<排名三位>_<ASIN>.jpg`，方便对账。**必须落盘再嵌表**，不能直接写外链（Excel 里外链图在别人电脑上会变空白）。

## 5. 颜色（完全无损，逐字照抄）

规则见 `references/layout-spec.md §4`。要点：

- **卡片显示商品页 Color 字段的完整字符串**：不改大小写、不截断、不摘要。看起来不像颜色的值（`200%Density 13x4 Lace Front Wig`、`CR6000`、`Silver+Leopard+Black (Pack of 3)`）也照原样上卡片，色系统计归「其他 / 未标注」
- 取数三处依次尝试：`#variation_color_name .selection` → 详情表 `Color` 行 → `#detailBullets` 的 `Color:` 项
- 批量补历史数据用 `<skill>/scripts/enrich_color.py`（浏览器通道，4 并发，125 个 ASIN 约 2 分钟，结果缓存在 `data/_color_attrs.json`，重跑不重复抓）
- 验收看**逐条一致率**：页面有 Color 字段的条目，`color_raw` 必须与 `color_dp` 100% 相同。日志里 `商品页原文 N 条（其中本身是色名 M 条）/ 标题兜底 X 条 / 无颜色 Y 条`——N 是「页面有该字段」，M 才是「看起来是色名」，别把 N 当质量指标
- **不要"顺手纠正"**：`Nutural Black`（natural 拼错）、`Sliver to Purple`（silver 拼错）、`1b`（小写）、`OFF BLACK`（全大写）都是亚马逊上的原样写法，改了就对不上页面
- `color_dp` 只从 2026-11-07 起才有；更早的日子原文已不可恢复，脚本沿用已定稿值、不重算。要补只能用 `enrich_color.py` 重新抓商品页

## 6. 出表

版式参数照抄 `references/layout-spec.md §3` 或直接读 `pipeline/run.py` 顶部的 `LAYOUT`。

两个必须记住的细节：

1. **列宽→像素换算**：`px = round(((256*w + round(128/mdw))/256)*mdw)`，`mdw` 是本机默认字体的最大数字宽度。
   本机实测 `mdw=8`，所以列宽 11.2 字符 = **90px**，一张卡（2 列）= **180px**。
   按 `11.2*7+5=83px` 估算会让图片整体偏左约 11px。
2. **价格单元格不要设缩进**（`Alignment(indent=1)`），否则实际可用宽度不够，Excel 直接显示 `#######`。

## 7. 校验（这一步别跳）

| 手段 | 查什么 | 命令/做法 |
|---|---|---|
| 结构断言 | 表名、图片数、超链数、排名完整性 | `openpyxl` 读回来 `len(ws._images)`、`c.hyperlink` 计数 |
| 几何断言 | 图片是否真居中 | Excel COM：`Range.Left/Width`、`Shape.Left/Width`，比对 `(卡宽-图宽)/2`，误差 < 1pt |
| 视觉抽查 | 版式观感 | Excel COM `Workbook.SaveAs(path, 44)` 导出 HTML → 浏览器截图 |

注意：`ExportAsFixedFormat` 导 PDF 在本机会让 Excel 进程崩掉，别用。
另外 HTML 导出会把列宽压缩、图片按绝对尺寸叠加，**看图只能判观感，几何结论必须用 COM 读数**。

## 7.4 性能与并发

单次请求往返 5–12s 是主要成本，页面大小（400KB vs 2MB）影响很小。因此：

- 商品页抓取默认**并发 5**（`--workers 5`），100 条从 ~10 分钟降到 ~2 分钟
- 图片下载并发 9，100 张从 ~2.5 分钟降到 ~25 秒
- 并发 6–8 更快但更容易触发限流；被限流的会转浏览器逐条补，反而拖慢，**默认 5 是折中**
- 榜单滚动等待从 850+850ms 收窄到 500+450ms，省 10s 左右
- 出表成功后清理 `data/cache_dp`（单次可达 150MB）

## 7.5 运行安全（并发 / 中断恢复）

- **并发锁**：`data/.run.lock`。已有任务在跑时新任务直接退出（
  `另一个抓取任务正在运行`），避免定时任务与手动运行互相覆盖。锁超过 3 小时视为失效。
- **中断保护**：抓取阶段只写 `data/<日期>/raw_items.json`（只有 rank/asin/title/img），
  补全品牌价格后才写 `items.json`。所以 `items.json` 缺失或被写成原始态时，
  用 `--skip-fetch` 会直接报错并提示「去掉 --skip-fetch 重跑」。
- **缓存清理**：出表成功后自动删除 `data/cache_dp`（单次抓取的 HTML 缓存可达 150MB+）。
- **从成品反推数据**：如果 `items.json` 丢了但 xlsx 还在，可以从表格里读回
  rank / 品牌 / 价格 / ASIN（卡片位置固定），再配 `raw_items.json` 的标题和图片补齐。
  注意榜单在一天内会变，跨时间点合并前先核对 ASIN 集合是否一致。

## 8. 日常复用

```bash
# 每天跑一次：汇总进「当月一个文件」，每天一张表（推荐）
python pipeline/run.py --date 2026-10-22 --monthly

# 单日单独出一个文件
python pipeline/run.py --date 2026-10-22

# 改了版式只想重排，不重抓
python pipeline/run.py --date 2026-10-22 --skip-fetch

# 换类目（节点 ID 换了就够，URL 里的 beauty 段不用改）
python pipeline/run.py --node <新节点> --name "<新类目名>" --date 2026-10-22

# 换榜单类型：新品榜 / 飙升榜 / 心愿单榜（同一套卡片版式，只换 URL 与文案）
python pipeline/run.py --rank-type new-releases --node 702381011 --name "Hair Replacement Wigs" --date 2026-10-22

# 换站点（节点 ID 必须用目标站点自己的）
python pipeline/run.py --market amazon.co.uk --currency GBP --node <英站节点> --name "Hair Extensions" --date 2026-10-22

# 只要核心字段：图 / 排名 / 价格（卡片收紧，色条自动不出现）
python pipeline/run.py --fields image,rank,price --date 2026-10-22 --monthly
```

`--fields` / `--rank-type` / `--market` 只影响「怎么抓、怎么排」，不改 `data/` 里的数据结构：
`items.json` 始终是那 8 个字段，因此同一批历史数据可以用不同字段、不同版式反复重排（`--skip-fetch`），
不会产生两份互相打架的数据。

月度汇总用 `--monthly`：它把 `data/<当月>/*` 全部重建成一本工作簿，而不是往旧文件里追加
（openpyxl 读回 xlsx 会丢图片）。所以历史某天的数据要保留在 `data/` 目录里，别删。

跑完看 `data/<date>/items.json` —— 这就是跨日对比的原料：新上榜（新 ASIN）/ 排名跃升 / 掉榜，都能直接 diff 两份 json 得到。
