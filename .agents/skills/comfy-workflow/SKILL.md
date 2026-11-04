---
name: comfy-workflow
description: 通过现有 SSH 隧道调用维护中的 Qwen Image 2.1 ComfyUI 工作流，根据对话要求生图、使用参考图、查询任务和取回图片。适用于能执行本地命令的 Agent，无需 MCP 或独立聊天前端。
---

# ComfyUI 图片生成

把用户的画面要求整理成生成提示词，通过附带脚本提交给现有工作流，并把结果图片交给用户。用户提供的完整提示词按原文使用；只有用户要求生成或调整提示词时才改写。具体视觉要求优先于自行补充的风格。

本 Skill 放在项目的 `.agents/skills/comfy-workflow/`，供 Codex 和 Pi 共同发现。从此项目启动 Agent；Codex 可用 `$comfy-workflow`，Pi 可用 `/skill:comfy-workflow` 显式调用。已开启的 Pi 会话使用 `/reload` 重新加载；首次进入项目时按 Pi 提示信任该项目。

以下命令从项目根目录执行；在其他目录使用本 Skill 下 `scripts/comfy.py` 的绝对路径。脚本只使用 Python 标准库，通过 `uv run --no-project python` 运行，不需要安装包。运行记录与图片始终保存在项目根目录的 `.runtime/`。

## 连接与默认设置

首次使用时，将 [connection.example.json](connection.example.json) 复制为同目录的 `connection.json`，填写自己的连接地址与 SSH 配置。`connection.json` 已被 Git 忽略，不提交真实服务器信息。

```bash
uv run --no-project python .agents/skills/comfy-workflow/scripts/comfy.py check
```

连接失败时运行 `connect`，然后再次 `check`。`connect` 建立后台 SSH 隧道；未配置免密且没有可复用连接时，在交互终端输入 SSH 密码，不把密码写入文件或命令。如果 SSH 已连通但 ComfyUI 未启动，报告服务状态；不要反复建立隧道。

```bash
uv run --no-project python .agents/skills/comfy-workflow/scripts/comfy.py connect
```

连接地址、SSH 端口及工作流路径由本地 `connection.json` 维护。迁移到其他实例时更新这份配置即可。`check` 返回模型、LoRA、采样参数和节点可用性。

调用脚本支持 `UNETLoader`（包括 INT8 ConvRot safetensors）、`UnetLoaderGGUF` 和统一模型选择器 `MirrorDiffusionModelLoader`，保留工作流所选权重及加载精度。统一选择器仅切换模型，不自动修改采样参数或 LoRA。遇到“尚未适配节点”时检查脚本的节点转换，不要直接判定服务器或模型不可用。

每次提交读取服务端已保存的工作流，浏览器内未保存的改动不会自动采用。默认保留模型、LoRA 开关与强度、CFG、Steps、采样器、调度器、尺寸和附加提示词。四个参考图输入默认清空，只有本次明确传入的图片才会启用，避免沿用上次的参考图。种子默认随机，指定 `--seed` 可复现。

## 生成与参考图

较长提示词放进 `.runtime/` 下的 UTF-8 文本文件，使用可审阅的文件编辑工具写入，避免 shell 转义破坏内容。

```bash
uv run --no-project python .agents/skills/comfy-workflow/scripts/comfy.py generate \
  --prompt-file .runtime/prompt.txt
```

短提示词可使用 `--prompt '一只坐在窗边的橘猫，自然光摄影'`。参考图由脚本上传，无需手工复制到远端：

```bash
uv run --no-project python .agents/skills/comfy-workflow/scripts/comfy.py generate \
  --prompt-file .runtime/prompt.txt --reference /absolute/path/reference.png
```

`--reference` 可以重复使用，按顺序对应 `<image1>` 到 `<image4>`。再次编辑生成结果时，把下载的结果作为新的参考图。生成工具能执行要求，但不能保证每次都满足全部视觉条件；支持视觉的 Agent 可查看结果，其他 Agent 根据用户反馈调整，不声称自己已经看过图片。

只在用户需要时覆盖参数：`--negative-file` / `--negative`、`--suffix`、`--seed`、`--steps`、`--cfg`、`--sampler`、`--scheduler`、`--width` 与 `--height`、`--batch-size`、`--reference-resolution`。宽高必须同时给出。

LoRA 默认完全沿用工作流。`--no-loras` 关闭所有 LoRA，`--lora '文件名.safetensors=0.8'` 按文件名开启并设置强度，`--disable-lora '文件名.safetensors'` 单独关闭；可重复指定。不得因文件名自行移除条目，也不擅自开启原本关闭的 LoRA。

`generate --dry-run` 只返回即将提交的 API 图和参考图上传计划，不上传图片、不提交生成、不改服务端工作流。先用它检查重要的参数覆盖。

## 任务与结果

远端界面扩展 `assets/comfy-agent-progress.js` 会把本 Skill 提交的任务进度接入 ComfyUI 原有进度条、节点高亮和预览。用户打开连接到同一服务的 ComfyUI APP 后即可查看，Agent 继续通过命令提交和查询。界面在任务中途打开时也会接收后续进度；加载模型和编码阶段可能只有节点状态，没有百分比。一次使用一个界面观察 Agent 任务，避免多个观察窗口争用任务连接。

扩展部署到服务端当前前端的 `extensions/comfy-agent-progress.js`，刷新界面后加载，无需重启生成服务。源码以本 Skill 的 `assets/` 为准；升级或更换 ComfyUI 前端后如果扩展丢失，需要重新部署。不要为显示进度重新提交用户正在运行的任务。

`generate` 返回 `prompt_id`。使用这个 ID 查询和下载，不重新提交同一个任务：

```bash
uv run --no-project python .agents/skills/comfy-workflow/scripts/comfy.py status PROMPT_ID
uv run --no-project python .agents/skills/comfy-workflow/scripts/comfy.py wait PROMPT_ID --timeout 300
uv run --no-project python .agents/skills/comfy-workflow/scripts/comfy.py download PROMPT_ID
```

等待超时后任务仍会继续，不代表失败；继续查询原 ID。生成提交遇到网络错误时也不自动重提，先根据返回的 ID 查状态、核对队列。任务失败时报告 ComfyUI 的错误；不擅自切换模型、重启服务或无限重试。

运行记录保存在 `.runtime/jobs/`，图片默认下载到 `.runtime/results/PROMPT_ID/`，也可指定 `download --output-dir /absolute/path`。输出为 JSON，错误时退出码非零。返回给用户可点击的绝对图片路径；支持内嵌图片的客户端直接展示图片。已有下载文件会跳过，不覆盖。

本 Skill 不需要额外调用一个 LLM：当前 Agent 负责理解要求与生成提示词，脚本负责执行工作流。无需修改全局 Agent、Magpie 或 MCP 配置。
