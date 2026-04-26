"""AI filter prompt templates."""

AI_FILTER_PROMPT_ZH = """你是一位AI领域内容分类专家。请判断以下每篇文章是否与AI/机器学习/大模型/AI应用/AI芯片/AI工具等主题直接相关。

请按以下步骤逐篇分析：

Step 1: 阅读每篇文章的标题和摘要，识别其主要话题
Step 2: 对每篇文章，判断是否符合以下"相关"标准：
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

Step 3: 输出分类结果

自检：标记为AI相关的文章是否真的涉及AI？如果只是提及"智能"但无AI实质内容，应标为false。

## 文章列表

{articles}

## 输出格式

严格按JSON输出，不要输出其他内容：
{{{{"id_1": true, "id_2": false, ...}}}}

其中 key 为文章编号，value 为布尔值（true=AI相关，false=不相关）。"""

AI_FILTER_PROMPT_EN = """You are an AI domain content classifier. Determine whether each article below is directly related to AI/machine learning/LLMs/AI applications/AI chips/AI tools.

Analyze each article step by step:

Step 1: Read each article's title and summary. Identify its primary topic.
Step 2: For each article, determine if it matches these relevance criteria:
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

Step 3: Output the classification results.

Self-check: Are articles marked as AI-related genuinely about AI? If an article only mentions "smart" without substantive AI content, mark it false.

## Article List

{articles}

## Output Format

Strict JSON output, nothing else:
{{{{"id_1": true, "id_2": false, ...}}}}

Key is the article number, value is boolean (true=AI-related, false=not related)."""
