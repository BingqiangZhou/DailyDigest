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
TECH_OUTPUT_DIR = OUTPUT_DIR / "tech"
PODCAST_OUTPUT_DIR = OUTPUT_DIR / "podcast"

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
    "AGI", "AIGC", "生成式", "智能体", "AI Agent", "RAG", "微调",
    "开源模型", "闭源模型", "多模态", "文生图",
    "文生视频", "语音识别", "NLP", "计算机视觉", "强化学习",
    "芯片", "GPU", "TPU", "算力", "AI芯片", "英伟达", "NVIDIA",
]
AI_KEYWORDS_EN = [
    "AI", "artificial intelligence", "LLM", "GPT", "Claude", "Gemini",
    "machine learning", "deep learning", "neural network", "transformer",
    "AGI", "AIGC", "generative", "AI agent", "RAG", "fine-tun",
    "inference", "open-source model", "multimodal", "text-to-image",
    "text-to-video", "NLP", "computer vision", "reinforcement learning",
    "GPU", "TPU", "AI chip", "NVIDIA", "deepseek", "anthropic",
    "openai", "google ai", "meta ai", "copilot", "chatbot",
]

# ============================================================
# Editorial Pipeline Configuration
# ============================================================

# Enable/disable the editorial pipeline (scoring, tiering, depth allocation)
EDITORIAL_ENABLED = os.environ.get("EDITORIAL_ENABLED", "true").lower() != "false"

# Minimum news value score to pass the initial filter
# Raised from 0.15 to 0.25 — the old threshold let nearly everything through
EDITORIAL_NEWS_VALUE_THRESHOLD = float(os.environ.get("EDITORIAL_THRESHOLD", "0.25"))

# Tier thresholds on news_value_score
EDITORIAL_TIER_MUST_READ = float(os.environ.get("TIER_MUST_READ", "0.70"))
EDITORIAL_TIER_NOTEWORTHY = float(os.environ.get("TIER_NOTEWORTHY", "0.40"))

# HN points threshold for tier promotion
EDITORIAL_HN_PROMOTE_THRESHOLD = int(os.environ.get("HN_PROMOTE_THRESHOLD", "200"))

# ============================================================
# Source Authority & Signal Keywords
# ============================================================

# High-signal keywords that boost importance score
HIGH_SIGNAL_KEYWORDS = {
    "release", "launch", "announce", "breakthrough", "first", "record",
    "acquire", "acquisition", "merger", "benchmark", "SOTA", "open-source",
    "regulation", "ban", "restrict", "发布", "推出", "首次", "突破", "收购",
    "合并", "开源", "禁止", "限制", "监管",
}

# Source authority weight mapping (domain -> tier weight)
AUTHORITY_DOMAINS = {
    # Tier 1 (weight 1.0) — AI labs, foundational research
    "openai.com": 1.0, "anthropic.com": 1.0, "ai.google": 1.0,
    "deepmind.google": 1.0, "ai.meta.com": 1.0, "llama.meta.com": 1.0,
    "arxiv.org": 1.0, "blog.google": 1.0, "research.google": 1.0,
    "deepseek.com": 1.0, "mistral.ai": 1.0, "x.ai": 1.0,
    "allenai.org": 1.0, "eleuther.ai": 1.0,
    # Tier 1 — top-tier research institutions
    "hai.stanford.edu": 1.0, "csail.mit.edu": 1.0,
    "microsoft.com": 1.0, "nature.com": 1.0,
    # Tier 1 — landmark individual blogs
    "karpathy.github.io": 1.0, "lilianweng.github.io": 1.0,
    "simonwillison.net": 1.0, "sebastianraschka.com": 1.0,
    # Tier 2 (weight 0.85) — premium tech media & analysis
    "stratechery.com": 0.85, "semianalysis.com": 0.85,
    "theinformation.com": 0.85, "technologyreview.com": 1.0,
    "quantamagazine.org": 0.85, "platformer.news": 0.85,
    "404media.co": 0.85, "ben-evans.com": 0.85,
    "economist.com": 0.85, "ft.com": 0.85,
    # Tier 2 (weight 0.7) — established tech media
    "techcrunch.com": 0.7, "theverge.com": 0.7, "wired.com": 0.7,
    "arstechnica.com": 0.7, "github.com": 0.7, "huggingface.co": 0.7,
    "venturebeat.com": 0.7, "the-decoder.com": 0.7,
    "theregister.com": 0.7, "thenewstack.io": 0.7,
    "reuters.com": 0.7, "paperswithcode.com": 0.7,
    "semanticscholar.org": 0.7, "thegradient.pub": 0.7,
    "lesswrong.com": 0.7, "alignmentforum.org": 0.7,
    "krebsonsecurity.com": 0.7, "bleepingcomputer.com": 0.7,
    # Tier 2 — engineering blogs from major companies
    "blog.cloudflare.com": 0.7, "stripe.com": 0.7,
    "vercel.com": 0.7, "supabase.com": 0.7, "cursor.com": 0.7,
    "eng.uber.com": 0.7, "netflixtechblog.com": 0.7,
    "shopify.engineering": 0.7, "stackoverflow.blog": 0.7,
    "blog.langchain.dev": 0.7, "wandb.ai": 0.7,
    # Tier 2 — Chinese media
    "36kr.com": 0.7, "jiqizhixin.com": 0.7, "机器之心": 0.7,
    "量子位": 0.7, "infoq.cn": 0.7, "新智元": 0.7,
    # Tier 3 (weight 0.55) — aggregators, product review sites
    "producthunt.com": 0.55, "lobste.rs": 0.55, "reddit.com": 0.55,
    "hnrss.org": 0.55, "dev.to": 0.55,
    "appleinsider.com": 0.55, "macrumors.com": 0.55,
    "ithome.com": 0.55, "engadget.com": 0.55,
}

# ============================================================
# AI Prompt Templates (lazy-loaded from config/prompts/)
# ============================================================

# Prompts are loaded on first access to avoid crashing core.config
# if config/prompts/ has issues.  Importers can still write:
#   from .config import AI_FILTER_PROMPT_ZH
# thanks to module-level __getattr__.

_PROMPT_NAMES = {
    "AI_FILTER_PROMPT_ZH",
    "AI_FILTER_PROMPT_EN",
    "AI_DEEP_ANALYSIS_PROMPT_ZH",
    "AI_DEEP_ANALYSIS_PROMPT_EN",
    "WECHAT_STRUCTURE_PROMPT_ZH",
}

def __getattr__(name):
    if name in _PROMPT_NAMES:
        from config import prompts as _prompts
        value = getattr(_prompts, name)
        # Cache on the module so __getattr__ is only called once per name
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

