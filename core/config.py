"""
统一配置管理模块
管理 RSS 源配置、分类映射、全局设置等。
"""

import json
import os
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 配置目录
CONFIG_DIR = PROJECT_ROOT / "config"

# 输出目录
OUTPUT_DIR = PROJECT_ROOT / "daily-digests"

# 工作空间目录（运行时中间文件）
WORKSPACE_DIR = PROJECT_ROOT / "workspace"


# ============================================================
# 分类定义
# ============================================================

# 统一分类体系（融合两个项目的分类）
CATEGORIES = {
    # --- 科技 RSS 分类（来自 tech-daily + 当前项目）---
    "ai_ml": {
        "name": "AI/ML",
        "display": "🧠 AI 研究前沿",
        "order": 1,
    },
    "tech_general": {
        "name": "综合科技",
        "display": "💻 科技与 AI 综合",
        "order": 2,
    },
    "ai_tools": {
        "name": "AI 工具",
        "display": "🛠️ AI 工具与应用",
        "order": 3,
    },
    "tech_product": {
        "name": "科技产品",
        "display": "📱 科技产品",
        "order": 4,
    },
    "general_news": {
        "name": "综合新闻",
        "display": "🌍 综合新闻",
        "order": 5,
    },
    # --- tech-daily 独有分类 ---
    "chips_hardware": {
        "name": "芯片硬件",
        "display": "🔧 芯片硬件",
        "order": 6,
    },
    "cloud": {
        "name": "云计算",
        "display": "☁️ 云计算",
        "order": 7,
    },
    "open_source": {
        "name": "开源",
        "display": "📂 开源",
        "order": 8,
    },
    "cybersecurity": {
        "name": "网络安全",
        "display": "🔒 网络安全",
        "order": 9,
    },
    "hacker_news": {
        "name": "Hacker News",
        "display": "🔥 Hacker News 热门",
        "order": 10,
    },
    # --- 播客分类 ---
    "podcast": {
        "name": "播客",
        "display": "🎙️ 播客更新",
        "order": 11,
    },
    # --- 微信公众号分类 ---
    "wechat_security": {
        "name": "安全",
        "display": "🛡️ 微信·安全",
        "order": 12,
    },
    "wechat_dev": {
        "name": "开发",
        "display": "💻 微信·开发",
        "order": 13,
    },
    "wechat_other": {
        "name": "其他",
        "display": "📱 微信·其他",
        "order": 14,
    },
    "wechat_user": {
        "name": "用户提交",
        "display": "👤 微信·用户提交",
        "order": 15,
    },
}

# 分类排序顺序（用于报告输出）
CATEGORY_ORDER = [
    "ai_ml", "tech_general", "ai_tools", "tech_product", "general_news",
    "chips_hardware", "cloud", "open_source", "cybersecurity", "hacker_news",
    "podcast",
    "wechat_security", "wechat_dev", "wechat_other", "wechat_user",
]

# 旧分类到新分类的映射（兼容当前项目的分类名）
LEGACY_CATEGORY_MAP = {
    "ai_research": "ai_ml",
    "tech_ai": "tech_general",
    "ai_tools": "ai_tools",
    "tech_product": "tech_product",
    "general_news": "general_news",
    # 微信分类中文 -> 英文 ID
    "安全": "wechat_security",
    "开发": "wechat_dev",
    "其他": "wechat_other",
    "用户提交": "wechat_user",
}

# Skills 项目分类到新分类的映射
SKILLS_CATEGORY_MAP = {
    "AI/ML": "ai_ml",
    "综合科技": "tech_general",
    "芯片硬件": "chips_hardware",
    "云计算": "cloud",
    "开源": "open_source",
    "网络安全": "cybersecurity",
    "Hacker News": "hacker_news",
}


def get_category_display(category_id):
    """获取分类的显示名称"""
    cat = CATEGORIES.get(category_id, {})
    return cat.get("display", category_id)


def get_category_name(category_id):
    """获取分类的短名称"""
    cat = CATEGORIES.get(category_id, {})
    return cat.get("name", category_id)


def normalize_category(category):
    """将旧分类名或 Skills 分类名标准化为新分类 ID"""
    if category in CATEGORIES:
        return category
    if category in LEGACY_CATEGORY_MAP:
        return LEGACY_CATEGORY_MAP[category]
    if category in SKILLS_CATEGORY_MAP:
        return SKILLS_CATEGORY_MAP[category]
    return category


def load_feed_config(feed_type="tech"):
    """加载指定类型的 RSS 源配置

    Args:
        feed_type: "tech" | "podcast" | "wechat"

    Returns:
        dict: 配置数据
    """
    config_map = {
        "tech": "tech_feeds.json",
        "podcast": "podcast_feeds.json",
        "wechat": "wechat_feeds.json",
    }
    filename = config_map.get(feed_type, "tech_feeds.json")
    config_path = CONFIG_DIR / filename

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dirs(*dirs):
    """确保目录存在"""
    for d in dirs:
        os.makedirs(d, exist_ok=True)


# ============================================================
# AI Digest Configuration
# ============================================================

# Categories whose articles are directly included in the AI digest
AI_DIGEST_DIRECT_CATEGORIES = {"ai_ml", "ai_tools"}

# Fallback AI keywords for keyword-based filtering when API is unavailable
AI_KEYWORDS_ZH = [
    "人工智能", "AI", "大模型", "LLM", "机器学习", "深度学习",
    "神经网络", "GPT", "Claude", "Gemini", "大语言模型", "Transformer",
    "AGI", "AIGC", "生成式", "智能体", "Agent", "RAG", "微调",
    "训练", "推理", "开源模型", "闭源模型", "多模态", "文生图",
    "文生视频", "语音识别", "NLP", "计算机视觉", "强化学习",
    "芯片", "GPU", "TPU", "算力", "AI芯片", "英伟达", "NVIDIA",
]
AI_KEYWORDS_EN = [
    "AI", "artificial intelligence", "LLM", "GPT", "Claude", "Gemini",
    "machine learning", "deep learning", "neural network", "transformer",
    "AGI", "AIGC", "generative", "agent", "RAG", "fine-tun",
    "inference", "open-source model", "multimodal", "text-to-image",
    "text-to-video", "NLP", "computer vision", "reinforcement learning",
    "GPU", "TPU", "AI chip", "NVIDIA", "deepseek", "anthropic",
    "openai", "google ai", "meta ai", "copilot", "chatbot",
]

# Prompt for batch AI-relevance classification
AI_FILTER_PROMPT_ZH = """你是一位AI领域内容分类专家。请判断以下每篇文章是否与AI/机器学习/大模型/AI应用/AI芯片/AI工具等主题直接相关。

相关标准（宽松）：
- 直接讨论AI技术、模型、算法、训练、推理
- AI产品、工具、应用、平台
- AI公司动态（OpenAI、Anthropic、Google DeepMind、Meta AI等）
- AI芯片、算力基础设施
- AI政策、监管、伦理
- 使用AI技术的产品更新

不相关：
- 纯硬件产品发布（非AI芯片）
- 一般软件开发新闻（无AI成分）
- 纯商业/金融新闻

## 文章列表

{articles}

## 输出格式

严格按JSON输出，不要输出其他内容：
{{{{"id_1": true, "id_2": false, ...}}}}

其中 key 为文章编号，value 为布尔值（true=AI相关，false=不相关）。"""

AI_FILTER_PROMPT_EN = """You are an AI domain content classifier. Determine whether each article below is directly related to AI/machine learning/LLMs/AI applications/AI chips/AI tools.

Relevance criteria (lenient):
- Direct discussion of AI technology, models, algorithms, training, inference
- AI products, tools, applications, platforms
- AI company news (OpenAI, Anthropic, Google DeepMind, Meta AI, etc.)
- AI chips, compute infrastructure
- AI policy, regulation, ethics
- Product updates that use AI technology

Not relevant:
- Pure hardware product launches (non-AI chips)
- General software development news (no AI component)
- Pure business/finance news

## Article List

{articles}

## Output Format

Strict JSON output, nothing else:
{{{{"id_1": true, "id_2": false, ...}}}}

Key is the article number, value is boolean (true=AI-related, false=not related)."""

# Prompt for AI deep analysis report
AI_DEEP_ANALYSIS_PROMPT_ZH = """你是一位资深AI行业分析师。请基于以下AI相关文章，生成一份深度分析报告。

你需要扮演"AI领域分析师"角色，提供专业的编辑视角和趋势洞察。

## AI相关文章

{articles}

## 报告要求

请按以下结构输出Markdown格式报告：

### 🔥 今日热点
选取2-3条最重要的AI新闻，每条包含：
- **标题和来源链接**
- 1-2句"为什么重要"的编辑评论

### 📊 趋势洞察
从所有文章中归纳2-3条跨文章的趋势模式，例如：
- 多家公司发布同类产品
- 某项技术从研究走向应用
- 行业格局变化
每条趋势附上支撑论据（引用具体文章）。

### 📰 详细报道

按以下子领域分类，每个领域列出相关文章表格（标题+链接 | 来源 | 核心要点）：

#### 基础模型与研究
（模型发布、研究论文、训练技术等）

#### AI工具与应用
（AI产品、工具、应用场景等）

#### AI硬件与基础设施
（AI芯片、算力、数据中心等）

#### 行业动态与观点
（公司动态、投融资、政策、观点评论等）

如果某个子领域没有相关文章，跳过该子领域。

### 🎙️ AI播客精选
（如果有播客内容被标记为AI相关，列出单集标题+播客名+摘要）

### 📱 AI微信精选
（如果有微信公众号内容被标记为AI相关，列出文章标题+公众号名+摘要）

## 注意事项
- 核心要点应该是提炼的洞察，而非简单转述
- 保持专业但易懂的语调
- 不要编造文章中没有的信息
- 使用中文输出"""

AI_DEEP_ANALYSIS_PROMPT_EN = """You are a senior AI industry analyst. Based on the following AI-related articles, generate a deep analysis report.

You should play the role of an "AI domain analyst", providing professional editorial perspective and trend insights.

## AI-Related Articles

{articles}

## Report Requirements

Output a Markdown report with the following structure:

### 🔥 Hot Topics
Select 2-3 most important AI news items, each with:
- **Title and source link**
- 1-2 sentences of editorial commentary on why it matters

### 📊 Trend Insights
Identify 2-3 cross-article trend patterns from all articles, such as:
- Multiple companies releasing similar products
- A technology moving from research to application
- Industry landscape shifts
Each trend should cite supporting articles.

### 📰 Detailed Coverage

Group articles by the following sub-domains, each with a table (title+link | source | key insight):

#### Foundation Models & Research
(Model releases, research papers, training techniques, etc.)

#### AI Tools & Applications
(AI products, tools, use cases, etc.)

#### AI Hardware & Infrastructure
(AI chips, compute, data centers, etc.)

#### Industry News & Opinions
(Company updates, funding, policy, opinion pieces, etc.)

Skip any sub-domain with no relevant articles.

### 🎙️ AI Podcast Highlights
(If any podcast content is flagged as AI-related, list episode title + podcast name + summary)

### 📱 AI WeChat Highlights
(If any WeChat content is flagged as AI-related, list article title + account name + summary)

## Notes
- Key insights should be extracted observations, not simple restatements
- Professional yet accessible tone
- Do not fabricate information not present in the articles
- Output in English"""
