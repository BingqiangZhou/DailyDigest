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
python main.py --source wechat        # 微信公众号
python main.py --source all           # 全部源
python main.py --hours 72             # 自定义时间范围
python main.py --language en          # 英文报告
python main.py --limit 20             # 限制源数量（测试用）
python main.py --source tech --finalize  # Skill 模式：合并 sub-agent 摘要生成最终报告
```

## 信息源

| 源类型 | 规模 | 配置文件 | 说明 |
|--------|------|---------|------|
| 科技新闻 | 268+ RSS + Hacker News | `config/tech_feeds.json` | 含 15 个分类，支持优先级 |
| 播客 | 1000 中文播客 | `config/podcast_feeds.json` | RSS + 小宇宙链接解析 |
| 微信公众号 | ~395 个 | `config/wechat_feeds.json` | 通过 Wechat2RSS 获取 |
| YouTube | 5 频道 | `config/youtube_feeds.json` | 含在科技新闻中 |

## 项目结构

```
├── main.py                  # 统一入口
├── SKILL.md                 # Claude Code Skill 定义
├── core/                    # 核心模块
│   ├── article.py           # 统一数据模型（Article dataclass）
│   ├── config.py            # 配置管理、分类体系
│   ├── http.py              # 共享 HTTP/SSL 工具（抓取、重试、ETag 缓存）
│   ├── html_utils.py        # 共享 HTML 解析（零依赖 + BeautifulSoup）
│   ├── rss_fetcher.py       # RSS 抓取（feedparser + stdlib 双后端）
│   ├── dedup.py             # 文章去重（SHA-256 + Jaccard 相似度）
│   ├── ai_summarizer.py     # AI 摘要（OpenAI API，带重试）
│   ├── report_generator.py  # Markdown 报告生成
│   ├── pipeline.py          # Pipeline 编排（运行、合并、finalize）
│   ├── podcast_utils.py     # 播客：小宇宙解析、播客报告
│   └── wechat_utils.py      # 微信：Feed 获取、全文提取、微信报告
├── config/                  # 源配置
│   ├── tech_feeds.json      # 科技 RSS 源
│   ├── podcast_feeds.json   # 播客源
│   ├── wechat_feeds.json    # 微信公众号源
│   └── youtube_feeds.json   # YouTube 频道
├── scripts/
│   └── fetch_transcripts.py # YouTube 字幕提取
├── .github/workflows/
│   └── digest.yml           # GitHub Actions 定时任务
├── daily-digests/           # 报告输出（Actions 自动提交）
└── workspace/               # 运行时中间文件（gitignore）
```

## 核心特性

- **双后端 RSS 抓取**：feedparser（功能全）+ stdlib urllib（零依赖），自动按 API_KEY 切换
- **ETag/If-Modified-Since 缓存**：增量更新，避免重复抓取
- **跨源去重**：URL 标准化 + 词级倒排索引加速的 Jaccard 标题相似度
- **并发抓取**：ThreadPoolExecutor，实时进度输出
- **优先级分级**：每个源可设置 priority（1/2/3），控制最大文章数
- **小宇宙集成**：自动解析播客页面的 `__NEXT_DATA__` 匹配 episode URL
- **统一数据模型**：Article dataclass 统一科技/播客/微信三种源的数据格式
- **报告格式美化**：标题加粗、`·` 分隔来源、清晰可见，合并报告自动降级标题层级、消除重复分隔符- **统一分类体系**：15 个分类，兼容两个源项目的分类映射
- **AI API 容错**：3 次重试 + 退避 + 超时控制

## GitHub Actions Secrets

| Secret | 必需 | 说明 |
|--------|------|------|
| `API_KEY` | 是 | OpenAI 兼容 API 密钥 |
| `BASE_URL` | 否 | API 地址，默认 NVIDIA NIM |
| `MODEL` | 否 | 模型名称，默认 llama-3.1-nemotron-70b |
| `SUPADATA_API_KEY` | 否 | YouTube 字幕提取 API |

## 依赖

```bash
pip install -r requirements.txt
```

- **GitHub Actions 模式**：feedparser, openai, python-dotenv, beautifulsoup4, requests
- **Skill 模式**：纯 Python 标准库，无需安装

## License

MIT
