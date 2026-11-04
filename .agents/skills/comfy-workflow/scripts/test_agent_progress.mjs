import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

async function fixture(baseUrl = "http://localhost:8188/") {
  const events = [];
  const timers = [];
  const sockets = [];
  let queue = { queue_running: [] };
  let history = {};
  const basePath = new URL(baseUrl).pathname.replace(/\/$/, "");
  const api = {
    apiURL: (path) => basePath + (path.startsWith("/api") ? path : "/api" + path),
    fileURL: (path) => basePath + path,
    dispatchEvent: (event) => events.push({ type: event.type, detail: event.detail }),
    fetchApi: async (path) => ({ ok: true, json: async () => path === "/queue" ? queue : history }),
  };
  class Socket {
    constructor(url) { this.url = url; sockets.push(this); }
    close() { this.onclose?.(); }
    message(type, data) { this.onmessage?.({ data: JSON.stringify({ type, data }) }); }
  }
  const context = vm.createContext({ CustomEvent, URL, Blob, DataView, console, api, app: { registerExtension() {} } });
  const source = await readFile(new URL("../assets/comfy-agent-progress.js", import.meta.url), "utf8");
  const module = new vm.SourceTextModule(source, { context });
  await module.link(async (name) => {
    const field = name.endsWith("api.js") ? "api" : "app";
    return new vm.SyntheticModule([field], function () { this.setExport(field, context[field]); }, { context });
  });
  await module.evaluate();
  const monitor = module.namespace.createAgentMonitor({
    api, WebSocket: Socket, location: { href: baseUrl },
    setTimeout: (fn) => { timers.push(fn); return timers.length; }, clearTimeout() {},
  });
  const id = "example-job";
  return { events, sockets, monitor, id,
    runJob: () => { queue = { queue_running: [[1, id, {}, { client_id: `comfy-skill-${id}` }]] }; },
    otherJob: () => { queue = { queue_running: [[1, id, {}, { client_id: "normal-ui" }]] }; },
    complete: (status) => { queue = { queue_running: [] }; history = { [id]: { status } }; },
    poll: async () => { await timers.shift()(); },
  };
}

test("uses the native WebSocket path so Desktop output downloads do not duplicate /api", async () => {
  for (const baseUrl of ["http://localhost:8188/", "https://example.com/comfy/"]) {
    const f = await fixture(baseUrl);
    f.runJob();
    await f.monitor.start();
    const socketUrl = new URL(f.sockets[0].url);
    const basePath = new URL(baseUrl).pathname.replace(/\/$/, "");
    assert.equal(socketUrl.pathname, basePath + "/ws");
    assert.equal(socketUrl.protocol, baseUrl.startsWith("https:") ? "wss:" : "ws:");
    // Desktop derives the output URL from the socket URL and appends /api/view.
    const downloadBase = socketUrl.href.replace(/^ws(s?):/, "http$1:").replace(/\/ws(\?.*)?$/, "");
    assert.equal(new URL(downloadBase + "/api/view").pathname, basePath + "/api/view");
    f.monitor.stop();
  }
});

test("attaches to an existing Agent task and forwards progress into the UI", async () => {
  const f = await fixture();
  f.runJob();
  await f.monitor.start();
  const socket = f.sockets[0];
  assert.equal(new URL(socket.url).searchParams.get("clientId"), `comfy-skill-${f.id}`);
  socket.onopen();
  socket.message("status", { sid: "secondary-session" });
  socket.message("progress", { prompt_id: "unrelated", value: 1, max: 5 });
  socket.message("executing", { node: "6" });
  socket.message("progress", { prompt_id: f.id, node: "6", value: 18, max: 35 });
  assert.deepEqual(f.events.map((e) => e.type), ["execution_start", "executing", "progress"]);
  assert.equal(f.events[1].detail, "6");
  assert.equal(f.events[2].detail.value, 18);
  socket.message("execution_success", { prompt_id: f.id });
  assert.deepEqual(f.events.slice(-2).map((e) => e.type), ["execution_success", "executing"]);
  assert.equal(f.events.at(-1).detail, null);
  f.monitor.stop();
});

test("ordinary UI jobs are left alone", async () => {
  const f = await fixture();
  f.otherJob();
  await f.monitor.start();
  assert.equal(f.sockets.length, 0);
  assert.equal(f.events.length, 0);
  f.monitor.stop();
});

test("lost sockets reconnect; a missed completion is recovered from history", async () => {
  const f = await fixture();
  f.runJob();
  await f.monitor.start();
  f.sockets[0].close();
  await f.poll();
  assert.equal(f.sockets.length, 2);
  f.sockets[1].onopen();
  f.complete({ completed: true, status_str: "success" });
  await f.poll();
  assert.equal(f.events.at(-2).type, "execution_success");
  assert.equal(f.events.at(-1).detail, null);
  f.monitor.stop();
});

test("a failed task is reported as an error rather than success", async () => {
  const f = await fixture();
  f.runJob();
  await f.monitor.start();
  f.sockets[0].onopen();
  f.complete({ status_str: "error", messages: [["execution_error", { exception_message: "test failure" }]] });
  await f.poll();
  assert.equal(f.events.at(-2).type, "execution_error");
  assert.equal(f.events.at(-2).detail.exception_message, "test failure");
  f.monitor.stop();
});
