# Qwen Image 2.1 ComfyUI 工作流

用于文字生图和参考图生成的 ComfyUI 工作流，附带可选参考图节点和中文界面标签。

## 安装

1. 准备支持 `TextEncodeQwenImage21`、`ResolutionSelector` 和 `ComfySwitchNode` 的 ComfyUI 环境。
2. 安装依赖插件：
   - [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)
   - [rgthree-comfy](https://github.com/rgthree/rgthree-comfy)
3. 将本仓库的 `custom_nodes/mirror_workflow_tools` 文件夹复制到 ComfyUI 的 `custom_nodes` 目录，重启 ComfyUI 并刷新页面。
4. 将 [Qwen_Image_2.1.json](workflows/Qwen_Image_2.1.json) 拖入 ComfyUI，加载工作流。
5. 自行准备模型权重，在工作流中选择已安装的模型；按需选择 LoRA，未安装的条目请关闭或移除。

仓库不包含模型、LoRA 权重或示例图片。工作流中保存的文件名仅用于引用，需根据本机安装情况调整。

## 使用

填写提示词，按需上传并启用参考图，然后运行工作流。不使用参考图时，关闭相应输入即可进行文字生图。生成结果可在 ComfyUI 中预览，并在其输出目录中查看。

具体选项和参数直接在工作流中查看、调整。

## 常见问题

- **导入后提示缺少节点**：确认依赖插件与本仓库附带的自定义节点均已安装，并重启 ComfyUI；若缺少上述内置节点，请检查 ComfyUI 版本是否支持。
- **找不到模型或 LoRA**：确认权重已放入 ComfyUI 对应的模型目录，再在工作流中重新选择。
- **参考图未生效**：确认图片已上传且相应输入已启用；未选择或文件已不存在的参考图会自动跳过。
