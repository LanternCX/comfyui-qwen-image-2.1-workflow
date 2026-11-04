#!/usr/bin/env python3
"""Call the maintained Qwen workflow through ComfyUI's local HTTP API."""

import argparse
import copy
import json
import mimetypes
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
CONFIG = Path(__file__).resolve().parents[1] / "connection.json"
WIDGETS = {
    "UnetLoaderGGUF": ["unet_name"],
    "UNETLoader": ["unet_name", "weight_dtype"],
    "MirrorDiffusionModelLoader": ["unet_name", "weight_dtype"],
    "CLIPLoader": ["clip_name", "type", "device"],
    "VAELoader": ["vae_name"],
    "ResolutionSelector": ["aspect_ratio", "megapixels", "multiple"],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "MirrorOptionalReferenceImage": ["image", "enabled"],
    "TextEncodeQwenImage21": ["prompt", "negative_prompt", "resolution"],
    "KSampler": ["seed", "control_after_generate", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "VAEDecode": [],
    "SaveImage": ["filename_prefix"],
    "RepeatLatentBatch": ["amount"],
    "PrimitiveStringMultiline": ["value"],
    "StringConcatenate": ["string_a", "string_b", "delimiter"],
}


class ComfyError(Exception):
    pass


def api_graph(workflow):
    """Convert this workflow's UI widgets, links and rgthree LoRAs to API inputs."""
    links = {link[0]: link for link in workflow["links"]}
    graph = {}
    for node in workflow["nodes"]:
        node_type = node["type"]
        if node.get("mode", 0) != 0:
            raise ComfyError(f"节点 {node['id']} 被禁用或旁路，请先在 ComfyUI 中保存正常工作流。")
        values = node.get("widgets_values") or []
        if node_type == "Power Lora Loader (rgthree)":
            loras = [v for v in values if isinstance(v, dict) and "lora" in v]
            inputs = {f"lora_{i}": copy.deepcopy(v) for i, v in enumerate(loras, 1)}
        elif node_type in WIDGETS:
            names = WIDGETS[node_type]
            if len(values) < len(names):
                raise ComfyError(f"节点 {node['id']} 的控件格式已改变，请更新调用脚本。")
            inputs = dict(zip(names, values))
            inputs.pop("control_after_generate", None)
        else:
            raise ComfyError(f"尚未适配节点 {node_type}；请先更新工作流适配，不能静默跳过。")
        for inp in node.get("inputs", []):
            if inp.get("link") is not None:
                link = links[inp["link"]]
                inputs[inp["name"]] = [str(link[1]), link[2]]
        graph[str(node["id"])] = {"class_type": node_type, "inputs": inputs}
    return graph


def unique_node(graph, node_type):
    matches = [n for n in graph.values() if n["class_type"] == node_type]
    if len(matches) != 1:
        raise ComfyError(f"工作流需要恰好一个 {node_type} 节点，实际为 {len(matches)}。")
    return matches[0]["inputs"]


def set_linked_value(graph, inputs, key, value):
    source = inputs.get(key)
    if isinstance(source, list):
        node = graph[source[0]]
        if node["class_type"] == "PrimitiveStringMultiline":
            node["inputs"]["value"] = value
            return
    inputs[key] = value


def model_loader(graph):
    matches = [node for node in graph.values() if node['class_type'] in ('UNETLoader', 'UnetLoaderGGUF', 'MirrorDiffusionModelLoader')]
    if len(matches) != 1:
        raise ComfyError(f"工作流需要恰好一个模型加载节点，实际为 {len(matches)}。")
    return matches[0]


def configure(graph, args, reference_names):
    graph = copy.deepcopy(graph)
    encoder = unique_node(graph, "TextEncodeQwenImage21")
    prompt_input = encoder.get("prompt")
    if isinstance(prompt_input, list) and graph[prompt_input[0]]["class_type"] == "StringConcatenate":
        concat = graph[prompt_input[0]]["inputs"]
        set_linked_value(graph, concat, "string_a", args.prompt_text)
        if args.suffix is not None:
            set_linked_value(graph, concat, "string_b", args.suffix)
    else:
        if args.suffix is not None:
            raise ComfyError("当前提示词输入没有独立附加文本节点。")
        set_linked_value(graph, encoder, "prompt", args.prompt_text)
    if args.negative_text is not None:
        set_linked_value(graph, encoder, "negative_prompt", args.negative_text)
    if args.reference_resolution is not None:
        encoder["resolution"] = args.reference_resolution
    slots = sorted((k for k in encoder if k.startswith("images.image_")), key=lambda k: int(k.rsplit("_", 1)[1]))
    if len(reference_names) > len(slots):
        raise ComfyError(f"此工作流最多接收 {len(slots)} 张参考图。")
    for i, slot in enumerate(slots):
        reference = graph[encoder[slot][0]]
        if reference["class_type"] != "MirrorOptionalReferenceImage":
            raise ComfyError("参考图节点已改变，请更新适配。")
        reference["inputs"].update(image=reference_names[i] if i < len(reference_names) else "", enabled=i < len(reference_names))
    sampler = unique_node(graph, "KSampler")
    sampler["seed"] = args.seed if args.seed is not None else secrets.randbits(63)
    for option, field in [("steps", "steps"), ("cfg", "cfg"), ("sampler", "sampler_name"), ("scheduler", "scheduler")]:
        value = getattr(args, option)
        if value is not None:
            sampler[field] = value
    if (args.width is None) != (args.height is None):
        raise ComfyError("请同时指定 --width 和 --height。")
    if args.width is not None:
        unique_node(graph, "EmptyLatentImage").update(width=args.width, height=args.height)
    if args.batch_size is not None:
        unique_node(graph, "EmptyLatentImage")["batch_size"] = 1
        unique_node(graph, "RepeatLatentBatch")["amount"] = args.batch_size
    loader = unique_node(graph, "Power Lora Loader (rgthree)")
    loras = {v["lora"]: v for k, v in loader.items() if k.startswith("lora_")}
    if args.no_loras:
        for value in loras.values():
            value["on"] = False
    for name in args.disable_lora:
        if name not in loras:
            raise ComfyError(f"工作流中没有 LoRA：{name}")
        loras[name]["on"] = False
    for spec in args.lora:
        name, separator, strength = spec.rpartition("=")
        if not separator or name not in loras:
            raise ComfyError(f"请使用工作流内的 LoRA 文件名=强度：{spec}")
        loras[name].update(on=True, strength=float(strength))
    return graph


class Client:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, payload=None, raw=None, content_type=None):
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else raw
        headers = {"Content-Type": content_type or "application/json"} if data is not None else {}
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=30) as response:
                body = response.read()
                if "json" in response.headers.get("Content-Type", ""):
                    return json.loads(body)
                return body
        except urllib.error.HTTPError as exc:
            raise ComfyError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise ComfyError(f"无法访问 ComfyUI：{exc}") from exc

    def workflow(self, path):
        value = self.request("/userdata/" + urllib.parse.quote(path, safe=""))
        return json.loads(value) if isinstance(value, bytes) else value

    def upload(self, path, filename):
        boundary = "comfy-" + uuid.uuid4().hex
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode()
        body += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        result = self.request("/upload/image", raw=body, content_type=f"multipart/form-data; boundary={boundary}")
        return "/".join(filter(None, [result.get("subfolder", ""), result["name"]]))

    def status(self, job_id):
        history = self.request("/history/" + urllib.parse.quote(job_id, safe=""))
        if job_id in history:
            entry = history[job_id]
            state = entry.get("status", {})
            images = [im for output in entry.get("outputs", {}).values() for im in output.get("images", [])]
            if state.get("status_str") == "error":
                return {"prompt_id": job_id, "state": "failed", "messages": state.get("messages", [])}
            if state.get("completed") or state.get("status_str") == "success":
                return {"prompt_id": job_id, "state": "completed", "images": images}
        queue = self.request("/queue")
        for key, state in [("queue_running", "running"), ("queue_pending", "queued")]:
            if any(item[1] == job_id for item in queue.get(key, [])):
                return {"prompt_id": job_id, "state": state}
        # A task may finish between the history and queue reads.
        latest = self.request("/history/" + urllib.parse.quote(job_id, safe=""))
        if job_id in latest:
            entry = latest[job_id]
            state = entry.get("status", {})
            if state.get("status_str") == "error":
                return {"prompt_id": job_id, "state": "failed", "messages": state.get("messages", [])}
            if state.get("completed") or state.get("status_str") == "success":
                return {"prompt_id": job_id, "state": "completed", "images": [im for out in entry.get("outputs", {}).values() for im in out.get("images", [])]}
        return {"prompt_id": job_id, "state": "unknown", "detail": "任务不存在、尚未入队或历史已清除；不要自动重提。"}


def summary(graph):
    return {
        "model": model_loader(graph)["inputs"]["unet_name"],
        "text_encoder": unique_node(graph, "CLIPLoader")["clip_name"],
        "vae": unique_node(graph, "VAELoader")["vae_name"],
        "sampling": unique_node(graph, "KSampler"),
        "loras": [v for k, v in unique_node(graph, "Power Lora Loader (rgthree)").items() if k.startswith("lora_")],
    }


def validate_available(graph, available):
    missing = sorted({node["class_type"] for node in graph.values()} - available.keys())
    if missing:
        raise ComfyError(f"缺少节点：{missing}")
    for node_type, field in [(model_loader(graph)["class_type"], "unet_name"), ("CLIPLoader", "clip_name"), ("VAELoader", "vae_name")]:
        filename = unique_node(graph, node_type)[field]
        choices = available[node_type]["input"]["required"][field][0]
        if filename not in choices:
            raise ComfyError(f"缺少模型：{filename}")
    choices = available["LoraLoader"]["input"]["required"]["lora_name"][0]
    for lora in summary(graph)["loras"]:
        if lora["on"] and lora["lora"] not in choices:
            raise ComfyError(f"缺少已启用的 LoRA：{lora['lora']}")


def check(client, config):
    graph = api_graph(client.workflow(config["workflow"]))
    available = client.request("/object_info")
    validate_available(graph, available)
    return {"base_url": client.base_url, "workflow": config["workflow"], "node_count": len(graph), "queue": client.request("/queue"), **summary(graph)}


def connect(client, config):
    try:
        client.request("/system_stats")
        return {"state": "connected", "base_url": client.base_url}
    except ComfyError:
        pass
    ssh = config["ssh"]
    target = f"{ssh['user']}@{ssh['host']}"
    command = ["ssh", "-S", ssh["control_path"], "-o", "ControlMaster=auto", "-o", "ControlPersist=yes", "-fNT", "-L", f"127.0.0.1:{ssh['local_port']}:127.0.0.1:{ssh['remote_port']}", "-p", str(ssh["port"]), "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", target]
    subprocess.run(command, check=True)
    try:
        client.request("/system_stats")
    except ComfyError as exc:
        raise ComfyError("SSH 隧道已建立，但 ComfyUI 未响应；检查远端服务，勿重复建隧道。") from exc
    return {"state": "connected", "base_url": client.base_url}


def generate(client, config, args):
    args.prompt_text = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
    args.negative_text = Path(args.negative_file).read_text(encoding="utf-8") if args.negative_file else args.negative
    paths = [Path(path).expanduser().resolve(strict=True) for path in args.reference]
    names = [uuid.uuid4().hex + path.suffix.lower() for path in paths]
    graph = configure(api_graph(client.workflow(config["workflow"])), args, names)
    validate_available(graph, client.request("/object_info"))
    if args.dry_run:
        return {"dry_run": True, "prompt": graph, "uploads": [{"path": str(p), "name": n} for p, n in zip(paths, names)], **summary(graph)}
    # Validate all overrides before uploading anything.
    for i, (path, name) in enumerate(zip(paths, names)):
        names[i] = client.upload(path, name)
    graph = configure(graph, args, names)
    job_id = str(uuid.uuid4())
    record = ROOT / ".runtime" / "jobs" / f"{job_id}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    payload = {"prompt_id": job_id, "client_id": "comfy-skill-" + job_id, "prompt": graph}
    record.write_text(json.dumps({"state": "submitting", **payload}, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        result = client.request("/prompt", payload)
    except ComfyError as exc:
        raise ComfyError(f"提交未确认，prompt_id={job_id}；先查询该 ID，不要自动重提。{exc}") from exc
    if result.get("error") or result.get("node_errors"):
        raise ComfyError(json.dumps(result, ensure_ascii=False))
    actual_id = result["prompt_id"]
    record.write_text(json.dumps({"response": result, **payload}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**result, "record": str(record), "requested_prompt_id": job_id, "prompt_id": actual_id}


def wait(client, job_id, timeout, interval):
    deadline = time.monotonic() + timeout
    while True:
        result = client.status(job_id)
        if result["state"] in ("completed", "failed", "unknown"):
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**result, "timed_out": True}
        time.sleep(min(interval, remaining))


def download(client, job_id, directory):
    result = client.status(job_id)
    if result["state"] != "completed":
        raise ComfyError(f"任务尚未成功完成：{json.dumps(result, ensure_ascii=False)}")
    directory = Path(directory or ROOT / ".runtime" / "results" / job_id).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for index, item in enumerate(result["images"], 1):
        filename = Path(item["filename"]).name
        target = directory / f"{index:02d}-{filename}"
        if not target.exists():
            body = client.request("/view?" + urllib.parse.urlencode(item))
            with target.open("xb") as stream:
                stream.write(body)
        files.append(str(target))
    return {"prompt_id": job_id, "files": files}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, default=CONFIG)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("connect")
    gen = commands.add_parser("generate")
    prompts = gen.add_mutually_exclusive_group(required=True)
    prompts.add_argument("--prompt")
    prompts.add_argument("--prompt-file")
    negatives = gen.add_mutually_exclusive_group()
    negatives.add_argument("--negative")
    negatives.add_argument("--negative-file")
    gen.add_argument("--suffix")
    gen.add_argument("--reference", action="append", default=[])
    for name in ["seed", "steps", "width", "height", "batch-size", "reference-resolution"]:
        gen.add_argument("--" + name, type=int)
    gen.add_argument("--cfg", type=float)
    gen.add_argument("--sampler")
    gen.add_argument("--scheduler")
    gen.add_argument("--no-loras", action="store_true")
    gen.add_argument("--lora", action="append", default=[])
    gen.add_argument("--disable-lora", action="append", default=[])
    gen.add_argument("--dry-run", action="store_true")
    for name in ["status", "wait", "download"]:
        cmd = commands.add_parser(name)
        cmd.add_argument("prompt_id", type=valid_job_id)
        if name == "wait":
            cmd.add_argument("--timeout", type=positive_float, default=300)
            cmd.add_argument("--interval", type=positive_float, default=3)
        if name == "download":
            cmd.add_argument("--output-dir")
    return result


def valid_job_id(value):
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("任务 ID 必须是 ComfyUI 返回的 UUID。") from exc


def positive_float(value):
    value = float(value)
    if not 0 < value < float("inf"):
        raise argparse.ArgumentTypeError("数值必须为正且有限。")
    return value


def main():
    args = parser().parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        client = Client(config["base_url"])
        if args.command == "check":
            result = check(client, config)
        elif args.command == "connect":
            result = connect(client, config)
        elif args.command == "generate":
            result = generate(client, config, args)
        elif args.command == "status":
            result = client.status(args.prompt_id)
        elif args.command == "wait":
            result = wait(client, args.prompt_id, args.timeout, args.interval)
        else:
            result = download(client, args.prompt_id, args.output_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("state") in ("failed", "unknown") else 0
    except (ComfyError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
