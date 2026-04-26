---
name: daily-digest
description: |
  Unified daily digest generator. Monitor tech news, podcasts, and WeChat articles.
  Trigger: "日报", "科技日报", "播客更新", "微信日报", "全部日报", "digest", "daily report"
  Do NOT trigger for: creating/editing content, managing accounts.
---
# Daily Digest — 统一日报生成

从多个信息源抓取内容并生成 AI 摘要日报。

## 支持的信息源

| 源 | 规模 | 命令 |
|----|------|------|
| 科技新闻 | 268+ RSS + HN | `python main.py --source tech` |
| 播客 | 1000 中文播客 | `python main.py --source podcast` |
| 微信公众号 | ~395 个 | `python main.py --source wechat` |
| 全部 | 所有源 | `python main.py --source all` |

## 快速开始

### 方式一：一键生成（GitHub Actions 模式，需要 API_KEY）

```bash
python main.py --source tech
python main.py --source podcast
python main.py --source wechat
python main.py --source all
```

设置 `API_KEY` 环境变量后，会自动调用 OpenAI API 生成 AI 摘要。

### 方式二：Claude sub-agent 生成摘要（Skill 模式，不需要 API_KEY）

不设置 `API_KEY` 时，`main.py` 只完成**抓取和去重**，将原始数据保存到 `workspace/` 目录。
然后由 Claude sub-agent 生成 AI 摘要，最后重新生成报告。

#### 科技新闻（sub-agent 模式）

```bash
# Step 1: 抓取（保存原始数据到 workspace/tech_updates.json）
python main.py --source tech

# Step 2: Claude 读取数据，用 sub-agent 生成摘要
# （Claude 自行执行：读取 workspace/tech_updates.json，分批启动 sub-agent）

# Step 3: 合并摘要后，Claude 重新生成含摘要的报告
```

#### 播客（sub-agent 模式）

```bash
# Step 1: 抓取 + 解析小宇宙链接（保存到 workspace/podcast_updates.json）
python main.py --source podcast

# Step 2: Claude 用 sub-agent 生成摘要
# （读取 workspace/podcast_updates.json，分批启动 sub-agent）
```

#### 微信公众号（sub-agent 模式）

```bash
# Step 1: 抓取（保存到 workspace/wechat_updates.json）
python main.py --source wechat

# Step 2: Claude 用 sub-agent 生成摘要
# （读取 workspace/wechat_updates.json，分批启动 sub-agent）
```

## Sub-agent 摘要 Prompt 模板

### 科技新闻

```
Read {project_root}/workspace/tech_batch_{N}.json

For each article, follow these steps:
Step 1: Identify the primary topic and sub-domain (AI/ML, security, cloud, open-source, etc.)
Step 2: Write a 50-100 character Chinese summary that captures the key insight (not just restating the title)
Step 3: Assign a tier based on significance:
  - must_read: Major announcement, breakthrough, cross-industry impact, or new model/product release
  - noteworthy: Significant update, useful tool, or notable research
  - brief: Routine update, minor release, or incremental improvement
Step 4: For must_read articles, write a 1-2 sentence "importance_reason" explaining why this matters
Step 5: Self-check: Do the must_read articles genuinely represent the most important developments?

Output JSON to {project_root}/workspace/tech_summary_batch_{N}.json:
{"summaries": [{"url": "...", "ai_summary": "...", "category": "...", "tier": "noteworthy", "importance_reason": ""}]}
Use json.dump() with ensure_ascii=False, encoding="utf-8".
```

### 播客

```
Read {project_root}/workspace/podcast_batch_{N}.json

For each episode, follow these steps:
Step 1: Identify the episode's core topic
Step 2: If the episode discusses a specific technology or product, name it explicitly
Step 3: Write a 30-50 character Chinese summary focusing on the key takeaway
Step 4: Filter out ads, sponsor reads, and promotional content — do not summarize these

If content is insufficient, output "内容暂无".
Output JSON to {project_root}/workspace/podcast_summary_batch_{N}.json:
{"url1": "summary1", "url2": "summary2", ...}
```

### 微信公众号

```
Read {project_root}/workspace/wechat_batch_{N}.json

For each article, follow these steps:
Step 1: Extract the article's core claim, finding, or key insight
Step 2: Write a 50-100 character Chinese summary that captures the key information
Step 3: If the article mentions specific technologies, products, or data, include them in the summary

Output JSON to {project_root}/workspace/wechat_summary_batch_{N}.json:
{"summaries": [{"article_url": "...", "ai_summary": "..."}]}
Use json.dump() with ensure_ascii=False, encoding="utf-8".
```

## 项目结构

```
├── main.py                  # 统一入口（GitHub Actions 和 Skill 都调用它）
├── core/                    # 核心模块
│   ├── article.py           # Article 数据类 + ArticleExtra TypedDict
│   ├── config.py            # 配置管理（分类、环境变量）
│   ├── llm.py               # LLM 客户端单例 + 任务配置 + 重试逻辑
│   ├── llm_utils.py         # JSON 解析（code fence 剥离 + json.loads）
│   ├── logging_config.py    # 结构化 logging 配置
│   ├── rss_fetcher.py       # RSS 抓取
│   ├── dedup.py             # 去重
│   ├── ai_summarizer.py     # AI 摘要（并发批量 + 多轮审阅）
│   ├── ai_report.py         # AI 深度分析报告
│   ├── ai_filter.py         # AI/非AI 分类
│   ├── wechat_article.py    # 公众号文章渲染
│   ├── topic_cluster.py     # 话题聚类与重要性评分
│   ├── enrich.py            # 全文补充（ENRICH_FULL_TEXT=1 开启）
│   ├── report_generator.py  # 报告生成
│   ├── pipeline.py          # Pipeline 编排（run_tech/run_podcast/run_wechat）
│   ├── podcast_utils.py     # 播客专有逻辑
│   └── wechat_utils.py      # 微信专有逻辑
├── config/                  # 源配置 + Prompt 模板
│   ├── prompts/             # Prompt 模板（独立模块）
│   │   ├── ai_filter.py     #   AI 过滤 prompt
│   │   ├── ai_report.py     #   深度分析 prompt
│   │   ├── wechat.py        #   公众号结构 prompt
│   │   └── critique.py      #   审阅 prompt
│   ├── tech_feeds.json      # 268+ 科技 RSS
│   ├── podcast_feeds.json   # 1000 播客
│   ├── youtube_feeds.json   # YouTube 频道
│   └── wechat_feeds.json    # 微信公众号
├── tests/                   # 测试套件（pytest）
│   ├── conftest.py          # 共享 fixture
│   ├── test_article.py      # Article 数据类测试
│   ├── test_config.py       # 配置函数测试
│   ├── test_llm_utils.py    # JSON 解析测试
│   ├── test_wechat_article.py # 渲染函数测试
│   └── test_report_generator.py # 报告工具函数测试
├── knowledge/               # 知识库
├── workspace/               # 运行时中间文件
└── daily-digests/           # 报告输出
```

## 性能

| 源 | 全量 | 缓存 | AI 摘要 | 总计 |
|----|------|------|---------|------|
| 科技 | ~2-3 min | ~30-60s | ~60s | ~3-5 min |
| 播客 | ~2 min | ~30s | ~1 min | ~3 min |
| 微信 | ~60-90s | ~15-20s | ~60s | ~2-3 min |
| 全部 | | | | ~8-11 min |
