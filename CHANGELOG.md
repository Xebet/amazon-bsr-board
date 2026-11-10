# 更新记录

## 1.2.0 — 2026-11-09

- 颜色口径改为完全无损：不改大小写、不截断、不删尺寸前缀，验收改为 color_raw 与 color_dp 逐条 100% 一致
- 脚本改用 _default_workdir() 自适应位置，项目版与技能版恢复字节一致
- 新增 __version__ 与 SKILL.md version 字段，两处保持一致
- 运行时会检测两处流水线脚本是否一致并提示同步

## 1.2.1 — 2026-11-09

- _default_workdir() 扩展到 render_html.py 与 enrich_color.py，三个脚本的默认工程目录解析规则统一（仓库内开发目录=仓库根，作为技能安装时=当前目录）

## 1.2.2 — 2026-11-09

- 修复颜色行行高：同一行 10 张卡共用行高，改为取最大值（此前后一张短颜色会把前一张撑高的行压回 17pt，导致 36 字符的颜色被裁成一行）

## 1.3.0 — 2026-11-09

**通用化：从「假发专用」回到「任意类目榜单工具」**

- 新增 `--rank-type`：`bestsellers` 畅销榜 / `new-releases` 新品榜 / `movers-and-shakers` 飙升榜 / `most-wished-for` 心愿单榜，同一套卡片版式，只换 URL 与文案
- 新增 `--market`：17 个站点（.com / .co.uk / .de / .co.jp / .ca / .com.au / .in / .com.mx …），币种与语言随站点切换；`--currency` 可覆盖
- 新增 `--fields`：卡片字段（图/排名/价格/品牌/颜色/ASIN）可增删，段高、色条、冻结行随之自适应，卡片不留空行
- 新增 `--slug`：URL 里的顶层类目段（实测 Amazon 按 node 路由，一般不用改）
- 修复**颜色源端截断**：`_clean_color` 末尾的 `v[:40]` 已去掉，正则捕获上限 60 → 200；实测 `B0HDNSRX2H` 由 40 字符恢复为 53 字符（`Honey Blonde Highlight｜Side Part｜7x5 Lace ｜Drawstring`）
- 文件名前缀随榜单类型（`Amazon_NewReleases_` / `Amazon_MoversShakers_` / `Amazon_MostWishedFor_`），避免同类目不同榜单互相覆盖；畅销榜保持 `Amazon_BestSellers_` 不变，已有文件名与下游脚本不受影响
- HTML 模板改为数据驱动：站点名、榜单名、币种符号与小数位、ASIN 链接域名都从数据里取
- 文档：SPEC/SOP/CHECKLIST/SKILL/README 补「可配置项」章节，并写明多站点的真实限制（类目节点按站点各一套、非美国站易被拦）
- 新增产物截图 `docs/screenshots/`

## 1.3.2 — 2026-11-10

- 文档措辞修正，无功能变更：公开文档统一去掉只有维护者看得懂的表述（本机 / 定时任务 / 项目内等）；`ExportAsFixedFormat` 的说明改为「按工作表导 PDF 可用、整本工作簿导易崩」；SKILL.md 的版本小节重写为对外可读的「版本与命名」
- README 事实性修正：版本号由 1.3.0 更新到当前版本（并改为随同步脚本自动更新）；商品图目录由 `output/.../images/` 更正为 `data/<日期>/images/`；「只留图/排名/价格」的卡片行数说明纠正（实际是「图 + 排名·价格」两行）；补上文件名前缀随榜单类型变化的说明

## 1.4.0 — 2026-11-10

- **口径修正：非色名原文不再记成「其他 / 未标注」**。卖家把 Color 填成规格/货号（`200%Density 13x4 Lace Front Wig`、`CR6000`、`C08#BH Color`、`wigs human hair`）时，卡片文字仍逐字显示原文；但色系占比与卡片色圆改按真实产品颜色判定——假发每一条都有颜色，缺的只是页面没写色名
- 新增 `parse_color_code()`：抓商品页 Style 表 `Color Code`（如 `#403C3B`），curl 与 Playwright 两条抓取路径都支持，落 `items.json` 的 `color_code`
- 新增 `hex_to_family()`：RGB→HSV 归色系（极暗→黑；低饱和按明度分黑/银灰；色相分段归金/棕/红/粉/紫/蓝/绿/橙，613 类沙金归金）
- `classify_color()` 入参改为 `(raw, title, code)`，顺序：原文色名 → 标题挑染信号 → Color Code 色调 → 标题色名；挑染（ombre/highlight/two-tone）仍优先，因为单一色号表达不了双色
- 新增 `dot_color` 字段：非色名原文的圆点直接用 Color Code 真实色值（Excel 富文本 ARGB、HTML `background`），不再画灰点；HTML 模板由 `st[0]` 改为 `x.dot_color || st[0]`
- 词表补 `jetblack`（Sensationnel 官方色号 `1 Jetblack`）/ `jet ?black`，此前被误判为非颜色
- 配套一次性回填脚本（不进技能包，只用于给既有 `data/` 补色号）：只给缺 `color_code` 的非色名条目补色号并重算，带 `color_raw` 不变硬校验（变了就中止写入）
- 实测 11-01～11-10：59 条非色名条目全部归入真实色系，「其他 / 未标注」由每日 1～4 条降为 **0 条**，`color_raw` 零改动
