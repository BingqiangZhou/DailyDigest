# Daily Digest

中文内容更新监控与每日摘要生成工具。从科技新闻、播客、微信公众号等渠道抓取内容，通过 AI 生成每日摘要报告。

## 两种运行模式

同一套代码，区别仅在于 **AI 摘要由谁来做**。抓取、去重、报告生成完全共用。

| 模式 | 触发方式 | AI 摘要 | 依赖 |
|------|---------|---------|------|
| **GitHub Actions** | 每日定时 / 手动触发 | OpenAI 兼容 API | feedparser, openai 等 |
| **Claude Code Skill** | 自然语言触发 | Claude sub-agent | 纯标准库（零依赖） |

通过环境变量 `API_KEY` 自动切换：有则走 API，无则走 sub-agent。

## 快速开始

### GitHub Actions 模式

1. Fork 仓库
2. 在 Settings → Secrets and variables → Actions 中添加：
   - `API_KEY`（必需）— OpenAI 兼容 API 密钥
   - `BASE_URL`（可选）— 默认 `https://integrate.api.nvidia.com/v1`
   - `MODEL`（可选）— 默认 `nvidia/llama-3.1-nemotron-70b-instruct`
3. 手动触发 workflow 或等待每日自动运行（北京时间 8:00）

### Claude Code Skill 模式

无需配置 API，在 Claude Code 中直接说"日报"即可触发。详见 [SKILL.md](SKILL.md)。

## 命令行用法

```bash
python main.py                        # 科技新闻（默认）
python main.py --source podcast       # 播客
python main.py --source all           # 全部源
python main.py --hours 72             # 自定义时间范围
python main.py --limit 20             # 限制源数量（测试用）
python main.py --source tech --finalize  # Skill 模式：合并 sub-agent 摘要生成最终报告
```

## 处理管线

```
RSS/WeChat/Podcast feeds
  → Fetch（并发抓取，Feed 健康熔断）
  → Dedup（URL 标准化 + Jaccard 标题相似度）
  → Noise filter（负面门控 → 关键词匹配 → 硬相关性检查）
  → Topic cluster（关键词提取，Jaccard 相似度，层次合并）
  → Editorial pipeline（6 因子新闻价值评分 → 三级分层）
  → AI/non-AI split（LLM 分类 + 关键词回退）
  → AI path：LLM 深度分析（草稿 → 自我批评 → 精炼）
  → Non-AI path：模板渲染（上限 30 篇）
  → 统一两部分报告（Part I: AI 深度，Part II: 科技简讯）
  → TL;DR 生成
```

## 信息源

| 源类型 | 规模 | 配置文件 | 说明 |
|--------|------|---------|------|
| 科技新闻 | 268+ RSS + Hacker News | `config/tech_feeds.json` | 含 15 个分类，支持优先级 |
| 播客 | 1000 中文播客 | `config/podcast_feeds.json` | RSS + 小宇宙链接解析 |
| 微信公众号 | ~395 个 | `config/wechat_feeds.json` | 通过 Wechat2RSS 获取 |

## 项目结构

```
├── main.py                  # 统一入口
├── SKILL.md                 # Claude Code Skill 定义
├── core/                    # 核心模块
│   ├── article.py           # 统一数据模型（Article dataclass）
│   ├── config.py            # 配置、分类体系、权威域名、编辑阈值
│   ├── pipeline.py          # Pipeline 编排（运行、合并、finalize）
│   ├── llm.py               # LLM 客户端（重试、降级、任务 profile）
│   ├── llm_services.py      # LLM 服务（简报生成、主题标题、TLDR）
│   ├── llm_classify.py      # LLM 评分 + 主题分组
│   ├── llm_utils.py         # LLM 工具（JSON 解析、文本清洗）
│   ├── editorial.py         # 编辑管线（6 因子新闻价值评分 → 层级分配）
│   ├── topic_cluster.py     # 主题聚类（关键词提取 + Jaccard + 层次合并）
│   ├── embedding_cluster.py # 嵌入向量聚类（API 模式）
│   ├── briefing.py          # 简报生成（V2 格式、TL;DR、主题标题）
│   ├── renderer.py          # Markdown 渲染（段落、表格、引用链接）
│   ├── report_builder.py    # 报告构建（统一报告、分类结果、TOC）
│   ├── dedup.py             # 文章去重（SHA-256 URL + Jaccard 标题相似度）
│   ├── feed_health.py       # Feed 健康（熔断器、5 次失败跳过、24h 重试）
│   ├── rss_fetcher.py       # RSS 抓取（feedparser）
│   ├── http.py              # HTTP/SSL 工具（重试、ETag 缓存）
│   ├── html_utils.py        # HTML 解析（零依赖 + BeautifulSoup）
│   ├── wechat_utils.py      # 微信：Feed 获取、全文提取、报告构建
│   ├── podcast_utils.py     # 播客：小宇宙解析、播客报告
│   ├── workspace.py         # 工作空间管理
│   ├── date_utils.py        # 日期工具
│   └── logging_config.py    # 日志配置
├── config/                  # 配置
│   ├── prompts/             # LLM Prompt 模板（7 个）
│   ├── tech_feeds.json      # 科技 RSS 源
│   ├── podcast_feeds.json   # 播客源
│   └── wechat_feeds.json    # 微信公众号源
├── knowledge/               # 内容策略、编辑风格、Prompt 技巧
├── scripts/                 # 工具脚本
├── .github/workflows/       # CI/CD（digest.yml + auto-retry.yml）
├── daily-digests/           # 报告输出（Actions 自动提交）
└── workspace/               # 运行时中间文件（gitignore）
```

## 核心特性

- **并发抓取 + Feed 健康熔断** — ThreadPoolExecutor 并发抓取，连续 5 次失败自动跳过，24h 后重试
- **ETag/If-Modified-Since 缓存** — 增量更新，避免重复抓取
- **跨源去重** — URL 标准化 + Jaccard 标题相似度
- **噪声过滤** — 负面门控 → 关键词匹配 → 硬相关性检查
- **主题聚类** — 关键词提取 + Jaccard 相似度 + 层次合并
- **编辑管线** — 6 因子新闻价值评分 → 三级分层（必读 / 值得关注 / 简讯）
- **权威度评分** — 76 个域名 4 级权重（AI 实验室 1.0 → 聚合站 0.55）
- **AI/非AI 分流** — LLM 分类 + 关键词回退
- **深度分析** — 草稿 → 自我批评 → 精炼，三阶段 LLM 循环
- **TL;DR 生成** — 自动提取主题标题和简明摘要
- **统一两部分报告** — Part I: AI 深度分析，Part II: 科技简讯
- **小宇宙集成** — 自动解析播客页面 episode URL
- **AI API 容错** — 重试 + 退避 + 超时 + 降级模式

## GitHub Actions Secrets

| Secret | 必需 | 说明 |
|--------|------|------|
| `API_KEY` | 是 | OpenAI 兼容 API 密钥 |
| `BASE_URL` | 否 | API 地址，默认 NVIDIA NIM |
| `MODEL` | 否 | 模型名称，默认 llama-3.1-nemotron-70b |
| `SUPADATA_API_KEY` | 否 | YouTube 字幕提取 API |

## LLM 稳定性参数

默认策略按"成功率优先"收敛，适合 NVIDIA NIM 和大多数 OpenAI 兼容后端：

| Env Var | 默认值 | 作用 |
|--------|--------|------|
| `LLM_TIMEOUT_SECONDS` | `180` | 单次 LLM 请求超时 |
| `LLM_MAX_RETRIES` | `4` | 可重试错误的最大重试次数 |
| `LLM_MAX_CONCURRENCY` | `2` | 全局 LLM 并发上限 |
| `LLM_RETRY_BASE_SECONDS` | `2` | 指数退避起始秒数 |
| `LLM_RETRY_MAX_SECONDS` | `30` | 单次退避最大等待 |
| `LLM_DEGRADE_AFTER_FAILURES` | `3` | 临时错误累计达到阈值后切入降级模式 |

当 `BASE_URL` 指向 NVIDIA 而 `MODEL` 缺失或明显不匹配时，程序会自动切回 NVIDIA 默认模型并打印告警。运行时日志还会输出当前 provider、model、timeout、并发和是否启用 critique，方便快速定位配置错误和 rate limit 问题。

## 依赖

```bash
uv sync
```

- **GitHub Actions 模式**：feedparser, openai, python-dotenv, beautifulsoup4, scikit-learn
- **Skill 模式**：纯 Python 标准库，无需安装

## License

MIT
