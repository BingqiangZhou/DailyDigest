"""AI filter prompt templates."""

AI_FILTER_PROMPT_ZH = """你是一位AI领域内容分类专家。请判断以下每篇文章是否与AI/机器学习/大模型/AI应用/AI芯片/AI工具等主题直接相关。

相关（宽松）：
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

请自行判断，不要展示分析过程。

## 文章列表

{articles}

## 输出格式

严格只输出一个 JSON 对象，不要输出 Markdown、解释、代码块或额外文字：
{{{{"1": true, "2": false, "...": false}}}}

key 必须是文章编号字符串，value 必须是布尔值（true=AI相关，false=不相关）。"""

AI_FILTER_PROMPT_EN = """You are an AI domain content classifier. Determine whether each article below is directly related to AI/machine learning/LLMs/AI applications/AI chips/AI tools.

Relevant (lenient):
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

Decide silently and do not show your reasoning.

## Article List

{articles}

## Output Format

Output exactly one JSON object. No Markdown, no explanation, no code fences:
{{{{"1": true, "2": false, "...": false}}}}

Keys must be article-number strings and values must be booleans."""
