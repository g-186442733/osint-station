---
name: osint-person-brief
description: |
  OSINT 人物情报速报。从 osint-station 数据库查询指定人物的近期动态，
  调用 LLM 分析核心观点、商业信号和异常变动，生成字幕翻译风格的精美 HTML 报告。
  触发词：「林亦最近在干嘛」「Sam Altman 动态」「给我陆奇的情报」「XXX的动态」「人物情报」
  输出：~/Desktop/osint-reports/{人物名}_{日期}.html，自动打开浏览器
---

# OSINT 人物情报速报

> 从 OSINT 数据库生成单人物深度情报报告，字幕翻译同款 HTML 风格。

## 触发条件

用户提到以下关键词时触发：
- 「XXX 最近在干嘛」「XXX 最近怎么样」「XXX 动态」
- 「给我 XXX 的情报」「查一下 XXX」「XXX 说了什么」
- 「XXX 最近发表了什么观点」「XXX 有什么变动」
- 「人物情报」「人物速报」「profile brief」
- 直接提到 osint-station 中已关注人物的名字 + 动态/情报/观点/最近等词
- 提到任何人名 + 「怎么样」「在干嘛」「最近」等词

## 执行流程

### Step 1: 识别目标人物

从用户消息中提取人物名称。如果不确定，运行以下命令列出可用人物：

```bash
cd ~/Desktop/osint-station && source .venv/bin/activate
python3 -c "
import sqlite3
db = sqlite3.connect('storage/osint.db')
db.row_factory = sqlite3.Row
for r in db.execute('SELECT name, slug, priority FROM profiles ORDER BY priority, name'):
    print(f'  {r[\"name\"]} (@{r[\"slug\"]}) [{r[\"priority\"]}]')
db.close()
"
```

### Step 2: 生成报告

```bash
cd ~/Desktop/osint-station && source .venv/bin/activate
python3 scripts/person_brief.py "人物名称" --days 30
```

参数说明：
- 第一个参数：人物名称（如 `林亦LYi`、`Sam Altman`、`李开复`）
- `--days N`：回溯天数，默认 30
- `--no-open`：不自动打开浏览器

脚本会自动：
1. 从 SQLite 查询该人物近 N 天的全平台帖子
2. 计算影响力指标（帖子数、平台覆盖、平均互动、活跃天数）
3. 调用 LLM（147AI gemini-2.0-flash → gpt-4.1-nano fallback）分析观点
4. 检测异常信号（频率突变、跨平台活跃、高互动内容）
5. 渲染 HTML 并输出到 `~/Desktop/osint-reports/`

### Step 3: 质量检查

生成后检查 HTML 文件：
- 确认 `<link rel="stylesheet" href="...style.css">` 路径正确
- 确认观点分析部分不为空（如 LLM 全部失败则提示用户）
- 如有需要，用 Read 工具读取 HTML 并优化分析内容的 Markdown 渲染

### Step 4: 向用户汇报

告诉用户：
- 报告已生成在 `~/Desktop/osint-reports/` 目录
- 包含哪些内容（核心发现、观点分析、TOP 5 帖子、时间线）
- 如果 LLM 分析因限速失败，提示用户稍后重试

## HTML 模板规范

引用 `~/Desktop/subtitle_work/style.css`（日韩杂志风格），使用以下 CSS 类：

| 组件 | CSS 类 | 用途 |
|------|--------|------|
| 核心发现 | `.conclusion` | 3-5 条要点总结 |
| 数据卡片 | `.stat-cards` + `.stat-card.red/.blue/.orange/.green` | 4 张统计卡片 |
| 异常信号 | `.callout.warning` | 异常检测结果 |
| 高亮框 | `.highlight-box` | TOP 5 帖子展示 |
| 时间线 | `.timeline` + `.timeline-item` + `.timeline-time` + `.timeline-content` | 活跃时间线 |
| 元信息 | `.meta-info` | 人物/时间/平台信息 |

## 数据源

- **数据库**: `~/Desktop/osint-station/storage/osint.db`
- **LLM API**: 147AI (`api.147ai.cn/v1`)，模型 `gemini-2.0-flash` → fallback `gpt-4.1-nano`
- **样式文件**: `~/Desktop/subtitle_work/style.css`
- **输出目录**: `~/Desktop/osint-reports/`
