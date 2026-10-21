import { app } from "../../../scripts/app.js";

const nodeTypes = new Set([
  "UnetLoaderGGUF", "CLIPLoader", "VAELoader", "ResolutionSelector",
  "EmptyLatentImage", "TextEncodeQwenImage21", "Power Lora Loader (rgthree)",
  "ComfySwitchNode", "KSampler", "VAEDecode", "SaveImage", "PreviewAny",
  "QwenImage21_T2IPromptRewrite", "QwenImage21_EditPromptRewrite",
  "MirrorOptionalReferenceImage", "MirrorAnyReferenceActive", "RepeatLatentBatch",
]);

const labels = {
  unet_name: "Model", clip_name: "CLIP", type: "模型类型",
  device: "Device", vae_name: "VAE", model: "Model",
  clip: "CLIP", vae: "VAE", image: "参考图片",
  enabled: "启用此参考图", upload: "上传参考图", reference_active: "参考图有效",
  mask: "Mask", prompt: "提示词", negative_prompt: "负面提示词",
  temperature: "Temperature", top_p: "Top-p", top_k: "Top-k",
  presence_penalty: "Presence penalty", max_new_tokens: "Max tokens",
  max_length: "Max tokens", seed: "Seed",
  control_after_generate: "生成后种子策略", positive_prompt: "扩写提示词",
  wh_ratio: "建议宽高比", ratio_follow: "参考图比例来源",
  thinking: "推理过程", parse_ok: "解析成功", positive: "Positive",
  negative: "Negative", resolution: "参考图分辨率",
  width: "宽度", height: "高度", batch_size: "基础批次大小",
  amount: "批量张数", samples: "Latent", latent: "Latent",
  latent_image: "Latent", steps: "Steps", cfg: "CFG",
  sampler_name: "Sampler", scheduler: "Scheduler", denoise: "Denoise",
  aspect_ratio: "宽高比", megapixels: "百万像素", multiple: "尺寸对齐倍数",
  preview: "尺寸预览", on_false: "文生图 Latent", on_true: "参考图 Latent",
  switch: "使用参考图", output: "Latent", images: "图片",
  filename_prefix: "保存路径前缀", source: "预览内容", preview_text: "扩写结果",
  preview_mode: "预览格式", MODEL: "Model", CLIP: "CLIP",
  VAE: "VAE", IMAGE: "Image", MASK: "Mask", LATENT: "Latent",
  BOOLEAN: "有效参考图", STRING: "文本", "➕ Add Lora": "➕ 添加 LoRA",
};

const optionLabels = {
  control_after_generate: {
    fixed: "固定", increment: "递增", decrement: "递减", randomize: "随机",
  },
  device: { default: "自动", cpu: "CPU" },
  aspect_ratio: {
    "1:1 (Square)": "1:1（正方形）", "2:3 (Portrait Photo)": "2:3（竖幅照片）",
    "3:2 (Photo)": "3:2（横幅照片）", "3:4 (Portrait Standard)": "3:4（标准竖幅）",
    "4:3 (Standard)": "4:3（标准横幅）", "9:16 (Portrait Widescreen)": "9:16（竖屏）",
    "16:9 (Widescreen)": "16:9（宽屏）", "21:9 (Ultrawide)": "21:9（超宽屏）",
  },
  preview_mode: { false: "纯文本", true: "Markdown" },
};

const tooltips = {
  temperature: "控制扩写的随机性；数值越低通常越稳定。",
  top_p: "按累计概率筛选候选词，通常保持默认值即可。",
  top_k: "每一步最多保留的候选词数量，通常保持默认值即可。",
  presence_penalty: "减少扩写内容的重复；文生图增强模型建议保持 1.5。",
  max_new_tokens: "扩写长度上限。过低可能截断结果；实际耗时取决于生成长度。",
  max_length: "扩写长度上限。过低可能截断结果。",
  resolution: "参考图按此目标分辨率缩放并保持比例；0 表示使用原图尺寸。",
  multiple: "宽高对齐到此数值的整数倍，通常保持默认值即可。",
  amount: "同一提示词一次生成的图片数量；增加张数会增加显存占用。",
};

function localize(node) {
  if (!nodeTypes.has(node.type)) return;
  for (const widget of node.widgets ?? []) {
    if (labels[widget.name]) widget.label = labels[widget.name];
    if (tooltips[widget.name]) widget.tooltip = tooltips[widget.name];
    if (widget.type === "toggle") {
      widget.options.on = widget.name === "preview_mode" ? "Markdown" : "开启";
      widget.options.off = widget.name === "preview_mode" ? "纯文本" : "关闭";
    }
    const values = optionLabels[widget.name];
    if (values) {
      widget.options.getOptionLabel = value => values[value] ?? value ?? "";
    }
    if (widget.element && labels[widget.name]) {
      const fields = widget.element.matches?.("textarea,input")
        ? [widget.element] : widget.element.querySelectorAll?.("textarea,input") ?? [];
      for (const field of fields) field.placeholder = labels[widget.name];
    }
  }
  for (const slot of [...(node.inputs ?? []), ...(node.outputs ?? [])]) {
    const imageIndex = slot.name.match(/^(?:images\.)?image_(\d+)$/);
    const referenceIndex = slot.name.match(/^reference_(\d+)$/);
    const label = imageIndex ? `image_${imageIndex[1]}`
      : referenceIndex ? `参考图 ${referenceIndex[1]} 有效` : labels[slot.name];
    if (label) {
      slot.label = label;
      slot.localized_name = label;
    }
  }
}

app.registerExtension({
  name: "mirror-workflow.chinese-labels",
  nodeCreated: localize,
  loadedGraphNode: localize,
});
