---
name: osint-daily-digest
description: |
  OSINT 每日情报摘要。扫描全部关注人物和行业资讯的近期动态，
  分为人物动态和行业资讯两部分，通过 LLM 生成结构化每日简报，输出字幕翻译风格的精美 HTML。
  触发词：「今日情报」「每日摘要」「今天有什么新动态」「daily digest」「情报简报」「每日速递」
  输出：~/Desktop/osint-reports/每日情报_{日期}.html，自动打开浏览器
---

# OSINT 每日情报摘要

> 生成包含人物动态和行业资讯的每日情报简报，字幕翻译同款 HTML 风格。

## 触发条件

用户提到以下关键词时触发：
- 「今日情报」「每日摘要」「今天有什么新动态」
- 「daily digest」「情报简报」「每日速递」
- 「今天的情报」「最新动态汇总」「每日简报」
- 「给我一份今天的情报」「今天AI圈有什么新鲜事」
- 「最近发生了什么」「有什么新消息」「给我看看最新的」
- 「情报汇总」「动态总结」「今日速报」
- 任何涉及「今天」「最新」「动态汇总」「情报」等词的请求

## 执行流程

### Step 1: 确认参数

默认扫描近 1 天。如果用户指定了时间范围（如「最近三天的摘要」），调整 `--days` 参数。

### Step 2: 生成报告

```bash
cd ~/Desktop/osint-station && source .venv/bin/activate
python3 scripts/daily_digest.py --days 1
```

参数说明：
- `--days N`：回溯天数，默认 1（今天）
- `--no-open`：不自动打开浏览器

脚本会自动：
1. 从 SQLite 查询近 N 天全部帖子
2. 按 slug 前缀将内容拆分为「人物动态」和「行业资讯」两组
3. 合并两组内容送入 LLM，生成结构化分析（今日要点、人物动态、值得深读、异常信号）
4. 渲染 HTML（统计卡片 + LLM 分析 + 人物高互动 TOP 10 + 行业热门 TOP 5）
5. 输出到 `~/Desktop/osint-reports/每日情报_{日期}.html`

### Step 3: 质量检查

生成后检查：
- 确认 LLM 分析不为空（如全部失败则提示用户稍后重试）
- 如果近 1 天无数据，建议用户扩大到 `--days 3` 或先运行 `python3 main.py collect`

### Step 4: 向用户汇报

告诉用户：
- 报告已生成在 `~/Desktop/osint-reports/` 目录
- 包含多少条人物动态和行业资讯
- 如果数据量少，建议扩大时间窗口

## HTML 模板规范

引用 `~/Desktop/subtitle_work/style.css`（日韩杂志风格），使用以下 CSS 类：

| 组件 | CSS 类 | 用途 |
|------|--------|------|
| 数据卡片 | `.stat-cards` + `.stat-card.blue/.orange/.green/.red` | 人物动态/行业资讯/数据来源/总计 |
| 高亮框 | `.highlight-box` | 高互动帖子展示 |
| 元信息 | `.meta-info` | 数据范围/生成时间 |

## 数据管理操作

### 调整时间范围
- 「看最近 3 天的」→ `--days 3`
- 「给我这周的」→ `--days 7`

### 数据不够时
如果近 1 天无数据，自动建议：
1. 扩大到 `--days 3` 重试
2. 或触发全量采集：

```bash
cd ~/Desktop/osint-station && source .venv/bin/activate
python3 scripts/manage_ops.py collect --all
```

### 查看/管理监控列表
```bash
cd ~/Desktop/osint-station && source .venv/bin/activate
python3 scripts/manage_ops.py list      # 查看所有监控人物
python3 scripts/manage_ops.py stats     # 查看数据库统计
python3 scripts/manage_ops.py add "人物名" --twitter handle --priority medium  # 新增人物
python3 scripts/manage_ops.py remove "人物名"   # 移除人物
```

### 可选后处理（推送企微/飞书）
```bash
cd ~/VideoProcessor && source .venv/bin/activate
python3 processor.py ~/Desktop/osint-reports/每日情报_YYYYMMDD.html
```

## 数据源

- **数据库**: `~/Desktop/osint-station/storage/osint.db`
- **LLM API**: 147AI (`api.147ai.cn/v1`)，模型 `gemini-2.0-flash` → fallback `gpt-4.1-nano`
- **样式文件**: `~/Desktop/subtitle_work/style.css`
- **输出目录**: `~/Desktop/osint-reports/`
- **后端脚本**: `~/Desktop/osint-station/scripts/daily_digest.py`
- **共用数据层**: `~/Desktop/osint-station/scripts/osint_data.py`
