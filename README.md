# OpenClaw Digest

中文内容更新监控与每日摘要生成工具。

## 两种运行模式，同一套代码

| 模式 | 触发方式 | AI 摘要 | 命令 |
|------|---------|---------|------|
| **GitHub Actions** | 每日定时 / 手动触发 | OpenAI API | `python main.py --source tech` |
| **Claude Code Skill** | 自然语言触发 | Claude sub-agent | `python main.py --source tech`（无 API_KEY） |

区别仅在于**AI 摘要由谁来做**。抓取、去重、报告生成完全共用。

## 用法

```bash
python main.py                    # 科技新闻（默认）
python main.py --source podcast   # 播客
python main.py --source wechat    # 微信公众号
python main.py --source all       # 全部源
python main.py --hours 72         # 自定义时间范围
python main.py --language en      # 英文报告
```

## 信息源

| 源类型 | 规模 | 配置文件 |
|--------|------|---------|
| 科技新闻 | 268+ RSS + Hacker News | `config/tech_feeds.json` |
| 播客 | 1000 中文播客（RSS + 小宇宙） | `config/podcast_feeds.json` |
| 微信公众号 | ~395 个 | `config/wechat_feeds.json` |
| YouTube | 5 频道（含在科技新闻中） | `config/youtube_feeds.json` |

## 项目结构

```
├── main.py                  # 统一入口（两种模式都调用它）
├── SKILL.md                 # Claude Code Skill 定义
├── core/                    # 核心模块
│   ├── config.py            # 配置管理
│   ├── rss_fetcher.py       # RSS 抓取（feedparser + stdlib 双后端）
│   ├── dedup.py             # 文章去重
│   ├── ai_summarizer.py     # AI 摘要（OpenAI API）
│   ├── report_generator.py  # 报告生成
│   ├── podcast_utils.py     # 播客：小宇宙解析、播客报告
│   └── wechat_utils.py      # 微信：Feed 获取、文章补充、微信报告
├── scripts/
│   └── fetch_transcripts.py # YouTube 字幕提取
├── config/                  # 源配置
├── .github/workflows/
│   └── digest.yml           # GitHub Actions 定时任务
├── daily-digests/           # 报告输出
└── workspace/               # 运行时中间文件
```

## GitHub Actions 配置

在仓库 Settings → Secrets 中添加：
- `API_KEY` — OpenAI 兼容 API 密钥（必需）
- `BASE_URL` — API 地址（可选）
- `MODEL` — 模型名称（可选）
- `SUPADATA_API_KEY` — YouTube 字幕 API（可选）

## Claude Code Skill

在 Claude Code 中触发：
- "科技日报" / "AI日报" → 科技新闻
- "播客日报" / "播客更新" → 播客
- "微信日报" / "公众号更新" → 微信公众号
- "全部日报" / "日报" → 先询问要获取哪些源

## 依赖

- **GitHub Actions 模式**：feedparser, openai, python-dotenv, beautifulsoup4, requests
- **Skill 模式**：纯 Python 标准库（零依赖）

## License

MIT
