# 验收清单 · 交付前逐条打勾

> 抄给 Agent：「按这份清单自检，每条给出 yes/no 和证据，不要只回『已完成』。」

## A. 数据完整性

- [ ] **A1 抓满 100 条**：排名 1–100 连续、无缺号、无重复 ASIN
      → 证据：`len(items) == 100`，`sorted(ranks) == list(range(1,101))`
- [ ] **A2 品牌无缺失**：100 条全部有品牌名
- [ ] **A3 价格无缺失**：100 条全部有美元价格（用榜单价兜底的已标 `price_src=grid`，需说明条数）
- [ ] **A4 图片齐**：100 张图落盘且单张 > 3KB（0 字节 / 占位图要重下）
- [ ] **A5 币种正确**：价格是 USD，不是 CNY 预算价

## B. 表格结构

- [ ] **B1 单表**：只有 1 个工作表，表名 = 日期（如 `2026-10-21`）
- [ ] **B2 卡片数**：100 张，10 张/行 × 10 行
- [ ] **B3 图片内嵌**：`len(ws._images) == 100`（不是外链）
- [ ] **B4 超链接**：`100` 个 ASIN 单元格都带 hyperlink，指向 `https://www.amazon.com/dp/<ASIN>`
- [ ] **B5 打开正常**：Excel / WPS 打开无「修复」提示

## C. 版式还原

- [ ] **C1 文字居中**：排名/价格/品牌/ASIN 的 `HorizontalAlignment == xlCenter(-4108)`
- [ ] **C2 图片居中**：`Shape.Left - Range.Left ≈ (卡宽 - 图宽)/2`，误差 < 1pt
- [ ] **C3 价格未被截断**：没有出现 `#######`
- [ ] **C4 前三名描边**：1–3 名卡片边框为 `#FF9900`
- [ ] **C5 顶部信息齐全**：标题 / 数据源+时间 / 价格统计 / 颜色分布 四行都在
- [ ] **C6 颜色占比合计 100%**：各颜色数量合计 100、占比合计 100%

## D. 一致性

- [ ] **D1 统计一致**：均价/中位价/价格带/品牌数 与卡片实际数据重算一致
- [ ] **D2 颜色数量一致**：色条上的数量 = 卡片里该颜色的实际张数
- [ ] **D3 排名与卡片一致**：`#N` 与该格商品在榜单的位次一致

## C+. 颜色字段

- [ ] **C1 每卡有颜色**：100 张卡片都有颜色行（页面确实没有 Color 字段时才允许 `—`）
- [ ] **C2 是原文不是归类**：卡片显示 `Light Hot Pink` / `1b` / `Ash Rooted platinum blonde`，不是「粉色」「黑色」，大小写与页面一致
- [ ] **C3 逐条一致率 100%**：`color_raw` 与 `color_dp` 逐条比对，差异必须为 0
- [ ] **C4 长文本不裁**：超过 24 字符的颜色（`Ash Rooted platinum blonde`、`Silver+Leopard+Black (Pack of 3)`）完整显示、单元格自动换行
- [ ] **C5 色条口径**：顶部「色系分布」合计 100，是 `color_raw` 归组后的结果，与卡片原文不矛盾

## D+. HTML 看板

- [ ] **H1 同月同文件**：`board_<slug>_<YYYY-MM>.html`，当月所有日期都在这一份里
- [ ] **H2 日期切换**：顶部按钮数量 = 当月天数，默认停在最新一天，点击能切换且数据随之变化
- [ ] **H3 内容一致**：HTML 某天的统计/卡片 与 Excel 同一天的工作表一致
- [ ] **H4 图能显示**：非 `--embed` 时依赖亚马逊图床，断网会空白（离线场景改用 `--embed`）

## E. 可复用性（交付这套流程时检查）

- [ ] **E1 参数化**：换类目只改 `--node` / `--name`，不用改代码
- [ ] **E2 幂等**：同一天重跑不叠加、不重复建表
- [ ] **E3 失败可见**：抓不满 100 条会报错退出，不会静默出半张表
- [ ] **E4 断点续跑**：`data/<date>/images` 已存在时跳过；商品页缓存在系统临时目录（按日期隔离，当天重跑免抓）
- [ ] **E5 耗时**：冷启动约 2.5–4 分钟，同一天热缓存重跑约 40 秒

## 自检命令（可直接跑）

```bash
# B3/B4/B1 + A1
python - <<'PY'
import json
from openpyxl import load_workbook
date = "2026-10-21"
items = json.load(open(f"data/{date}/items.json", encoding="utf-8"))
ws = load_workbook(f"output/Amazon_BestSellers_Hair_Replacement_Wigs_{date}.xlsx").active
ranks = sorted(x["rank"] for x in items)
print("A1", ranks == list(range(1, 101)), len(items))
print("B1", ws.title == date, ws.title)
print("B3", len(ws._images))
print("B4", sum(1 for r in ws.iter_rows() for c in r if c.hyperlink))
PY
```
