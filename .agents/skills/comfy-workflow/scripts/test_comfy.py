import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import comfy


def workflow():
    def node(node_id, kind, values, inputs=()):
        return {"id": node_id, "type": kind, "widgets_values": values,
                "inputs": [{"name": name, "link": link} for name, link in inputs]}

    loras = [{"on": i == 1, "lora": name, "strength": strength, "strengthTwo": None}
             for i, (name, strength) in enumerate([
                 ("example.safetensors", .8), ("fix.safetensors", 1),
                 ("lenovo.safetensors", 1.5), ("realism.safetensors", .6)])]
    return {"nodes": [
        node(1, "UnetLoaderGGUF", ["model.gguf"]),
        node(2, "CLIPLoader", ["encoder.safetensors", "qwen_image", "default"]),
        node(3, "VAELoader", ["vae.safetensors"]),
        node(4, "EmptyLatentImage", [1376, 768, 1]),
        node(5, "TextEncodeQwenImage21", ["", "", 1024], [("prompt", 1), ("negative_prompt", 2), ("images.image_1", 3)]),
        node(6, "KSampler", [123, "randomize", 35, 2, "er_sde", "simple", 1]),
        node(10, "Power Lora Loader (rgthree)", [{}, {"type": "header"}, *loras, {}, ""]),
        node(11, "MirrorOptionalReferenceImage", ["old-user-image.png", True, "image"]),
        node(24, "RepeatLatentBatch", [1]),
        node(25, "PrimitiveStringMultiline", ["old prompt"]),
        node(26, "PrimitiveStringMultiline", ["original negative"]),
        node(27, "PrimitiveStringMultiline", ["maintained suffix"]),
        node(28, "StringConcatenate", ["", "", "\n"], [("string_a", 4), ("string_b", 5)]),
    ], "links": [[1, 28, 0, 5, 0, "STRING"], [2, 26, 0, 5, 1, "STRING"],
                 [3, 11, 0, 5, 2, "IMAGE"], [4, 25, 0, 28, 0, "STRING"], [5, 27, 0, 28, 1, "STRING"]]}


def arguments(*extra):
    args = comfy.parser().parse_args(["generate", "--prompt", '红猫，牌子写着 "hello"\n$(literal) `literal`', "--seed", "42", *extra])
    args.prompt_text, args.negative_text = args.prompt, args.negative
    return args


class WorkflowTests(unittest.TestCase):
    def test_unified_loader_preserves_model_sampling_and_loras(self):
        for name in ['qwen-int8.safetensors', 'unsloth/qwen-Q8.gguf', 'abenzerps/qwen-turbo-Q8.gguf']:
            with self.subTest(model=name):
                source = workflow()
                baseline = comfy.configure(comfy.api_graph(source), arguments(), [])
                source['nodes'][0].update(type='MirrorDiffusionModelLoader', widgets_values=[name, 'default'])
                graph = comfy.configure(comfy.api_graph(source), arguments(), [])
                available = {node['class_type']: {} for node in graph.values()}
                for kind, field, choices in [
                    ('MirrorDiffusionModelLoader', 'unet_name', [name]),
                    ('CLIPLoader', 'clip_name', ['encoder.safetensors']),
                    ('VAELoader', 'vae_name', ['vae.safetensors']),
                    ('LoraLoader', 'lora_name', ['fix.safetensors']),
                ]:
                    available[kind] = {'input': {'required': {field: [choices]}}}
                comfy.validate_available(graph, available)
                self.assertEqual(comfy.summary(graph)['model'], name)
                self.assertEqual(graph['1']['inputs'], {'unet_name': name, 'weight_dtype': 'default'})
                self.assertEqual(graph['6'], baseline['6'])
                self.assertEqual(graph['10'], baseline['10'])

    def test_convrot_loader_can_be_checked_and_prepared_without_submission(self):
        source = workflow()
        source['nodes'][0].update(type='UNETLoader', widgets_values=['convrot.safetensors', 'default'])
        graph = comfy.configure(comfy.api_graph(source), arguments(), [])
        available = {node['class_type']: {} for node in graph.values()}
        for kind, field, choices in [
            ('UNETLoader', 'unet_name', ['convrot.safetensors']),
            ('CLIPLoader', 'clip_name', ['encoder.safetensors']),
            ('VAELoader', 'vae_name', ['vae.safetensors']),
            ('LoraLoader', 'lora_name', ['fix.safetensors']),
        ]:
            available[kind] = {'input': {'required': {field: [choices]}}}
        comfy.validate_available(graph, available)
        self.assertEqual(comfy.summary(graph)['model'], 'convrot.safetensors')
        self.assertEqual(graph['1']['inputs'], {'unet_name': 'convrot.safetensors', 'weight_dtype': 'default'})
        available['UNETLoader']['input']['required']['unet_name'][0] = []
        with self.assertRaisesRegex(comfy.ComfyError, '缺少模型'):
            comfy.validate_available(graph, available)

    def test_default_preserves_sampling_and_all_loras_but_clears_old_reference(self):
        source = workflow()
        graph = comfy.configure(comfy.api_graph(source), arguments(), [])
        self.assertEqual(graph["25"]["inputs"]["value"], arguments().prompt_text)
        self.assertEqual(graph["26"]["inputs"]["value"], "original negative")
        self.assertEqual(graph["27"]["inputs"]["value"], "maintained suffix")
        self.assertEqual(graph["6"]["inputs"], dict(seed=42, steps=35, cfg=2, sampler_name="er_sde", scheduler="simple", denoise=1))
        self.assertEqual(graph["11"]["inputs"], {"image": "", "enabled": False})
        self.assertEqual([v["on"] for v in comfy.summary(graph)["loras"]], [False, True, False, False])
        self.assertEqual(comfy.summary(graph)["loras"][0]["lora"], "example.safetensors")
        self.assertEqual(source["nodes"][7]["widgets_values"][0], "old-user-image.png")

    def test_only_requested_overrides_are_applied(self):
        graph = comfy.configure(comfy.api_graph(workflow()), arguments(
            "--no-loras", "--lora", "example.safetensors=0.4", "--cfg", "3",
            "--steps", "40", "--negative", "blur", "--width", "1024", "--height", "1024", "--batch-size", "2"), ["uploaded.png"])
        self.assertEqual(graph["11"]["inputs"]["image"], "uploaded.png")
        self.assertTrue(graph["11"]["inputs"]["enabled"])
        self.assertEqual(graph["4"]["inputs"], dict(width=1024, height=1024, batch_size=1))
        self.assertEqual(graph["24"]["inputs"]["amount"], 2)
        self.assertEqual(graph["26"]["inputs"]["value"], "blur")
        self.assertEqual([v["on"] for v in comfy.summary(graph)["loras"]], [True, False, False, False])
        self.assertEqual(comfy.summary(graph)["loras"][0]["strength"], .4)

    def test_rejects_unadapted_workflow_and_bad_overrides(self):
        changed = workflow()
        changed["nodes"][0]["type"] = "DifferentModelLoader"
        with self.assertRaises(comfy.ComfyError):
            comfy.api_graph(changed)
        for args, refs in [(arguments("--width", "1024"), []), (arguments(), ["1.png", "2.png"]),
                           (arguments("--lora", "missing.safetensors=1"), [])]:
            with self.subTest(args=args, refs=refs), self.assertRaises(comfy.ComfyError):
                comfy.configure(comfy.api_graph(workflow()), args, refs)

    def test_dry_run_never_uploads_submits_or_creates_records(self):
        class Fake:
            def workflow(self, path):
                return workflow()

            def request(self, path):
                assert path == "/object_info"
                return {}

        with tempfile.TemporaryDirectory() as temp, patch.object(comfy, "ROOT", Path(temp)), patch.object(comfy, "validate_available"):
            image = Path(temp) / "ref.png"
            image.write_bytes(b"reference")
            args = arguments("--reference", str(image), "--dry-run")
            result = comfy.generate(Fake(), {"workflow": "example.json"}, args)
            self.assertTrue(result["dry_run"])
            self.assertEqual(len(result["uploads"]), 1)
            self.assertFalse((Path(temp) / ".runtime").exists())


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.posts = []
        self.history_reads = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.startswith("/history/"):
                    owner.history_reads += 1
                    data = {} if owner.history_reads == 1 else {"job": {"status": {"completed": True, "status_str": "success"}, "outputs": {"8": {"images": [{"filename": "cat.png", "subfolder": "", "type": "output"}]}}}}
                elif self.path == "/queue":
                    data = {"queue_running": [], "queue_pending": []}
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.end_headers()
                    self.wfile.write(b"complete-image")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def do_POST(self):
                owner.posts.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"validation failed"}')

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = comfy.Client(f"http://127.0.0.1:{self.server.server_port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_completion_between_history_and_queue_is_not_lost(self):
        result = self.client.status("job")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["images"][0]["filename"], "cat.png")

    def test_downloads_image_and_preserves_existing_file(self):
        self.history_reads = 1
        with tempfile.TemporaryDirectory() as temp:
            first = comfy.download(self.client, "job", temp)
            path = Path(first["files"][0])
            self.assertEqual(path.read_bytes(), b"complete-image")
            path.write_bytes(b"already-downloaded")
            comfy.download(self.client, "job", temp)
            self.assertEqual(path.read_bytes(), b"already-downloaded")

    def test_http_error_preserves_server_message_and_does_not_retry(self):
        with self.assertRaisesRegex(comfy.ComfyError, "validation failed"):
            self.client.request("/prompt", {"prompt": {}})
        self.assertEqual(len(self.posts), 1)


if __name__ == "__main__":
    unittest.main()
