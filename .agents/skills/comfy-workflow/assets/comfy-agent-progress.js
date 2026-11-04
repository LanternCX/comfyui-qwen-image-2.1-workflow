import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";

// Agent jobs use their own client ID. Forward their events into this UI's
// existing progress handlers without changing how either client submits jobs.
export function createAgentMonitor({ api, WebSocket, location, setTimeout, clearTimeout, prepareJob = async () => {} }) {
  let active = null;
  let timer = null;
  let stopped = false;
  let polling = false;
  const emit = (type, detail) => api.dispatchEvent(new CustomEvent(type, { detail }));

  function disconnect() {
    if (!active) return;
    const previous = active;
    active = null;
    previous.socket.onmessage = null;
    previous.socket.close();
  }

  function finish(type, detail) {
    if (!active) return;
    emit(type, { ...detail, prompt_id: active.id });
    emit("executing", null);
    disconnect();
  }

  function connect(item) {
    const id = item[1];
    const clientId = item[3]?.client_id;
    // Match the native socket path: Desktop appends /api/view to its base URL.
    const url = new URL(api.fileURL("/ws"), location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.searchParams.set("clientId", clientId);
    const socket = new WebSocket(url.href);
    socket.binaryType = "arraybuffer";
    active = { id, socket };
    socket.onopen = () => {
      if (active?.socket !== socket) return;
      // Also attach to a job already in progress when this UI was opened.
      emit("execution_start", { prompt_id: id });
    };
    socket.onmessage = (event) => {
      if (active?.socket !== socket) return;
      if (typeof event.data !== "string") {
        const bytes = event.data;
        if (bytes.byteLength >= 8) {
          const header = new DataView(bytes);
          if (header.getUint32(0) === 1) {
            const type = header.getUint32(4) === 2 ? "image/png" : "image/jpeg";
            emit("b_preview", new Blob([bytes.slice(8)], { type }));
          }
        }
        return;
      }
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      const { type, data } = message;
      // Ignore the secondary socket's status/sid: the UI keeps its own session.
      if (!data || (data.prompt_id && data.prompt_id !== id)) return;
      if (type === "executing") {
        if (data.node == null) finish("execution_success", data);
        else emit(type, data.display_node ?? data.node);
      } else if (["execution_success", "execution_error", "execution_interrupted"].includes(type)) {
        finish(type, data);
      } else if (["execution_start", "execution_cached", "executed", "progress", "progress_state", "progress_text"].includes(type)) {
        emit(type, data);
      }
    };
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      if (active?.socket === socket) active = null;
    };
  }

  async function poll() {
    if (stopped || polling) return;
    polling = true;
    try {
      const response = await api.fetchApi("/queue");
      if (!response.ok) throw new Error(`Queue HTTP ${response.status}`);
      const queue = await response.json();
      const item = (queue.queue_running || []).find((job) =>
        typeof job[3]?.client_id === "string" && job[3].client_id === `comfy-skill-${job[1]}`
      );
      if (active && item?.[1] !== active.id) {
        const response = await api.fetchApi(`/history/${encodeURIComponent(active.id)}`);
        if (!response.ok) throw new Error(`History HTTP ${response.status}`);
        const history = await response.json();
        const entry = history[active.id];
        if (entry?.status?.status_str === "error") {
          const error = entry.status.messages?.find(([type]) => type === "execution_error")?.[1];
          finish("execution_error", error || {});
        } else if (entry?.status?.completed || entry?.status?.status_str === "success") {
          finish("execution_success", {});
        }
      }
      if (!active && item) {
        await prepareJob(item);
        if (!stopped) connect(item);
      }
    } catch (error) {
      console.debug("Comfy Agent progress: reconnecting", error);
    } finally {
      polling = false;
      if (!stopped) timer = setTimeout(poll, 1000);
    }
  }

  return {
    start: poll,
    stop() {
      stopped = true;
      clearTimeout(timer);
      disconnect();
    },
  };
}

app.registerExtension({
  name: "comfy-workflow.agent-progress",
  setup() {
    const monitor = createAgentMonitor({
      api, WebSocket, location, setTimeout, clearTimeout,
      async prepareJob(item) {
        // The maintained workflow uses rgthree's existing progress bar.
        // Supply its node metadata, just as a submission from this UI would.
        try {
          const { SERVICE } = await import("../../rgthree/common/prompt_service.js");
          SERVICE.getOrMakePrompt(item[1]).setPrompt({ output: item[2] });
        } catch (error) {
          console.debug("Comfy Agent progress: optional rgthree metadata unavailable", error);
        }
      },
    });
    window.addEventListener("pagehide", () => monitor.stop(), { once: true });
    monitor.start();
  },
});
