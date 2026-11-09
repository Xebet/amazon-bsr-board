# 更新记录

## 1.2.0 — 2026-11-09

- 颜色口径改为完全无损：不改大小写、不截断、不删尺寸前缀，验收改为 color_raw 与 color_dp 逐条 100% 一致
- 脚本改用 _default_workdir() 自适应位置，项目版与技能版恢复字节一致
- 新增 __version__ 与 SKILL.md version 字段，统一由 tools/sync_skill.py 维护
- 运行时会检测两处流水线脚本是否一致并提示同步

## 1.2.1 — 2026-11-09

- _default_workdir() 扩展到 render_html.py 与 enrich_color.py，三个脚本的默认工程目录解析规则统一（项目内=项目根，技能内=当前目录）
