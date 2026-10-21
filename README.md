# MEDUSA 手稿转写工作台

给 **MEDUSA 0.2（Qwen3.5-VL，4B / 9B）** 用的本地 ALTO 工作台：

- 扫描一个工作区里的 **ALTO XML + 页面图片**，按 `TextLine` 切分，优先使用 `Polygon` 轮廓；
- 在 **WSL（Ubuntu）** 里加载模型，逐行裁剪、批量推理，结果缓存到磁盘；
- 浏览器里并排两栏：左边原图 + 浅蓝底深蓝描边的多边形，右边同位置的模型输出；
- 两栏可拖动平移、滚轮缩放，滚动与缩放始终同步；
- 侧栏可下载 / 切换 **4B 与 9B** 权重（9B 自动 4-bit），模型统一放在 `models/`；
- 最左侧是工作区所有页面的竖向缩略图列表。

```
┌──────────┬─────────────────────┬─────────────────────┐
│ 缩略图    │  图像 + 行框          │  转写（同位置、自动缩放） │
│ page_01  │  ┌───────────────┐  │  ┌───────────────┐  │
│ page_02  │  │ ▭ line 1      │  │  │ et repleti …  │  │
│ page_03  │  │ ▭ line 2      │  │  │ ab omni malo… │  │
└──────────┴─────────────────────┴─────────────────────┘
```

## 目录

```
.
├── models/                Git 忽略；权重下载到这里
│   ├── README.md
│   ├── Medusa0.2Line-4B/  默认权重（本地存在，不提交）
│   └── Medusa0.2Line-9B/  可选（网页下载，不提交）
├── fonts/                 Junicode.ttf / Italic / Bold / BoldItalic + OFL
├── workspace/             Git 忽略；ALTO + 图片、结果、缓存
├── server.py              FastAPI：工作区、任务队列、模型下载/切换、静态页面
├── alto.py                ALTO 解析（命名空间无关，支持 pixel/mm10/…）
├── inference.py           Qwen3.5-VL 封装：懒加载、批量生成、OOM 自动降 batch、4-bit
├── static/                前端（原生 HTML/CSS/JS，无构建步骤）
├── tools/
│   ├── make_sample.py     生成示例手稿页 + ALTO
│   ├── test_model.py      WSL 里单行推理冒烟测试
│   ├── test_4bit.py       4-bit 加载冒烟测试
│   └── test_polygon_only.py
├── setup.sh               一次性安装 WSL 依赖（torch cu128 + transformers 5.x）
├── run.sh                 在 WSL 启动 uvicorn
├── run.bat                Windows 双击启动（调用 WSL）
├── .gitignore
└── README.md
```

**仓库不包含模型权重和手稿数据**：`models/`、`workspace/` 都被 `.gitignore` 排除。克隆后从网页的
**模型** 面板下载权重，或把已有权重放进 `models/Medusa0.2Line-4B/`。

## 启动

本机已经装好 `Ubuntu` 发行版和 `~/.venvs/medusa`，直接在仓库根目录双击：

```powershell
.\run.bat
```

然后打开 <http://localhost:8000>。

在新机器上：

```powershell
wsl --install -d Ubuntu
wsl -d Ubuntu -u root -- bash -lc "apt-get update && apt-get install -y python3-venv python3-pip"
wsl -d Ubuntu -u root -- bash -lc "cd /mnt/c/path/to/this/repo && bash setup.sh"
```

`run.bat` 会自动把当前目录通过 `wslpath` 传给 WSL，不依赖固定路径。也可以手动：

```bash
cd /mnt/c/path/to/this/repo
bash setup.sh
bash run.sh
```

如果浏览器打不开 `localhost`（NAT 模式），在 WSL 里执行 `hostname -I` 用返回的 IP 访问。
`run.sh` 绑定 `0.0.0.0`，端口用 `PORT=8001 bash run.sh` 可改。

## 模型下载与切换

默认权重在 `models/Medusa0.2Line-4B`。侧栏的 **模型 · 4B** 按钮打开模型管理：

- **4B**：本机已下载，约 9 GB，bf16 加载；[ENC-PSL/Medusa0.2Line-4B](https://huggingface.co/ENC-PSL/Medusa0.2Line-4B)
- **9B**：从 [ENC-PSL/Medusa0.2Line-9B](https://huggingface.co/ENC-PSL/Medusa0.2Line-9B) 下载，约 **18.8 GB**。12 GB 显存会自动使用 **4-bit（NF4）**，需要 `bitsandbytes`（`setup.sh` 已包含）；
- 下载显示百分比、字节数、速度和当前文件，支持取消和断点续传；下载完成后点 **使用此模型** 才会切换；
- 切换是懒加载：下一次识别时才载入新权重；识别任务或下载进行中会拒绝切换；
- 选择会写入 `models/.active`，重启后自动恢复（显式设置 `MEDUSA_MODEL_PATH` 时以环境变量为准）；
- 下载的模型统一落在 `models/<模型名>/`。

当前网络无法直连 huggingface.co，下载器默认走 `https://hf-mirror.com`；如果能直连，可用
`MEDUSA_HF_ENDPOINT=https://huggingface.co` 覆盖。

## 数据约定

一个页面 = 一个 `.xml` / `.alto` 文件 + 一张图：

1. 优先读 ALTO 的 `<sourceImageInformation><fileName>`（相对/绝对路径都行）；
2. 否则找同名图片（`page_01.xml` ↔ `page_01.jpg`）；
3. 再否则找文件名以 ALTO 名开头的图片。

坐标处理：

- 用 `<Page WIDTH/HEIGHT>` 与图片实际像素的**比例**缩放，因此 `pixel`、`mm10`、`inch1200` 都能直接用；
- 行框取 `TextLine` 的 `HPOS/VPOS/WIDTH/HEIGHT`；缺失时用其 `String` 子元素求并集；
- 检测到整页坐标超出 Page 边界时，会尝试按 `PrintSpace` 的偏移平移（部分导出器这样写）；
- 多边形 `POINTS` 同时支持 `x,y x,y …` 和 `x y x y …` 两种写法（eScriptorium 用后者），解析后用于**显示**和**裁剪遮罩**；没有多边形时退回矩形，右栏文字仍按外接矩形排版；
- `String` 文本只作为对照（tooltip / 底部细节栏的 GT），不参与推理。

上传：点左上角 `＋`，可一次选 ALTO 和图片；文件落到 `workspace/uploads/<时间戳>/`。
也可以直接把文件拷进 `workspace/` 再点 `↻`。

## 界面

- **侧栏**：竖排缩略图，绿/黄/灰点表示已识别/部分/未识别；`识别全部` 按工作区顺序排队。
- **左栏**：页面原图，每行按 ALTO `Polygon` 画浅蓝半透明、深蓝描边的多边形（没有多边形时退回矩形）；悬停变亮，点击选中后整行变**橙黄色**。
- **右栏**：同尺寸空白页，在完全相同的相对位置放输出文本；多边形轮廓同样保留，文字仍按外接矩形排版。
  - 文字拟合：**始终保持单行**（换行和多余空白折叠为空格），字号按框高铺满；超宽时优先 `scaleX` 横向压扁，压缩超过约 3.3 倍才略微缩小字号，不再换行。
- **底部信息条**常驻，高度与左下角模型状态栏一致：未选中时显示提示；选中行后显示行 ID、坐标、耗时、完整转写和 GT，文本可直接拖选复制。
- **双击任意行**（左栏多边形或右栏文本皆可）打开悬浮编辑窗：上方是该行放大的原图，下方是可编辑的转写；窗口可拖动，`保存` 会写回结果文件并立即更新页面，`Ctrl/Cmd+Enter` 保存、`Esc` 关闭。
- **拖动平移**：在任意一栏按住左键拖动，两栏一起滚动；拖动超过 4px 不会误触发行选中。
- **滚轮缩放**：以光标位置为锚点缩放两栏，滚动位置保持同步；点缩放百分比可回到当前适配比例。
- 两栏滚动同步；`适宽 / 整页 / ±` 控制缩放，缩放后文字重新排版。
- 右上角 **提示词** 按钮打开编辑器：预设、自由编辑、最大生成长度、是否重跑已有结果。
- 使用自定义提示词时按钮上会出现黄点，旁边出现 **恢复默认**，一键回到官方两行版本。

## 提示词

默认使用模型卡给出的官方两行版本（模型针对它优化过，建议作为基线）：

```
Transcribe the handwritten text in this line image.
Output ONLY the transcription.
```

对话框里提供 4 个预设，点一下即可填入，也可以自由修改：

| 预设 | 内容要点 |
|---|---|
| 官方推荐 | 默认两行版本 |
| 保留缩写 | 显式要求 `Keep abbreviations exactly as written` |
| 展开缩写 | 要求把缩写补全为完整字母形式（可按语言自行补充规则） |
| CATMuS 详细 | DocWorkflow 配置里的长版本：保留缩写、统一 u/i、不记录异体字 |

使用方式：

- 顶栏 **提示词** → 选预设或直接编辑 → **保存**。改动保存在 localStorage（`medusa.settings.v2`）。
- 顶栏 **恢复默认**（仅当当前提示词不是官方版本时出现）会立即还原并提示，不需要打开对话框。
- 对话框左下角的 **恢复官方默认** 只重置输入框，点 **保存** 后才生效；**取消** 放弃本次改动。
- 每页结果 JSON 都会记录当时使用的 `prompt`，便于对比不同提示词的效果。
- 提示词不会自动触发重跑：要比较差异，勾选「重跑已有结果」后再识别。

模型对措辞比较敏感，展开缩写这类改动可能同时影响拼写和 u/v 规范化；建议官方版本和自定义版本各跑一页对照。

## 字体

转写文本（右侧行框、底部信息条、悬浮编辑框）优先使用 **Junicode**，以覆盖中世纪字符：

- 字体已随仓库提供：`fonts/Junicode.ttf`、`Junicode-Italic.ttf`、`Junicode-Bold.ttf`、`Junicode-BoldItalic.ttf`（SIL OFL 1.1，见 `fonts/OFL.txt`）；
- 也会扫描 `workspace/` 和 `workspace/fonts/`；可以用 `MEDUSA_FONT_DIR` 指定额外目录；
- `/fonts.css` 会自动生成 `@font-face`（按文件名识别 italic / bold），`/fonts/<文件名>` 提供字体文件；
- 找不到 Junicode 时自动回退 Georgia / Times New Roman，界面不受影响。

## 后端

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `MEDUSA_MODEL_PATH` | `models/Medusa0.2Line-4B` | 当前激活的权重目录（相对仓库根目录） |
| `MEDUSA_MODELS_DIR` | `models/` | 模型根目录，下载也落在这里 |
| `MEDUSA_HF_ENDPOINT` | `https://hf-mirror.com` | 模型下载源；能直连 HF 时可设为 `https://huggingface.co` |
| `MEDUSA_WORKSPACE` | `workspace/` | 数据目录 |
| `MEDUSA_MODE` | `auto` | `auto` / `model` / `mock` |
| `MEDUSA_BATCH` | `4` | 生成 batch，OOM 时自动减半 |
| `MEDUSA_MAX_PIXELS` | `401408` | 单行裁剪的像素上限（约 1792×224） |
| `MEDUSA_MAX_NEW_TOKENS` | `160` | 默认最大生成长度 |
| `MEDUSA_4BIT` | `auto` | `auto` 按权重/显存自动选择；`1` 强制 NF4；`0` 强制 bf16 |
| `MEDUSA_DISPLAY_EDGE` | `3000` | 浏览器显示图最长边，超出则缓存降采样 JPEG |
| `MEDUSA_FONT_DIR` | 空 | 额外字体目录；默认扫描 `fonts/`、`workspace/`、`workspace/fonts/` |
| `MEDUSA_ATTN` | 空 | 例如 `sdpa` / `flash_attention_2` |
| `MEDUSA_WARMUP` | `1` | 加载后跑一次空图预热 |

没有 CUDA / 没装 torch / 模型目录不存在时，会自动退回 **mock 后端**：直接把 ALTO 里的 GT
当输出返回，界面和任务流程仍然可用（右下角显示紫色“模拟后端”）。也可以 `MEDUSA_MODE=mock`
强制开启。

结果文件 `workspace/.results/<docId>.json`，按行保存，任务每完成一个 batch 就落盘一次：

```json
{
  "lines": { "L0004": { "text": "non ita impii …", "ms": 400 } },
  "prompt": "Transcribe the handwritten text …",
  "backend": "model",
  "updated": 1792543310.2
}
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/docs` | 工作区页面列表 + 状态 |
| GET | `/api/docs/{id}` | 页面详情（尺寸、行框、已有结果） |
| GET | `/api/docs/{id}/image` · `/thumb` | 显示图 / 缩略图 |
| GET | `/api/docs/{id}/lines/{lineId}/crop` | 行放大图（悬浮编辑窗用） |
| PATCH | `/api/docs/{id}/lines/{lineId}` | 保存手工修改，body `{text}` |
| GET | `/fonts.css` · `/fonts/{name}` | Junicode `@font-face` 与字体文件 |
| GET | `/api/models` | 本地模型、磁盘剩余、下载进度 |
| POST | `/api/models/download` | 开始下载，body `{id}` |
| POST | `/api/models/download/cancel` | 取消当前下载 |
| POST | `/api/models/activate` | 切换激活模型，body `{id}` |
| POST | `/api/jobs` | `{docIds, prompt, maxNewTokens, force}`，入队 |
| GET | `/api/jobs/{id}` | 进度 + 增量文本 |
| POST | `/api/jobs/{id}/cancel` | 取消（在 batch 之间生效） |
| POST | `/api/upload` | multipart `files` |
| POST | `/api/rescan` | 重新扫描 |
| GET | `/api/backend` · `/api/config` | 模型状态 / 默认提示词 |

## 实测（RTX 4070 SUPER 12GB）

- 4B bf16：加载 43s，峰值显存 **9.2GB**；22 行约 10s（热启动）。
- 4B NF4（4-bit）：加载约 56s，峰值显存 **3.4GB**，用于验证 9B 的量化路径。
- 模型下载（hf-mirror）：约 11 MB/s；支持取消/续传，18.8GB 的 9B 预计 30 分钟左右。
- torch 2.11 + CUDA 12.8，transformers 5.20，Ubuntu 26.04（WSL2）。

## 已知限制

- 手工修改只写回转写文本（结果 JSON 里带 `edited: true`），不会自动重新识别，也不做 CATMuS 后处理；CATMuS 规范化仍建议交给 DocWorkflow。
- 右栏文字布局用外接矩形（多边形只用于轮廓和取景遮罩），因此极斜行的文字可能略微越过多边形边线。
- 任务在进程内排队，重启后不恢复；同一时间只有一个 GPU 任务。
- 页面特别多时首次扫描要逐个解析 XML（结果按 mtime 缓存在内存里）。

## 上传到 GitHub

仓库已经 `git init` 并提交到 `main` 分支；`.gitignore` 排除了：

- `models/`（所有权重，包括 `.active`）
- `workspace/`（你的 ALTO、图片、结果、缓存）
- `__pycache__/`、虚拟环境和大模型文件

推送：

```bash
git remote add origin git@github.com:<你的用户名>/<仓库名>.git
git push -u origin main
```

> 请用 git / GitHub Desktop 推送，不要用网页拖拽整个文件夹：网页上传会绕过 `.gitignore`，而且权重远超 GitHub 的单文件限制。
