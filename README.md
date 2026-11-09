# 亚马逊 BSR 看板 · amazon-bsr-board

把亚马逊 Best Sellers 类目榜单（前 100 名）抓下来，做成**能直接看的卡片看板**：一份 Excel、一份单文件 HTML。

> BSR = Best Sellers Rank。这个技能解决的不是"把数据抓下来"，而是"抓下来之后长什么样"——
> 排名表人人会做，但一万行数字看不出谁在抢位子，一百张卡片可以。

| 字段 | 来源 | 说明 |
|---|---|---|
| 商品图 | 榜单卡片 | 内嵌进表格，不是外链 |
| 排名 | 榜单 `.zg-bdg-text` | 1–100 自然位次，前 3 名橙色描边 |
| 价格 | 商品页 | 美元实价，带榜单卡片价兜底 |
| 品牌 | 商品页 `#bylineInfo` | 去掉 `Visit the` / `Brand:` / `Store` 前后缀 |
| 颜色 | 商品页 Color 字段 | **完整原文，一字不改**（见下文） |
| ASIN | 榜单链接 | 超链接，点击直达商品页 |

## 产出

一次运行同时产出两份，内容一致：

```
output/Amazon_BestSellers_<类目>_<YYYY-MM>.xlsx   一个月一个文件，一天一张以日期命名的工作表
output/board_<类目>_<YYYY-MM>.html                一个月一个文件，顶部按钮切日期，默认最新一天
```

- **Excel**：卡片看板版式，每行 10 张、共 100 张；顶部三行信息 + 一行色系分布色条
- **HTML**：单文件，当月所有日期都在里面，点日期即切换；图片默认走亚马逊图床，`--embed` 可离线

## 安装

作为 [WorkBuddy](https://www.workbuddy.cn) 技能使用，直接克隆到技能目录：

```bash
git clone https://github.com/Xebet/amazon-bsr-board.git ~/.workbuddy/skills/amazon-bsr-board
```

也可以下载 `amazon-bsr-board.zip` 导入技能列表。装好后对 Agent 说
「帮我抓亚马逊 XX 类目榜单做个看板」即可，它会读 `SKILL.md` 执行。

## 用法

```bash
pip install openpyxl pillow playwright

# 每天跑这个：抓当天数据，汇总进当月文件（每月自动新建，一天一张表）
python scripts/run.py --date 2026-11-09 --monthly

# 只重排版式，不重抓（改样式后用，约 40 秒）
python scripts/run.py --date 2026-11-09 --skip-fetch --monthly

# 换类目
python scripts/run.py --date 2026-11-09 --monthly --node 702381011 --name "Hair Replacement Wigs"

# 只出 HTML
python scripts/render_html.py --month 2026-11 --embed

# 给历史数据补颜色原文（浏览器通道，带缓存）
python scripts/enrich_color.py --dates 2026-10-21,2026-10-22
```

工程根目录默认取**当前工作目录**，里面需要 `data/` 与 `output/`（脚本自动建）。

### 主要参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--date` | 今天 | 目录名与工作表名 |
| `--node` | `702381011` | 类目节点 ID，从榜单 URL `zgbs/<类目>/<节点ID>` 取 |
| `--name` | `Hair Replacement Wigs` | 类目英文名，用于文件名 |
| `--monthly` | 关 | 汇总成当月一本多工作表工作簿 |
| `--skip-fetch` | 关 | 复用 `data/<日期>/items.json`，只重排 |
| `--workers` | 5 | 商品页并发数 |
| `--no-html` | 关 | 不生成 HTML |

## 颜色口径：完整原文，一字不改

卡片上显示的是**亚马逊商品页里卖家自己写的 Color 字段**，不是"归成黑色/粉色"的分类名：

```
Ash Rooted platinum blonde        1b                OFF BLACK
Small size light blue             Natural Color 1b#
Silver+Leopard+Black (Pack of 3)  200%Density 13x4 Lace Front Wig
```

上面这些都要**原样显示**，即使看起来不像颜色。规则：

- 取数顺序：变体选择器 `#variation_color_name .selection` → 详情表 `Color` 行 → `detailBullets` 的 `Color:` 项 → 都没有才用标题里的颜色短语
- 清洗只做无损动作：去 HTML 标签、去 `Color:` 这个**字段名**前缀、合并空白、去首尾标点
- **不改大小写**（`1b` 就是 `1b`）、**不截断**、**不删尺寸前缀**
- 卖家拼写错误照留：`Nutural Black`、`Sliver to Purple`、`Gloden Blonde`
- 验收标准：页面有该字段时，`color_raw` 与 `color_dp` 逐条 **100% 相同**
- 顶部色条仍按**色系**统计（黑/金/渐变…），标签写「色系分布」，与卡片原文并存

这条规则被改坏过两次，`references/layout-spec.md` §4 记了完整教训，改之前请先读。

## 已知限制

- **榜单每页只直出 30 条**，其余靠滚动加载，必须连续多轮无增长才算到底，否则只会拿到 60 条
- **图片可能被截断**（curl 中断常见 8KB 半张图），下载后必须真解码一遍，坏图换规格重下
- **部分商品不显示价格**（"无法配送至当前地区"），美国收货地址 prime 未生效时会出现单日大量缺价；此时均价会偏低，需在报告里注明
- **价格不可回溯**：榜单每天变，用今天的价格补昨天的表等于造假；**颜色可以**（Color 是商品固定属性）
- **openpyxl 读回 xlsx 会丢已嵌入的图片**，所以月度表必须由落盘数据整体重建，不能"打开旧文件再追加一张表"
- 亚马逊反爬会限流，`--workers` 调高更快但更易触发；触发后自动转浏览器逐条补，反而更慢，5 是折中

## 目录

```
SKILL.md                  技能说明（触发条件、执行命令、硬性规则）
scripts/run.py            主流水线：抓取 → 补品牌/价格/颜色 → 下图 → 出表
scripts/render_html.py    同一份数据出单文件 HTML 看板
scripts/enrich_color.py   给历史数据补商品页颜色原文（浏览器并发，带缓存）
assets/board.html         HTML 模板
references/layout-spec.md      版式与颜色口径（精确参数）
references/troubleshooting.md  踩坑与排错
references/acceptance.md       交付验收清单
CHANGELOG.md              更新记录
```

## 版本

当前 **v1.2.0**，详见 [CHANGELOG.md](CHANGELOG.md)。版本号同时写在 `SKILL.md` 的 `version:`
与 `scripts/run.py` 的 `__version__`，两处必须一致；运行 `python scripts/run.py --version` 可查。

## License

[MIT](LICENSE)
