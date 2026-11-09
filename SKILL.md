---
name: 亚马逊BSR看板
version: 1.2.0
description: 抓取亚马逊美国站（或其他站点）Best Sellers 类目榜单前 N 名（默认 100），逐条补齐品牌、美元售价与商品页颜色属性原文（如 Light Hot Pink、Off Black 1B）、下载商品图，产出「卡片看板」式 Excel 与可切日期的 HTML：一天一张以日期命名的工作表，一个月一个文件，跨月自动新建。用于亚马逊选品、竞品榜单监控、BSR（Best Sellers Rank）追踪。触发词：亚马逊榜单、Best Sellers、BSR 看板、类目排名、Top 100 抓取、竞品榜单监控、每日榜单表、按日期出表、monthly bestseller board；或用户提供类目节点 ID，要求出带商品图、ASIN 链接、品牌、价格的排名表。
agent_created: true
---

# 亚马逊 BSR 看板（Best Sellers 每日榜单）

## 用途

抓取亚马逊某类目 Best Sellers 前 N 名（默认 100），补齐品牌、美元价与商品页颜色原文、下载商品图，一次产出两份「卡片看板」：

- **Excel**：一天一张以日期命名的工作表，一个月一个文件（跨月新建）
- **HTML**：同月一个单文件，顶部按钮切换当月任意一天，可直接发给人看

全流程脚本化、可重跑、带结构自检（图片数/超链数/排名连续性都会断言）。

## 何时使用

- 用户要「亚马逊榜单 / Best Sellers / 类目排名」Top 100，并希望出成表格
- 需要按日期持续积累榜单数据（做新上榜、排名跃升、掉榜对比）
- 需要看板/卡片样式（图片 + 排名 + 价格 + 品牌 + 颜色 + 可点击 ASIN），而不是普通行列表
- 用户提供了类目节点 ID 或类目名，要求量产式的榜单表

## 前置检查

1. `python -c "import openpyxl, PIL, playwright"` 必须通过；缺则 `pip install openpyxl pillow playwright`
2. Playwright 用系统 Edge 驱动（`channel="msedge"`），无需下载 chromium；无 Edge 会自动回退
3. 确认目标类目的节点 ID：访问 `https://www.amazon.com/gp/bestsellers/beauty/` 逐层点进，地址栏 `zgbs/<类目>/<节点ID>` 中的数字即节点 ID。**不要用类目名猜 URL**，容易跳到别的类目

## 执行

在用户工程根目录（含 `data/`、`output/`）下运行：

```bash
# 日常：抓当天 + 汇总进当月文件（推荐）
python <skill>/scripts/run.py --date YYYY-MM-DD --monthly --node <节点ID> --name "<类目英文名>"

# 只重排版式，不重抓
python <skill>/scripts/run.py --date YYYY-MM-DD --monthly --skip-fetch ...

# 单日单独出一个文件
python <skill>/scripts/run.py --date YYYY-MM-DD --node ... --name ... [--want 100] [--workdir <目录>]

# HTML 看板：出表时自动生成（月度一个文件、顶部切日期）；也可单独跑
python <skill>/scripts/render_html.py --month YYYY-MM [--embed]
```

`--workdir` 省略时默认当前工作目录。脚本退出码非 0 即失败（抓不满 N 条会主动报错，不会静默出半张表）。

**耗时预期**：一次完整运行约 4 分钟（榜单 ~30s + 商品页并发 5 约 2min + 图片并发 9 约 25s + 出表 5s）。
瓶颈是单次请求往返 5–12s，`--workers` 控制商品页并发：默认 5，调到 6–8 更快但更易被限流（限流会转浏览器逐条补，反而更慢），网络差时降到 3。

## 硬性规则（违反就会出错）

1. **币种**：所有请求带 cookie `i18n-prefs=USD`，否则价格是人民币预算价。
2. **榜单分段加载**：榜单页每页只直出 30 条，其余靠滚动触发 AJAX。必须滚到「连续多轮数量不增长」才停，结束时断言排名 1–N 连续无缺号；不足就重跑，不要用假数据补。
3. **品牌和颜色都只在商品页**：榜单卡片没有这两项，需逐条抓 `/dp/<ASIN>`：品牌取 `#bylineInfo`（去掉 `Visit the` / `Brand:` / `Store` 前后缀），价格取 `a-price-whole` + `a-price-fraction`，颜色取商品页 Color 属性原文（见下节）。
4. **通道与限速**：用 `curl`，不要用 urllib/requests（会被判爬虫返回 3–4KB 空页）。每条间隔 0.6–1.3s；连续约 70 条后会被限流，被限流的改用无头浏览器补。
5. **价格兜底**：商品页出现 "This item cannot be shipped to your selected delivery location" 时不显示价格，用榜单卡片价兜底并标 `price_src=grid`。
   - 抓取前会先 `prime_us_session()`：用 Playwright 调 `address-change.html` 把默认收货地址设成美国 ZIP（默认 `10001`，可用环境变量 `AMZ_US_ZIP` 改），落盘 `data/_amz_session.json` + `data/_amz_cookies.txt`，TTL 6 小时。**不要用点弹窗的方式设地址**（实测无效），只有 AJAX 接口有效且要回读 `#glow-ingress-line2` 校验。
   - 不做这一步，非美国 IP 下会有 30–40 条商品不显示价格（历史上因此让均价低估约 30%）。启动那次 prime 失败不必立刻干预，看最终缺价数，浏览器补抓阶段会重新 prime。
6. **图片完整性**：下载后用 `Image.load()` 真正解码校验，截断图（8KB 左右）要发现并重下；嵌表用 ≤200px 缩略图控制体积。
7. **一月一文件**：openpyxl 读回 xlsx 会**丢掉已嵌入图片**，所以不能「打开旧文件追加一张表」。必须用 `data/<当月>/*` 全部快照整体重建（`stage_build_monthly`）。副作用是幂等：同一天重跑只覆盖当天表。
8. **图片居中用真实列宽**：`px = round(((256*w + round(128/mdw))/256)*mdw)`，本机 `mdw=8` → 列宽 11.2 字符 = 90px，一张卡（2 列）= 180px。按 `11.2*7+5` 估算会让图片整体偏左约 11px。
9. **价格单元格不要设缩进**，否则实际宽度不足会显示 `#######`。
10. **卡片文字全部居中**：左对齐会让宽卡片中间出现大片空白。

## 颜色口径（完全无损，逐字照抄）

卡片上显示的是**亚马逊商品页里卖家自己写的 Color 字段的完整文本**，不是归成「黑色/粉色」的分类名，也不是截短后的摘要：

| 优先级 | 来源 | 示例 |
|---|---|---|
| 1 | 变体选择器 `#variation_color_name .selection` | `Light Hot Pink` |
| 2 | 商品详情表 `Color` 行 / detailBullets | `Off Black 1B` |
| 3 | 页面**没有** Color 字段时，取标题里的颜色短语原文 | `Mixed Blonde` |
| 4 | 都没有 | `—` |

- **只要商品页有 Color 字段，就原样完整显示**，即使它写的是规格文案（`200%Density 13x4 Lace Front Wig`、`C08#BH Color`、`CR6000`、`bob-1`、`P4/30`、`Highlight`）——这是「要能跟商品页对得上」的硬要求，不得为了好看而改写或丢弃。
- 原文里的拼写错误照留（`Nutural Black`、`Nature color`、`Gloden Blonde`），不要"顺手纠正"。
- `normalize_color` 只做**无损清洗**：去 HTML 标签、去 `Color:` 这个字段名前缀、合并连续空白、去首尾标点。
  **不得改大小写**（`1b` 就是 `1b`，`OFF BLACK` 就是 `OFF BLACK`）、**不得按长度截取**、**不得删尺寸前缀**（`Small size light blue` 照留）。
  验收标准：页面有 Color 字段时，`color_raw` 要与 `color_dp` **逐条 100% 相同**。
  历史教训（别再走回去）：v1 用 `len>24` 触发短语提取，把 `Ash Rooted platinum blonde` 截成 `Platinum Blonde`、
  把 `Brown Mix Blonde Highlights` 截成 `Brown` 并误判色系；v2 取消截断但仍改大小写，与原文每天 19~21 条不一致。
- 判源只认 `color_dp`（商品页原文快照），不看 `color_raw`，避免把上一轮的标题兜底值误标成 `dp`。
- 非色名原文（规格/货号）保留在卡片上，但色系归入「其他 / 未标注」；标题里另取的颜色存 `color_title` 字段备用，不上卡片。
- 顶部色条仍按**色系**统计（黑/金/渐变…），标签写「色系分布」，与卡片原文并存。
- 日志口径：「商品页原文 N 条（其中本身是色名 M 条）/ 标题兜底 / 无颜色」。N 通常=100，M 才是真正的色名数，别把 N 当颜色覆盖度。
- 长原文的版式：Excel 颜色行 `wrap_text=True` 并按显示宽度动态撑高（最多 4 行）；HTML 卡片允许换行，不用 `nowrap`+省略号。

补历史数据的颜色用 `scripts/enrich_color.py --dates 2026-10-21,...`（浏览器并发，结果缓存到 `data/_color_attrs.json`）。

## 双产物

每次运行产出两份，内容一致：

| 产物 | 路径 | 结构 |
|---|---|---|
| Excel | `output/Amazon_BestSellers_<slug>_<YYYY-MM>.xlsx` | 一月一文件，每天一张以日期命名的工作表 |
| HTML | `output/board_<slug>_<YYYY-MM>.html` | 一月一文件，顶部按钮切日期，默认最新一天 |

HTML 体积很小（~0.1MB/月，图片走图床）；加 `--embed` 转 base64 可离线。不想出 HTML 加 `--no-html`。

## 版式要点（细化参数见 references/layout-spec.md）

- 工作表 = 一天（月度文件里一天一张，日期升序、默认停在最新一天）；隐藏网格线、冻结第 6 行、横向打印、按页宽缩放
- 第 1–4 行：标题 / 数据源+抓取时间 / 价格与品牌统计 / **颜色分布色条**（底色=颜色，文案「名称 数量 · 占比%」，占比合计 100%）
- 第 6 行起卡片看板：每行 10 张、每卡 2 列 + 1 列间距，卡片内为 图片 / `#排名`+`$价格` / 品牌 / 颜色（原文 + 色点）/ ASIN 超链；前 3 名橙色描边
- 图像最大 126×138px，等比缩放后水平垂直居中

## 颜色归类

先判多色（ombre / highlight / two-tone / colorful / rainbow / gradient / "X to Y" / "black and white" → 渐变挑染），再按**出现位置最靠前**的单色归类（白/银 → 黑 → 金 → 棕 → 红 → 粉 → 紫 → 蓝 → 橙 → 绿）。
归类入参顺序是 `classify_color(color_raw, title)`：**先看原文 `color_raw`，原文里没有颜色词才退回标题**（所以 `Brown Mix Blonde Highlights` 现在能正确进「渐变 / 挑染」，不再被截成 `Brown` 误判棕色）。都不中则 `其他 / 未标注`——卖家把规格文案填进 Color 的条目（`CR6000`、`200%Density 13x4 Lace Front Wig` 等）就落在这里，属预期。边界条目按商品页 `Color` 人工校准，映射写在 `scripts/run.py` 的 `COLOR_OVERRIDE`。

## 版本与同步

本技能有两份同源代码：项目内 `workflow/`（开发/调试用）与本技能目录（分发用）。

- 版本号有两处，必须一致：`SKILL.md` 的 `version:` 与 `scripts/run.py` 的 `__version__`，由同步脚本统一维护
- 脚本靠 `_default_workdir()` 自适应位置：项目内 `<root>/workflow/pipeline/run.py` → 解析到 `<root>`；技能内 `<skill>/scripts/run.py` → 解析到**当前工作目录**。因此两份代码可以做到字节一致
- 运行时会自检：若当前目录下存在 `workflow/pipeline/run.py` 且与自身不一致，日志会打印 `⚠️ 检测到两处流水线脚本不一致` 并给出同步命令
- 改完代码/文档后执行同步（会校验、升版本、重打包）：

```bash
python workflow/tools/sync_skill.py --check     # 只检查，不写文件
python workflow/tools/sync_skill.py             # 同步 + 升补丁号
python workflow/tools/sync_skill.py --minor     # 口径/功能变更时升次版本号
```

**注意**：不要用「整文件覆盖」的方式手工同步。2026-11-09 就是这么把项目专用的 `--workdir` 默认值带进技能，导致技能在别人的工程里去找 `~/.workbuddy/skills/data/` 而直接失败。

## 验收

交付前按 `references/acceptance.md` 逐条自检，并把结果如实报给用户（通过/不通过 + 证据）。脚本内已内建断言：工作表名 = 日期、每张表图片数 = 卡片数、超链数 = 卡片数。

## 排错

遇到抓不满、价格缺失、图片空白、列宽/居中异常，查 `references/troubleshooting.md`；里面按现象 → 原因 → 对策列出了所有已踩过的坑。

## 资源

- `scripts/run.py`：主流水线（抓取 → 补品牌价格 → 下图 → 颜色 → 出表，含校验断言）
- `scripts/render_html.py`：把 `data/<日期>/items.json` 渲染成单文件 HTML 看板（`--embed` 可离线）
- `assets/board.html`：HTML 看板模板，`__DATA__` 处注入 JSON
- `references/layout-spec.md`：版式精确参数、字段定义、颜色规则、验收标准
- `references/troubleshooting.md`：执行手册与踩坑表
- `references/acceptance.md`：交付前检查清单
