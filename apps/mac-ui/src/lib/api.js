const API_ROOT = "/v1";

async function request(path, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    },
    ...options
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // Keep the HTTP status when a response has no JSON body.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  health: () => request("/health"),
  startBackend: (provider = "colab") =>
    request(`/backend/start?provider=${encodeURIComponent(provider)}`, { method: "POST" }),
  stopBackend: () => request("/backend/stop", { method: "POST" }),
  listProjects: () => request("/projects"),
  createProject: (project) => request("/projects", { method: "POST", body: JSON.stringify(project) }),
  saveProject: (project) =>
    request(`/projects/${project.project_id}`, { method: "PUT", body: JSON.stringify(project) }),
  uploadReference: async (projectId, role, file) => {
    const form = new FormData();
    form.append("role", role);
    form.append("file", file);
    const response = await fetch(
      `${API_ROOT}/projects/${encodeURIComponent(projectId)}/references`,
      { method: "POST", body: form }
    );
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch {
        // Keep the HTTP status when a response has no JSON body.
      }
      throw new Error(detail);
    }
    return response.json();
  },
  uploadIdentityLora: async (projectId, file, options = {}) => {
    const form = new FormData();
    form.append("file", file);
    if (options.trigger_token) form.append("trigger_token", options.trigger_token);
    if (options.strength !== undefined && options.strength !== null) {
      form.append("strength", String(options.strength));
    }
    const response = await fetch(
      `${API_ROOT}/projects/${encodeURIComponent(projectId)}/identity-lora`,
      { method: "POST", body: form }
    );
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch {
        // Keep the HTTP status when a response has no JSON body.
      }
      throw new Error(detail);
    }
    return response.json();
  },
  removeIdentityLora: (projectId) =>
    request(`/projects/${encodeURIComponent(projectId)}/identity-lora`, { method: "DELETE" }),
  removeReference: (projectId, referenceId) =>
    request(
      `/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}`,
      { method: "DELETE" }
    ),
  listJobs: () => request("/jobs"),
  evaluateGeometry: (project) =>
    request("/geometry/evaluate", { method: "POST", body: JSON.stringify(project) }),
  createJob: (project, provider = "colab") =>
    request("/jobs", {
      method: "POST",
      body: JSON.stringify({ project, provider })
    }),
  cancelJob: (jobId) => request(`/jobs/${jobId}/cancel`, { method: "POST" })
};

export function connectEvents(onEvent) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${window.location.host}/v1/events`);
  socket.onmessage = (event) => onEvent(JSON.parse(event.data));
  const keepalive = window.setInterval(() => {
    if (socket.readyState === WebSocket.OPEN) socket.send("ping");
  }, 20000);
  socket.addEventListener("close", () => window.clearInterval(keepalive));
  return socket;
}
