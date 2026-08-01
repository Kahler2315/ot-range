const STATUS_LABELS = {
  "ot-range-process-sim-1": "process-sim",
  "ot-range-openplc-1": "OpenPLC",
  "ot-range-hmi-1": "HMI",
  "ot-range-historian-1": "historian",
  "ot-range-postgres-1": "postgres",
  "ot-range-grafana-1": "Grafana",
  "ot-range-router-1": "router",
};

let refreshTimer = null;

function renderStatus(data) {
  const grid = document.getElementById("status-grid");
  grid.innerHTML = "";

  const addItem = (ok, label, linkUrl) => {
    const div = document.createElement("div");
    div.className = "status-item";
    const dot = `<span class="dot ${ok ? "ok" : "bad"}"></span>`;
    const link = linkUrl ? `<a href="${linkUrl}" target="_blank">open →</a>` : "";
    div.innerHTML = `${dot}<span class="label">${label}</span>${link}`;
    grid.appendChild(div);
  };

  if (!data.docker.any_present) {
    addItem(false, "Docker stack not running — click “Bring stack up”");
  } else {
    for (const c of data.docker.containers) {
      addItem(c.ok, STATUS_LABELS[c.name] || c.name);
    }
    addItem(data.docker.ports.modbus_openplc, "Modbus (OpenPLC) :502");
    addItem(data.docker.ports.modbus_sim, "Modbus (process-sim) :5502");
    addItem(data.docker.ports.openplc_web, "OpenPLC web UI", "http://localhost:8080");
    addItem(data.docker.ports.hmi, "HMI", "http://localhost:8090");
    addItem(data.docker.ports.grafana, "Grafana", "http://localhost:3000");
  }

  const busy = data.busy;
  document.getElementById("busy-note").style.display = busy ? "inline" : "none";
  for (const btn of document.querySelectorAll("button.run-btn, #btn-up, #btn-down, #btn-reset")) {
    btn.disabled = busy;
  }
}

async function refreshStatus() {
  try {
    const resp = await fetch("/api/status");
    const data = await resp.json();
    renderStatus(data);
  } catch (err) {
    console.error("status refresh failed", err);
  }
}

function openConsole(title) {
  document.getElementById("console-title").textContent = title;
  const badge = document.getElementById("console-badge");
  badge.textContent = "running";
  badge.className = "status-badge running";
  document.getElementById("console-body").textContent = "";
  document.getElementById("console-wrap").style.display = "flex";
}

function appendConsoleLine(text) {
  const body = document.getElementById("console-body");
  body.textContent += text + "\n";
  body.scrollTop = body.scrollHeight;
}

function finishConsole(returncode) {
  const badge = document.getElementById("console-badge");
  const ok = returncode === 0 || returncode === "0";
  badge.textContent = ok ? "done" : `failed (${returncode})`;
  badge.className = `status-badge ${ok ? "ok" : "fail"}`;
  refreshStatus();
}

function streamJob(jobId, title) {
  openConsole(title);
  const source = new EventSource(`/api/stream/${jobId}`);
  source.onmessage = (ev) => appendConsoleLine(JSON.parse(ev.data));
  source.addEventListener("done", (ev) => {
    finishConsole(ev.data);
    source.close();
  });
  source.onerror = () => {
    appendConsoleLine("[console disconnected]");
    source.close();
  };
}

async function postJSON(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    if (resp.status === 409) {
      alert("Something is already running — wait for it to finish.");
    } else {
      alert(`Error: ${data.error || resp.statusText}`);
    }
    return null;
  }
  return data;
}

function wireStackButtons() {
  document.getElementById("btn-up").addEventListener("click", async () => {
    const data = await postJSON("/api/stack/up");
    if (data) streamJob(data.job_id, "make up");
  });
  document.getElementById("btn-down").addEventListener("click", async () => {
    if (!confirm("Tear down the stack? All containers will stop.")) return;
    const data = await postJSON("/api/stack/down");
    if (data) streamJob(data.job_id, "make down");
  });
  document.getElementById("btn-reset").addEventListener("click", async () => {
    if (!confirm("Reset wipes postgres and zeek-log state. Continue?")) return;
    const data = await postJSON("/api/stack/reset");
    if (data) streamJob(data.job_id, "make reset");
  });
  document.getElementById("btn-refresh").addEventListener("click", refreshStatus);
  document.getElementById("console-close").addEventListener("click", () => {
    document.getElementById("console-wrap").style.display = "none";
  });
}

function wireScenarioButtons() {
  for (const btn of document.querySelectorAll(".run-btn")) {
    btn.addEventListener("click", async () => {
      const scenario = btn.dataset.scenario;
      const select = document.querySelector(`select[data-scenario="${scenario}"]`);
      const modeIndex = parseInt(select.value, 10);
      const data = await postJSON("/api/run", { scenario, mode_index: modeIndex });
      if (data) streamJob(data.job_id, `${scenario}: ${select.options[select.selectedIndex].text}`);
    });
  }
}

function wireDocLinks() {
  const overlay = document.getElementById("modal-overlay");
  const title = document.getElementById("modal-title");
  const body = document.getElementById("modal-body");
  for (const link of document.querySelectorAll(".doc-links a")) {
    link.addEventListener("click", async () => {
      const scenario = link.dataset.scenario;
      const doc = link.dataset.doc;
      title.textContent = `${scenario} — ${link.textContent}`;
      body.textContent = "loading…";
      overlay.style.display = "flex";
      const resp = await fetch(`/api/docs/${scenario}/${doc}`);
      body.textContent = resp.ok ? await resp.text() : "not found";
    });
  }
  document.getElementById("modal-close").addEventListener("click", () => {
    overlay.style.display = "none";
  });
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) overlay.style.display = "none";
  });
}

wireStackButtons();
wireScenarioButtons();
wireDocLinks();
refreshStatus();
refreshTimer = setInterval(refreshStatus, 4000);
