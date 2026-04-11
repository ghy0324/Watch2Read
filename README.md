# Watch2Read

> 将 B 站视频转化为结构化的 Markdown 阅读笔记 —— 看视频太慢，不如读笔记。

## 已整理视频

<!-- VIDEO_TABLE_START -->
| 发布日期 | UP主 | 视频名称 | 时长 | 笔记 |
|----------|------|----------|------|------|
| 2026-03-26 | [KrillinAI小林](https://space.bilibili.com/242124650) | [【人物访谈】 杰弗里·辛顿毫无保留谈论人工智能的未来](https://www.bilibili.com/video/BV1GFXJBzEhf) | 1:01:11 | [【人物访谈】 杰弗里·辛顿毫无保留谈论人工智能的未来.md](notes/【人物访谈】%20杰弗里·辛顿毫无保留谈论人工智能的未来.md) |
| 2026-03-21 | [Easonlee的AI笔记](https://space.bilibili.com/3546559488723681) | [【干货】Karpathy最新访谈：Code Agent，Auto Research和AI的自我循环时代](https://www.bilibili.com/video/BV1dwAczDEXY) | 1:06:32 | [【干货】Karpathy最新访谈.md](notes/【干货】Karpathy最新访谈.md) |
| 2026-03-16 | [张小珺商业访谈录](https://space.bilibili.com/280780745) | [对谢赛宁的7小时马拉松访谈：世界模型、逃出硅谷、反OpenAI、AMI Labs、两次拒绝Ilya、杨立昆、李飞飞和42](https://www.bilibili.com/video/BV1tew5zVEDf) | 6:44:38 | [对谢赛宁的7小时马拉松访谈.md](notes/对谢赛宁的7小时马拉松访谈.md) |
| 2026-03-06 | [硅谷101](https://space.bilibili.com/508452265) | [全面解析“世界模型”：定义、路线、实践与AGI的更近一步【硅谷101】](https://www.bilibili.com/video/BV11LPWzNEkm) | 49:36 | [全面解析“世界模型”.md](notes/全面解析“世界模型”.md) |
| 2026-02-23 | [小Lin说](https://space.bilibili.com/520819684) | [黄金白银大崩盘，谁是幕后推手？](https://www.bilibili.com/video/BV1MxfcBhEdo) | 19:36 | [黄金白银大崩盘，谁是幕后推手？.md](notes/黄金白银大崩盘，谁是幕后推手？.md) |
| 2026-01-17 | [WhynotTV](https://space.bilibili.com/14145636) | [翁家翌：OpenAI，GPT，强化学习，Infra，后训练，天授，tuixue，开源，CMU，清华｜WhynotTV Podcast #4](https://www.bilibili.com/video/BV1darmBcE4A) | 2:02:45 | [翁家翌.md](notes/翁家翌.md) |
<!-- VIDEO_TABLE_END -->

## 动机

现在有大量高质量的长视频内容——深度技术分享、学术讲解、人物访谈、行业分析——它们的信息密度极高，往往是一个领域里最好的学习材料。但问题也很明显：一个深度访谈动辄一两个小时甚至更长，很难找到一整块时间从头看到尾。收藏夹里积压的"稍后再看"越来越多，大部分永远不会被打开。

网上当然也有各种总结和拆解，但通常过于精炼——一个小时的对话被压缩成几百字的要点，大量有价值的细节、论证过程、具体案例和微妙的观点碰撞都被丢掉了。你读完之后知道"他聊了什么话题"，但并不真正理解"他到底说了什么"。

Watch2Read 想解决的就是这个中间地带：**在"看完整个视频"和"只看一段摘要"之间，提供一种高效但不损失关键信息的阅读方式。** 它将视频自动转化为结构化的 Markdown 长文，保留所有重要观点、数据、论据和上下文，同时通过结构化排版让你能够快速浏览、按需深入。

核心诉求：

- **节省时间**：阅读速度远快于视频播放速度，一篇笔记十几分钟读完，原视频可能要看一两个小时。
- **保留细节**：与简单摘要不同，本工具追求"不丢失重要信息"的结构化整理，保留关键观点、论证逻辑、具体数据和术语，让你读完之后真正理解内容，而不只是知道一个大概。
- **易于回顾**：Markdown 纯文本，可全文检索、版本管理、集成到个人知识库。不像视频那样需要拖进度条去找"他说那句话在哪里"。
- **可跳转原片**：每个章节标题带有时间戳超链接，读到感兴趣的部分可以一键跳转到视频对应位置观看原片。

## 用法

### 前置条件

1. Python 3.10+
2. 安装依赖：

```bash
pip install requests pycryptodome
```

如需使用 `--to-pdf` 生成 PDF，请额外安装：

```bash
pip install markdown weasyprint
```

3. 准备 API 配置文件 `api_config.json`（兼容 OpenAI API 格式的大模型服务）：

```json
{
  "base_url": "https://your-api-endpoint/v1",
  "api_key": "sk-your-api-key",
  "model": "model-name"
}
```

### 运行

```bash
# 基本用法（单个视频）
python main.py -l "https://www.bilibili.com/video/BVxxxxxxx/" -c api_config.json

# 一次处理多个视频（逐个串行）
python main.py -l "https://www.bilibili.com/video/BV1xxx/" "https://www.bilibili.com/video/BV2yyy/" -c api_config.json

# 流水线模式：所有视频按步骤统一推进，LLM 步骤跨视频并行
python main.py -l "https://www.bilibili.com/video/BV1xxx/" "https://www.bilibili.com/video/BV2yyy/" -c api_config.json --pipeline

# 保留所有中间文件
python main.py -l "https://www.bilibili.com/video/BVxxxxxxx/" -c api_config.json --keep-all

# 额外输出 PDF（默认不生成）
python main.py -l "https://www.bilibili.com/video/BVxxxxxxx/" -c api_config.json --to-pdf

# 指定输出文件名（仅单视频时有效）
python main.py -l "https://www.bilibili.com/video/BVxxxxxxx/" -c api_config.json --name "我的笔记"

# 高并发处理（适用于 API 限流宽松的场景）
python main.py -l "https://www.bilibili.com/video/BVxxxxxxx/" -c api_config.json --workers 50
```

### 静态网页（浏览笔记）

仓库根目录的 `index.html`、`app.js`、`styles.css` 用于在浏览器中查看 `notes/` 下的 Markdown。文件名列表来自根目录的 **`notes-index.json`**（运行 `main.py` 或 `update.py` 后会自动更新；若手动增删 `notes/*.md`，请同步更新该 JSON 或再跑一次脚本）。

在线访问地址：https://ghy0324.github.io/Watch2Read/

本地预览可执行：`python -m http.server`，再打开 http://127.0.0.1:8000/ 。

页面默认为**浅色**，可在顶栏切换**深色**（偏好保存在浏览器 `localStorage`）。**笔记列表**与**本篇目录**均可点击标题栏展开 / 收起（状态同样会记住）。窄屏下正文在上、列表在下；目录在正文下方，便于先读文章再跳转章节。目录由 `h1`–`h4` 生成，支持滚动高亮当前节。

### 修改已有笔记

如果对某个视频的笔记不满意，可以用 `update.py` 交互式地重新生成：

```bash
# 交互式选择单个视频修改
python update.py -c api_config.json

# 更新所有视频，使用流水线模式（LLM 步骤跨视频并行）
python update.py -c api_config.json --pipeline

# 更新时同步生成 PDF（默认不生成）
python update.py -c api_config.json --to-pdf
```

运行后首先选择视频：

- 输入 `0`：更新所有视频（支持 `--pipeline` 流水线并行）
- 输入 `1` ~ `N`：选择单个视频

选定单个视频后显示操作列表：

- 输入 `0`：完全重新生成（从分段开始重跑所有 LLM 步骤）
- 输入 `1` ~ `N`：选择对应的章节。如果该章节包含多个小节，会进一步询问：
  - 输入 `0`：重新生成整章
  - 输入 `1` ~ `N`：只重新生成对应的小节

脚本会自动复用已有的中间文件（`.srt`、`.meta.json`、`.segments.json`），缺失的会自动重新下载或生成。

### 参数说明

| 参数 | 说明 |
|------|------|
| `-l, --link` | B 站视频链接，支持多个（空格分隔，必需） |
| `-c, --config` | API 配置文件路径（必需） |
| `--pipeline` | 流水线模式：多视频时按步骤统一推进，LLM 步骤跨视频并行（仅多视频时生效） |
| `--workers` | 并发线程数（默认 5） |
| `--max-batch-minutes` | 单次 LLM 调用处理的最大时长（分钟），超过的章节会自动切分为多个子批次分别处理，结果合并回同一章节（默认 20） |
| `--keep-srt` | 保留中间 SRT 字幕文件 |
| `--keep-json` | 保留中间 JSON 结构化结果文件 |
| `--keep-meta` | 保留视频元数据 JSON 文件（含简介、置顶评论等） |
| `--keep-segments` | 保留分段结果 JSON 文件（含分段来源标注） |
| `--keep-all` | 保留所有中间过程文件（等同于同时指定所有 `--keep-*` 参数） |
| `--to-pdf` | 渲染 Markdown 后同步导出 PDF（默认关闭） |
| `--name` | 输出文件名（不含扩展名），仅单视频时有效，默认从视频标题自动生成 |
| `--output-dir` | 输出目录（默认 `notes`） |

### 多视频执行模式

传入多个视频链接时，支持两种执行模式：

**默认模式** — 逐个视频完整处理，一个视频走完全部 6 步后再处理下一个：

```
视频A: 字幕→元数据→分段→结构化→渲染→README
视频B: 字幕→元数据→分段→结构化→渲染→README
视频C: 字幕→元数据→分段→结构化→渲染→README
```

**`--pipeline` 流水线模式** — 所有视频按步骤统一推进，涉及 LLM API 调用的步骤跨视频并行执行：

```
Step 1 字幕下载   （串行）：A → B → C
Step 2 元数据获取  （串行）：A → B → C
Step 3 语义分段   （并行）：A | B | C   ← LLM
Step 4 AI 结构化  （并行）：A | B | C   ← LLM
Step 5 渲染 MD    （串行）：A → B → C
Step 6 更新 README（串行）：A → B → C
```

流水线模式下，LLM 步骤（分段 + 结构化）跨视频并发执行，充分利用 API 并发能力，整体耗时更短。非 LLM 步骤保持串行，日志清晰且避免无意义的并发。任一视频在某步失败会被自动跳过，不影响其余视频继续处理。

### 中间文件说明

使用 `--keep-*` 参数保存的中间文件均位于输出目录下，命名规则：

| 文件 | 说明 |
|------|------|
| `{标题}.srt` | 原始 SRT 字幕文件 |
| `{标题}.meta.json` | 视频元数据（标题、UP 主、简介、置顶评论、播放统计等） |
| `{标题}.segments.json` | 分段结果（含 `source` 字段标注来源：`meta` 或 `llm`） |
| `{标题}.json` | AI 结构化整理结果 |
| `{标题}.md` | 最终输出的 Markdown 笔记 |
| `{标题}.pdf` | 可选输出：由 Markdown 转换的 PDF（启用 `--to-pdf` 时生成） |

## Notes

- **平台支持**：目前仅支持 Bilibili 平台。字幕下载依赖视频本身附带的字幕（包括 UP 主上传的字幕和 Bilibili 平台自动生成的 AI 字幕），无字幕的视频暂时无法处理。
- **字幕准确性**：平台自动生成的 AI 字幕不可避免地存在错误，尤其是人名、机构名、专业术语等专有名词。虽然 AI 结构化阶段会尝试根据上下文修正，但仍可能有遗漏，请读者注意甄别。
- **建议观看原片**：本工具生成的笔记旨在帮助快速了解视频内容和事后检索回顾。如果时间充裕，仍然建议观看原视频——视频中的语气、表情、演示等信息是文字无法完全传达的。每个章节标题都附有时间戳链接，方便跳转到感兴趣的片段。

## 实现思路

整体流程分为六个阶段，以管道（pipeline）方式串联：

```
视频链接 → 字幕下载 → 元数据获取 → 语义分段 → AI 结构化 → Markdown 渲染
```

1. **字幕下载**：通过第三方字幕提取服务获取视频的 SRT 格式字幕。
2. **元数据获取**：调用 B 站公开 API 获取视频标题、UP 主、发布日期、时长、简介、置顶评论等信息。
3. **语义分段**：优先从视频简介或置顶评论中提取已有的章节划分（通过 LLM 解析时间戳和层级结构）；若无现成划分，则将完整字幕交给 LLM 按主题变化进行语义分段。
4. **AI 结构化整理**：按分段结果将字幕分批，每批独立送入大语言模型，输出结构化 JSON（二级标题体系 + 要点列表 + 时间戳）。
5. **Markdown 渲染**：将 JSON 结构渲染为带折叠、时间戳跳转链接的 Markdown 文档。
6. **更新索引**：自动将新视频追加到 README 的「已整理视频」表格中（按发布日期从新到旧排列）。

## 具体实现

项目由以下模块组成：

| 文件 | 职责 |
|------|------|
| `main.py` | 主入口，串联整个流水线，处理命令行参数，输出进度信息 |
| `update.py` | 交互式修改已有笔记，支持完全重新生成或按章节单独重跑 |
| `download_subtitle.py` | 字幕下载，处理加密通信、字幕轨道选择、SRT 内容获取 |
| `video_meta.py` | 视频元数据获取，BV 号提取、B 站 API 调用、数据格式化 |
| `segment_video.py` | 语义分段，从简介/评论提取章节或通过 LLM 分析字幕进行语义分段 |
| `structure_subtitle.py` | 字幕结构化，SRT 解析、Prompt 构造、LLM API 调用、JSON 提取 |
| `render_markdown.py` | Markdown 渲染，JSON → 带折叠详情和跳转链接的 Markdown |
| `md2pdf.py` | 可选 PDF 导出，Markdown → PDF（展开 `<details>` 并优化打印样式） |

### 字幕下载 (`download_subtitle.py`)

通过 kedou.life 的字幕提取 API 获取 B 站视频字幕。该 API 需要 RSA + AES 加密通信，模块实现了完整的密钥协商和加密流程。自动优先选择中文字幕轨道。

### 元数据获取 (`video_meta.py`)

通过 B 站公开 Web API (`/x/web-interface/view`) 获取视频信息，支持 BV 号和 AV 号两种格式。返回结构化的元数据字典，包含标题、UP 主（含主页链接）、发布时间、时长、简介、置顶评论、播放统计等。

### 语义分段 (`segment_video.py`)

采用两级分段策略：

1. **优先从元数据提取**：将视频简介和置顶评论交给 LLM，判断是否包含时间戳章节标记。LLM 能理解层级结构（如 `-` 开头的子条目归入上级章节），只提取顶级章节作为分段点。调用前会用正则快速检查是否存在时间戳模式，避免无意义的 API 调用。
2. **LLM 字幕语义分段**（备选）：当简介/评论中无章节信息时，将完整字幕送入 LLM，由模型根据主题变化进行语义分段。超长字幕会自动降采样以适应上下文窗口。

### AI 结构化 (`structure_subtitle.py`)

按分段结果将字幕分批（每个章节对应一个批次），每批独立送入大语言模型进行结构化整理。模型被要求输出扁平的 section 列表 JSON，每个 section 包含标题、TL;DR 摘要、要点列表和起始时间戳。System Prompt 要求去除口语化表达、修正语音识别错误，同时不丢失信息。对于超过 `--max-batch-minutes`（默认 20 分钟）的长章节，会先通过 LLM 进行语义子切分（在主题自然切换处划分，而非机械按时间切断），再分别调用 LLM 进行结构化，最终将各子批次的小节合并回同一章节下展示。

### Markdown 渲染 (`render_markdown.py`)

将结构化 JSON 渲染为最终的 Markdown 文档。每个章节标题附带可点击的 Bilibili 时间戳跳转链接，详细内容使用 HTML `<details>` 标签折叠，方便快速浏览。文档头部包含视频元信息（UP 主及主页链接、日期、时长、链接）。
