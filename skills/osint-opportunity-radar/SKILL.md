---
name: osint-opportunity-radar
description: |
  OSINT 商业机会雷达。扫描所有关注人物的近期动态，通过 LLM 聚合分析商业信号、
  趋势聚类和行动建议，生成字幕翻译风格的精美 HTML 报告。
  触发词：「最近有什么商业机会」「商业雷达」「行业洞察」「趋势分析」「机会扫描」
  输出：~/Desktop/osint-reports/商业机会_{日期}.html，自动打开浏览器
---

# OSINT 商业机会雷达

> 扫描全部关注人物的近期内容，聚合提取商业信号和行业趋势，字幕翻译同款 HTML 风格。

## 触发条件

用户提到以下关键词时触发：
- 「最近有什么商业机会」「有什么机会」「商业信号」
- 「商业雷达」「机会雷达」「opportunity radar」
- 「行业洞察」「趋势分析」「趋势聚类」
- 「最近行业有什么动向」「商业情报」
- 「AI圈这周发生了什么」「这周的行业动态」
- 「最近有什么新的创业方向」「投资机会」
- 「给我看看最近的趋势」「什么方向值得关注」
- 任何涉及「趋势」「商业机会」「行业洞察」「创业方向」等词的请求

## 执行流程

### Step 1: 确认参数

默认扫描近 7 天。如果用户指定了时间范围（如「最近一个月」），调整 `--days` 参数。

### Step 2: 生成报告

```bash
cd ~/Desktop/osint-station && source .venv/bin/activate
python3 scripts/opportunity_radar.py --days 7
```

参数说明：
- `--days N`：扫描天数，默认 7
- `--no-open`：不自动打开浏览器

脚本会自动：
1. 从 SQLite 查询所有真人 KOL 近 N 天帖子（过滤 Reddit/HN 聚合源）
2. 按互动量排序取 TOP 50 送入 LLM 分析
3. LLM 输出：TOP 5 商业信号、趋势聚类、行动建议、关键人物动向
4. 渲染 HTML（统计卡片 + LLM 分析 + 高互动内容列表）
5. 输出到 `~/Desktop/osint-reports/商业机会_{日期}.html`

### Step 3: 质量检查

生成后检查：
- 确认 LLM 分析不为空（如全部失败则提示用户稍后重试）
- 确认帖子数 > 0（如无数据提示用户先运行 `python3 main.py collect`）

### Step 4: 向用户汇报

告诉用户：
- 报告已生成在 `~/Desktop/osint-reports/` 目录
- 扫描了多少帖子、涉及多少人物
- 如果 LLM 分析因限速失败，提示用户稍后重试

## HTML 模板规范

引用 `~/Desktop/subtitle_work/style.css`（日韩杂志风格），使用以下 CSS 类：

| 组件 | CSS 类 | 用途 |
|------|--------|------|
| 数据卡片 | `.stat-cards` + `.stat-card.red/.blue/.orange/.green` | 帖子数/人物数/平台数/天数 |
| 高亮框 | `.highlight-box` | 高互动内容展示 |
| 元信息 | `.meta-info` | 扫描范围/生成时间 |

## 数据管理操作

### 调整扫描范围
- 「只看最近 3 天的」→ `--days 3`
- 「看这个月的趋势」→ `--days 30`

### 人物不在列表中
如果用户提到某个人物的商业动向但该人物不在监控列表中：
1. 告知「该人物不在监控列表，当前报告不包含其数据」
2. 询问「是否要添加到监控列表？需要哪些平台的 ID？」
3. 确认后用管理脚本添加：

```bash
cd ~/Desktop/osint-station && source .venv/bin/activate
python3 scripts/manage_ops.py add "人物名" --twitter handle --youtube channel_id --tags AI --priority medium
python3 scripts/manage_ops.py collect "人物名"
```

4. 重新生成报告

### 查看/管理监控列表
```bash
cd ~/Desktop/osint-station && source .venv/bin/activate
python3 scripts/manage_ops.py list      # 查看所有监控人物
python3 scripts/manage_ops.py stats     # 查看数据库统计
python3 scripts/manage_ops.py remove "人物名"   # 移除人物
python3 scripts/manage_ops.py priority "人物名" high  # 调优先级
```

### 可选后处理（推送企微/飞书）
```bash
cd /Users/axureboutique/VideoProcessor && source .venv/bin/activate
python3 processor.py "/Users/axureboutique/Desktop/osint-reports/商业机会_YYYYMMDD.html"
```

## 数据源

- **数据库**: `~/Desktop/osint-station/storage/osint.db`
- **LLM API**: 147AI (`api.147ai.cn/v1`)，模型 `gemini-2.0-flash` → fallback `gpt-4.1-nano`
- **样式文件**: `~/Desktop/subtitle_work/style.css`
- **输出目录**: `~/Desktop/osint-reports/`
- **后端脚本**: `~/Desktop/osint-station/scripts/opportunity_radar.py`
- **共用数据层**: `~/Desktop/osint-station/scripts/osint_data.py`
