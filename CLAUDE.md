# CLAUDE.md — OSINT 工作站

> 本文件供 AI 助手在新会话开始时阅读，包含所有必要上下文。

---

## 1. 项目简介

**OSINT 工作站** — 自托管的个人开源情报平台，用于追踪公共人物在国内外社交媒体上的动态，发现新兴趋势和商业机会（非安全/威胁情报）。

核心流程：`crontab 定时采集` → `多平台数据收集` → `SQLite 存储` → `LLM 话题分类 + 趋势评分` → `情报简报生成` → `企微推送` → `Web Dashboard 可视化`

---

## 2. 强制约束

1. **API 平台**：通过 147AI (`api.147ai.cn`) 调用 LLM，禁止直连 Gemini API（地域限制）
2. **模型选择**：话题分类用 `gemini-2.0-flash`，情报简报用 `gpt-4o-mini`，兜底 `gpt-4.1-nano`
3. **无认证爬取优先**：所有采集器尽量使用无需登录的 API 端点
4. **限速必须遵守**：每个平台请求间隔 ≥ 1 秒，B站 WBI 签名正确处理
5. **敏感信息**：API Key 和 Cookie 存 `.env`，禁止硬编码
6. **数据去重**：同一帖子不重复入库，基于 `platform + platform_id` 联合唯一约束
7. **中文输出**：所有 LLM 分析结果和简报使用简体中文

---

## 3. 目录结构

```
osint-station/
├── main.py                  # CLI 入口（init/collect/analyze/brief/push/search/run/status）
├── config/
│   ├── settings.py          # 全局配置（DB路径、LLM配置、API密钥）
│   ├── watchlist.json       # 关注人物列表
│   └── identity_mappings.json # 跨平台身份映射
├── collectors/
│   ├── base.py              # BaseCollector 基类
│   ├── bilibili/collector.py   # B站采集（WBI签名、视频/动态/资料）
│   ├── weibo/collector.py      # 微博采集（m.weibo.cn API）
│   ├── twitter/collector.py    # Twitter/X 采集
│   ├── youtube/collector.py    # YouTube 采集（scrapetube）
│   ├── github/collector.py     # GitHub 公开活动采集
│   ├── reddit/collector.py     # Reddit 采集
│   └── hackernews/collector.py # HackerNews 采集
├── etl/
│   ├── pipeline.py          # 采集调度 + 去重 + 存储
│   ├── summarizer.py        # LLM 话题分类 + 趋势评分
│   └── analyzer.py          # 数据分析
├── intel/
│   ├── profiler.py          # 人物情报分析器
│   ├── discovery.py         # 新人物发现
│   ├── weekly_report.py     # 周报生成
│   └── anomaly.py           # 异常检测
├── storage/
│   ├── schema.py            # SQLite 表结构定义
│   └── db.py                # 数据库操作封装
├── dashboard/
│   └── app.py               # Web Dashboard（原生 HTTP 服务器，端口 8501）
├── reports/
│   ├── briefing.py          # 情报简报生成
│   ├── pusher.py            # 企微 Webhook 推送
│   └── weekly/              # 周报存档
├── scripts/
│   ├── run_daily.py         # 每日定时任务入口
│   ├── query.py             # 数据查询工具
│   ├── search_user.py       # 用户名搜索
│   └── setup_cron.sh        # crontab 配置
├── .env                     # 环境变量（API Key、Cookie、Webhook）
└── requirements.txt         # Python 依赖
```

---

## 4. 关键文件速查

| 用途 | 路径 |
|------|------|
| CLI 入口 | `main.py` |
| 全局配置 | `config/settings.py` |
| 关注列表 | `config/watchlist.json` |
| 身份映射 | `config/identity_mappings.json` |
| 数据库结构 | `storage/schema.py` |
| 数据库文件 | `storage/osint.db` |
| 采集调度 | `etl/pipeline.py` |
| LLM 分析 | `etl/summarizer.py` |
| 人物情报 | `intel/profiler.py` |
| Dashboard | `dashboard/app.py` |
| 简报生成 | `reports/briefing.py` |
| 企微推送 | `reports/pusher.py` |

---

## 5. 常用命令

```bash
# 激活虚拟环境
cd ~/Desktop/osint-station && source .venv/bin/activate

# 初始化数据库
python3 main.py init

# 采集所有关注人物
python3 main.py collect

# 采集指定人物
python3 main.py collect 稚晖君

# 话题分类分析
python3 main.py analyze

# 生成情报简报
python3 main.py brief

# 生成简报并推送企微
python3 main.py push

# Maigret 用户名搜索
python3 main.py search <username>

# 完整流程：采集 → 分类 → 简报 → 推送
python3 main.py run

# 查看数据库状态
python3 main.py status

# 启动 Dashboard
python3 dashboard/app.py  # 访问 http://localhost:8501
```

---

## 6. 数据流

```
crontab (每日 09:00/21:00)
    ↓
main.py run
    ↓
pipeline.py → 调度各 collector
    ↓
各平台 API → 标准化帖子格式 → SQLite (posts 表)
    ↓
summarizer.py → 147AI LLM → 话题分类 + 趋势评分
    ↓
briefing.py → 情报简报 Markdown
    ↓
pusher.py → 企微 Webhook
```

---

## 7. 已知问题与踩坑

- B站 Web API 需 WBI 签名，签名参数每日更新
- 微博 m.weibo.cn API 有严格限速，需模拟移动端 UA
- YouTube Channel ID 需从 `@handle` 转换，用 scrapetube 库
- Maigret 搜索结果噪音大，需后端黑名单 + 前端置信度阈值过滤
- 人物情报工具要求人物必须在 watchlist 中（存在于 profiles 表），否则返回"未找到人物"
- Dashboard 无认证保护，仅限本地访问
- 147AI 偶发 Gemini 地区限制错误，fallback 链自动切换

---

## 8. 偏好与约定

- 前端只用原生 HTML/CSS/JS，不用 React/Vue
- 话题标签统一使用中文
- 时间显示使用相对时间（如"2小时前"）
- LLM 输出必须是简体中文
- 新增采集器必须继承 `BaseCollector` 基类
