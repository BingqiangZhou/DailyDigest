# 播客音频生成设计

为科技日报和播客日报添加双人播客音频，将 Markdown 日报转换为轻松闲聊风格的双人对话播客 MP3。

## 方案

单文件流式生成：LLM 一次性生成完整双人对话脚本 → edge-tts 逐条合成 → pydub 拼接为 MP3。

## 数据流

```
日报 Markdown (.md)
  → 提取关键内容（TL;DR + 主题标题 + 摘要）
  → LLM 生成双人对话脚本（JSON 格式）
  → edge-tts 按角色分段合成音频
  → pydub 拼接为单个 MP3
  → 保存到 daily-digests/podcast_audio/
```

## 模块设计

### core/podcast_generator.py

播客生成主模块，职责：

1. **内容提取** — 从 Markdown 日报中提取 TL;DR 和各主题的标题+摘要
2. **脚本生成** — 调用 LLM 生成双人对话脚本（JSON 数组）
3. **音频合成** — edge-tts 按角色音色逐条合成
4. **音频拼接** — pydub 拼接所有片段 + 静音间隔，导出 MP3

对外接口：

```python
def generate_podcast_audio(report_path: str, report_type: str, date: str) -> str | None:
    """从日报 Markdown 生成播客 MP3。返回 MP3 路径或 None（失败时）。"""
```

### config/prompts/podcast_script.py

双人对话脚本生成的 prompt 模板。

**输入：** TL;DR + 主题标题/摘要 + 日报类型标识

**角色设定：**
- 主持人 A（`zh-CN-YunxiNeural`，男声）：主导话题，引导流程，提问和总结
- 主持人 B（`zh-CN-XiaoxiaoNeural`，女声）：补充观点，提供背景和分析

**Prompt 要求：**
- 轻松闲聊风格，自然口语化
- 总时长 5-10 分钟（约 2000-3500 字中文）
- 包含开场白、各主题讨论、结束语
- 提炼要点、加入观点，不逐条念新闻
- 纯文字，不含 markdown 格式、括号注释或舞台指示
- 输出严格为 JSON 数组 `[{speaker: "A"|"B", text: "..."}, ...]`

**调用方式：** 复用 `core/llm_services.py` 中的 LLM 调用，一次调用生成完整脚本。

## TTS 合成

- **edge-tts** — 免费，中文支持好，无需 API key
- **pydub** — 音频拼接（依赖 ffmpeg，GitHub Actions 已预装）

**合成流程：**
1. 遍历对话脚本，按 speaker 选择音色
2. edge-tts 逐条合成音频片段（in-memory BytesIO）
3. 所有片段间统一插入 0.3s 静音间隔
4. pydub 拼接，导出为 MP3（64kbps）

**超时处理：** 单条合成 30s 超时，整体 5 分钟总超时。

## Pipeline 集成

在 `core/pipeline.py` 的 `run_tech_unified()` 和 `run_podcast()` 末尾：

```python
if api_key:
    generate_podcast_audio(report_path, report_type="tech"|"podcast", date=date)
```

仅 API 模式下触发。Skill 模式跳过。

## CLI 变更

新增 `--podcast-only` 参数，从已有日报单独生成播客：

```bash
python main.py --podcast-only tech      # 科技日报播客
python main.py --podcast-only podcast   # 播客日报播客
python main.py --podcast-only all       # 两个都生成
```

## 输出

- `daily-digests/podcast_audio/{date}_tech.mp3` — 科技日报播客
- `daily-digests/podcast_audio/{date}_podcast.mp3` — 播客日报播客

预期大小：5-10 分钟 @ 64kbps ≈ 2.5-5 MB。

## 错误处理

- 播客生成失败（LLM 超时/TTS 异常/ffmpeg 缺失）→ warning 日志，不影响日报 .md 生成
- 单条 TTS 合成失败重试 1 次，仍失败则跳过该条并打日志
- edge-tts 限流：逐条串行合成，不额外加速
- 内容过长：LLM prompt 中明确 3500 字上限

## 依赖

pyproject.toml 新增：
- `edge-tts`
- `pydub`

## 文件变更清单

**新增：**
- `core/podcast_generator.py`
- `config/prompts/podcast_script.py`

**修改：**
- `pyproject.toml` — 新增依赖
- `core/pipeline.py` — 末尾调用播客生成
- `main.py` — 新增 `--podcast-only` 参数

**无需修改：**
- `.github/workflows/digest.yml` — 新依赖通过 pyproject.toml 自动安装
