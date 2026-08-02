// App shell orchestration: stack actions, status polling, service
// panel, scenario cards/drawer, doc viewer, console, progress
// section, sidebar nav. Training lifecycle/scoring logic lives in
// training.js; network map rendering lives in networkmap.js — this
// file wires them together and owns everything that talks to the
// Flask backend directly.

import {
  TrainingStore,
  buildCompletionReport,
  buildCourseReport,
  confirmResetAttempt,
  confirmRevealSolution,
  ensureAttemptStarted,
  exportJSON,
  initInstructorPanel,
  isSolutionDoc,
  lifecycleLabel,
  markDocOpened,
  openPrintableReport,
  recomputeStatus,
  renderFlagsSection,
  renderLifecycleBadges,
  renderModeSelector,
  renderProgressSection,
  setSolutionDocs,
  showConfirmDialog,
  trapFocus,
  wireNotesField,
} from "./training.js";
import { initNetworkMap, resetMapView, setMapOverlay, toggleMapPorts, updateMapHealth } from "./networkmap.js";

const SCENARIOS = JSON.parse(document.getElementById("scenarios-data").textContent);
const SCENARIOS_BY_ID = Object.fromEntries(SCENARIOS.map((s) => [s.id, s]));
setSolutionDocs(JSON.parse(document.getElementById("solution-docs-data").textContent));

const DOC_LABELS = {
  briefing: "Briefing",
  detection: "Detection",
  "expected-impact": "Expected impact",
  "answer-key": "Answer key",
};

const CONTAINER_NAME_BY_NODE = {
  "process-sim": "ot-range-process-sim-1",
  openplc: "ot-range-openplc-1",
  hmi: "ot-range-hmi-1",
  historian: "ot-range-historian-1",
  postgres: "ot-range-postgres-1",
  grafana: "ot-range-grafana-1",
  router: "ot-range-router-1",
};

const SERVICE_GROUPS_DEF = [
  {
    title: "Control & Process",
    items: [
      { node: "process-sim", label: "Process Simulator" },
      { node: "openplc", label: "OpenPLC Controller", portKey: "openplc_web", url: "http://localhost:8080" },
      { node: "hmi", label: "HMI", portKey: "hmi", url: "http://localhost:8090" },
    ],
  },
  {
    title: "Data & Monitoring",
    items: [
      { node: "historian", label: "Historian" },
      { node: "postgres", label: "PostgreSQL" },
      { node: "grafana", label: "Grafana", portKey: "grafana", url: "http://localhost:3000" },
    ],
  },
  {
    title: "Network Security",
    items: [{ node: "router", label: "Router / Sensor (Zeek + Suricata)" }],
  },
];

let FLAGS = {};
let TOPOLOGY = null;
let _lastStatus = null;
let currentDrawerScenario = null;
let consoleStartTime = null;
let consoleTimerHandle = null;
let _prereqUpdate = null;

// ==================== status / readiness / services ====================

async function refreshStatus() {
  try {
    const resp = await fetch("/api/status");
    const data = await resp.json();
    _lastStatus = data;
    renderReadiness(data);
    renderServiceGroups(data);
    updateMapHealth(data);
    updateButtonsBusyState(data.busy);
    if (currentDrawerScenario) {
      renderDrawerWorkspaceLinks(SCENARIOS_BY_ID[currentDrawerScenario]);
      _prereqUpdate?.();
    }
  } catch (err) {
    console.error("status refresh failed", err);
  }
}

function renderReadiness(data) {
  let word;
  let cls;
  if (data.busy) {
    word = "Busy";
    cls = "warn";
  } else if (!data.docker.any_present) {
    word = "Offline";
    cls = "bad";
  } else if (data.docker.all_healthy) {
    word = "Ready";
    cls = "ok";
  } else {
    word = "Degraded";
    cls = "warn";
  }

  document.getElementById("readiness-word").textContent = word;
  document.getElementById("readiness-word-2").textContent = word;
  document.getElementById("readiness-dot").className = `readiness-dot ${cls}`;
  document.getElementById("readiness-dot-2").className = `readiness-dot large ${cls}`;
  document.getElementById("header-busy").hidden = !data.busy;
  document.getElementById("busy-note").hidden = !data.busy;

  const healthy = data.docker.containers.filter((c) => c.ok).length;
  document.getElementById("metric-containers").textContent = `${healthy}/${data.docker.containers.length}`;
  const interfaceKeys = Object.keys(data.docker.ports);
  const reachable = interfaceKeys.filter((k) => data.docker.ports[k]).length;
  document.getElementById("metric-interfaces").textContent = `${reachable}/${interfaceKeys.length}`;
  document.getElementById("metric-scenarios").textContent = `${SCENARIOS.length}`;
}

function updateButtonsBusyState(busy) {
  for (const btn of document.querySelectorAll("button.run-btn, #btn-up, #btn-down, #btn-reset")) {
    btn.disabled = busy;
  }
}

function renderServiceGroups(data) {
  const container = document.getElementById("service-groups");
  if (!container) return;
  if (!data.docker.any_present) {
    container.innerHTML = '<p class="status-empty">Range is offline — click "Start Range" above.</p>';
    return;
  }
  container.innerHTML = "";
  for (const group of SERVICE_GROUPS_DEF) {
    const groupEl = document.createElement("div");
    groupEl.className = "service-group";
    groupEl.innerHTML = `<h3>${group.title}</h3>`;
    for (const item of group.items) {
      const containerName = CONTAINER_NAME_BY_NODE[item.node];
      const container_ = data.docker.containers.find((c) => c.name === containerName);
      const ok = container_ ? container_.ok : false;
      const stateText = container_
        ? container_.health
          ? `${container_.state}, ${container_.health}`
          : container_.state
        : "not found";
      const row = document.createElement("div");
      row.className = "service-item";
      row.innerHTML = `
        <span class="readiness-dot ${ok ? "ok" : "bad"}"></span>
        <span class="service-name">${item.label}</span>
        <span class="service-detail">${stateText}</span>
        <span class="spacer"></span>
      `;
      if (item.portKey && data.docker.ports[item.portKey]) {
        const link = document.createElement("a");
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener";
        link.className = "workspace-link";
        link.textContent = "Open ↗";
        row.appendChild(link);
      }
      groupEl.appendChild(row);
    }
    container.appendChild(groupEl);
  }
}

// ==================== stack actions ====================

async function showAlert(title, message) {
  const cancelBtn = document.getElementById("dialog-cancel");
  cancelBtn.style.display = "none";
  await showConfirmDialog({ title, message, confirmLabel: "OK" });
  cancelBtn.style.display = "";
}

async function postJSON(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    if (resp.status === 409) await showAlert("Busy", "Something is already running — wait for it to finish.");
    else await showAlert("Error", data.error || resp.statusText);
    return null;
  }
  return data;
}

function wireStackButtons() {
  document.getElementById("btn-up").addEventListener("click", async () => {
    const data = await postJSON("/api/stack/up");
    if (data) streamJob(data.job_id, "Start Range (make up)");
  });
  document.getElementById("btn-down").addEventListener("click", async () => {
    const ok = await showConfirmDialog({
      title: "Stop the range?",
      message:
        "This tears down every container. Any scenario currently running is interrupted. Local training progress and scores are not affected.",
      confirmLabel: "Stop range",
      cancelLabel: "Cancel",
      danger: true,
    });
    if (!ok) return;
    const data = await postJSON("/api/stack/down");
    if (data) streamJob(data.job_id, "Stop Range (make down)");
  });
  document.getElementById("btn-reset").addEventListener("click", async () => {
    const ok = await showConfirmDialog({
      title: "Reset the range environment?",
      message:
        "This wipes the historian database and Zeek/Suricata log volume, then restarts against the already-built images. It does not touch your local training progress or scores — use “Reset this attempt” inside a scenario for that instead.",
      confirmLabel: "Reset range",
      cancelLabel: "Cancel",
      danger: true,
    });
    if (!ok) return;
    const data = await postJSON("/api/stack/reset");
    if (data) streamJob(data.job_id, "Reset Range (make reset)");
  });
  document.getElementById("btn-refresh").addEventListener("click", refreshStatus);
}

// ==================== console drawer ====================

function openConsole(title) {
  document.getElementById("console-title").textContent = title;
  const badge = document.getElementById("console-badge");
  badge.textContent = "running";
  badge.className = "status-badge running";
  document.getElementById("console-body").textContent = "";
  const wrap = document.getElementById("console-wrap");
  wrap.hidden = false;
  wrap.classList.remove("collapsed");
  consoleStartTime = Date.now();
  clearInterval(consoleTimerHandle);
  consoleTimerHandle = setInterval(updateConsoleElapsed, 1000);
  updateConsoleElapsed();
}

function updateConsoleElapsed() {
  if (!consoleStartTime) return;
  document.getElementById("console-elapsed").textContent = `${Math.floor((Date.now() - consoleStartTime) / 1000)}s`;
}

function appendConsoleLine(text) {
  const body = document.getElementById("console-body");
  body.textContent += `${text}\n`;
  if (document.getElementById("console-autoscroll").checked) body.scrollTop = body.scrollHeight;
}

function finishConsole(returncode) {
  clearInterval(consoleTimerHandle);
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

function wireConsole() {
  document.getElementById("console-close").addEventListener("click", () => {
    document.getElementById("console-wrap").hidden = true;
    clearInterval(consoleTimerHandle);
  });
  document.getElementById("console-collapse").addEventListener("click", (ev) => {
    const wrap = document.getElementById("console-wrap");
    wrap.classList.toggle("collapsed");
    ev.target.textContent = wrap.classList.contains("collapsed") ? "▸" : "▾";
  });
  document.getElementById("console-clear").addEventListener("click", () => {
    document.getElementById("console-body").textContent = "";
  });
}

// ==================== document viewer modal ====================

function openDocModal(scenarioId, docKey) {
  document.getElementById("modal-title").textContent = DOC_LABELS[docKey];
  document.getElementById("modal-subtitle").textContent = `${scenarioId} — ${SCENARIOS_BY_ID[scenarioId].title}`;
  const body = document.getElementById("modal-body");
  body.textContent = "Loading…";
  const overlay = document.getElementById("modal-overlay");
  overlay.hidden = false;
  overlay._releaseFocus = trapFocus(document.getElementById("modal-box"));

  fetch(`/api/docs/${scenarioId}/${docKey}`)
    .then((r) => (r.ok ? r.text() : Promise.resolve("Not found.")))
    .then((text) => {
      body.textContent = text;
    });
}

async function tryOpenDoc(scenarioId, docKey) {
  ensureAttemptStarted(scenarioId);
  if (isSolutionDoc(docKey)) {
    const state = TrainingStore.getScenario(scenarioId);
    if (!state.solutionLocked && state.mode !== "guided") {
      const ok = await confirmRevealSolution();
      if (!ok) return;
    }
  }
  markDocOpened(scenarioId, docKey);
  recomputeStatus(scenarioId, FLAGS[scenarioId] || []);
  renderLifecycleBadges(FLAGS);
  renderProgressSection(SCENARIOS, FLAGS, exportOneScenarioReport);
  updateDrawerLifecycleBadge(scenarioId);
  if (currentDrawerScenario === scenarioId) refreshDrawerFlags();
  openDocModal(scenarioId, docKey);
}

function wireModalClose() {
  const overlay = document.getElementById("modal-overlay");
  const close = () => {
    overlay.hidden = true;
    overlay._releaseFocus?.();
  };
  document.getElementById("modal-close").addEventListener("click", close);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) close();
  });
  document.getElementById("modal-copy").addEventListener("click", () => {
    navigator.clipboard?.writeText(document.getElementById("modal-body").textContent);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !overlay.hidden) close();
  });
}

// ==================== scenario detail drawer ====================

function updateDrawerLifecycleBadge(scenarioId) {
  const badge = document.getElementById("drawer-lifecycle-badge");
  if (!badge || currentDrawerScenario !== scenarioId) return;
  const state = TrainingStore.getScenario(scenarioId);
  badge.textContent = lifecycleLabel(state);
  badge.className = `lifecycle-badge status-${state.status}`;
}

function openDrawer(scenarioId) {
  currentDrawerScenario = scenarioId;
  const meta = SCENARIOS_BY_ID[scenarioId];

  const content = document.getElementById("drawer-content");
  content.innerHTML = `
    <div class="drawer-subtitle">${scenarioId}</div>
    <h2 class="drawer-title">${meta.title}</h2>
    <span class="lifecycle-badge" id="drawer-lifecycle-badge"></span>
    <p class="meta-line" style="margin-top:8px">${meta.hook}</p>

    <div class="drawer-section">
      <h4>Threat summary</h4>
      <p class="meta-line"><b>Impact:</b> ${meta.impact}</p>
      <p class="meta-line"><b>Caught by:</b> ${meta.caught_by}</p>
      <span class="severity severity-${meta.severity}">${meta.severity}</span>
      ${meta.severity === "critical" ? '<div class="warning-banner">This scenario causes real simulated process damage (tank overflow, pump damage) when run. Simulated environment only — see SECURITY.md.</div>' : ""}
    </div>

    <div class="drawer-section">
      <h4>Learning objectives</h4>
      <ul class="objective-list">${meta.objectives.map((o) => `<li>${o}</li>`).join("")}</ul>
    </div>

    <div class="drawer-section" id="drawer-mode-section"></div>

    <div class="drawer-section">
      <h4>Run scenario</h4>
      <div class="row">
        <select id="drawer-mode-select">
          ${meta.modes.map((m, i) => `<option value="${i}">${m.label}</option>`).join("")}
        </select>
        <button class="btn-primary run-btn" id="drawer-run-btn">Run</button>
      </div>
      <div id="drawer-prereq"></div>
    </div>

    <div class="drawer-section">
      <h4>Documentation</h4>
      <div class="doc-tabs">
        <button class="doc-tab" data-doc="briefing">Briefing</button>
        <button class="doc-tab" data-doc="detection">Detection</button>
        <button class="doc-tab" data-doc="expected-impact">Expected impact</button>
        <button class="doc-tab solution-doc" data-doc="answer-key">Answer key 🔒</button>
      </div>
    </div>

    <div class="drawer-section">
      <h4>Investigation flags</h4>
      <div id="drawer-flags"></div>
    </div>

    <div class="drawer-section">
      <h4>Investigation workspace</h4>
      <div class="workspace-links" id="drawer-workspace-links"></div>
    </div>

    <div class="drawer-section">
      <h4>Notes (local only, never treated as a flag answer)</h4>
      <textarea class="notes-field" id="drawer-notes"></textarea>
    </div>

    <div class="drawer-section">
      <button class="btn-secondary-danger" id="drawer-reset-btn">Reset this attempt</button>
    </div>
  `;

  renderModeSelector(document.getElementById("drawer-mode-section"), scenarioId, refreshDrawerFlags);
  wireNotesField(document.getElementById("drawer-notes"), scenarioId);
  refreshDrawerFlags();
  renderDrawerWorkspaceLinks(meta);
  renderDrawerPrereq(meta);
  updateDrawerLifecycleBadge(scenarioId);

  content.querySelectorAll(".doc-tab").forEach((btn) => {
    btn.addEventListener("click", () => tryOpenDoc(scenarioId, btn.dataset.doc));
  });

  document.getElementById("drawer-run-btn").addEventListener("click", async () => {
    const modeIndex = Number.parseInt(document.getElementById("drawer-mode-select").value, 10);
    ensureAttemptStarted(scenarioId);
    renderLifecycleBadges(FLAGS);
    updateDrawerLifecycleBadge(scenarioId);
    const data = await postJSON("/api/run", { scenario: scenarioId, mode_index: modeIndex });
    if (data) streamJob(data.job_id, `${scenarioId}: ${meta.modes[modeIndex].label}`);
  });

  document.getElementById("drawer-reset-btn").addEventListener("click", async () => {
    const ok = await confirmResetAttempt();
    if (!ok) return;
    TrainingStore.resetScenario(scenarioId);
    renderLifecycleBadges(FLAGS);
    renderProgressSection(SCENARIOS, FLAGS, exportOneScenarioReport);
    renderModeSelector(document.getElementById("drawer-mode-section"), scenarioId, refreshDrawerFlags);
    refreshDrawerFlags();
    updateDrawerLifecycleBadge(scenarioId);
    document.getElementById("drawer-notes").value = "";
  });

  const overlay = document.getElementById("drawer-overlay");
  overlay.hidden = false;
  overlay._releaseFocus = trapFocus(document.getElementById("scenario-drawer"));

  tryShowMapOverlay(scenarioId);
}

function refreshDrawerFlags() {
  const container = document.getElementById("drawer-flags");
  if (!container || !currentDrawerScenario) return;
  renderFlagsSection(container, currentDrawerScenario, FLAGS[currentDrawerScenario] || [], () => {
    renderLifecycleBadges(FLAGS);
    renderProgressSection(SCENARIOS, FLAGS, exportOneScenarioReport);
    updateDrawerLifecycleBadge(currentDrawerScenario);
    // Re-check the map overlay gate — it may have just unlocked.
    if (document.getElementById("map-scenario-select").value === currentDrawerScenario) {
      tryShowMapOverlay(currentDrawerScenario);
    }
  });
}

function renderDrawerWorkspaceLinks(meta) {
  const container = document.getElementById("drawer-workspace-links");
  if (!container) return;
  const links = [];
  const ports = _lastStatus?.docker?.ports;
  if (ports?.hmi) links.push({ label: "HMI ↗", url: "http://localhost:8090" });
  if (ports?.openplc_web) links.push({ label: "OpenPLC web UI ↗", url: "http://localhost:8080" });
  if (ports?.grafana) links.push({ label: "Grafana ↗", url: "http://localhost:3000" });
  container.innerHTML =
    links.map((l) => `<a class="workspace-link" href="${l.url}" target="_blank" rel="noopener">${l.label}</a>`).join("") +
    '<a class="workspace-link" href="#network-map">Network map</a>' +
    '<button class="workspace-link" id="drawer-console-link" type="button">Console</button>';
  document.getElementById("drawer-console-link")?.addEventListener("click", () => {
    document.getElementById("nav-console-link").click();
  });
}

function renderDrawerPrereq(meta) {
  const banner = document.getElementById("drawer-prereq");
  const select = document.getElementById("drawer-mode-select");
  const runBtn = document.getElementById("drawer-run-btn");
  _prereqUpdate = () => {
    const mode = meta.modes[Number.parseInt(select.value, 10)];
    const ready = _lastStatus?.docker?.all_healthy;
    if (mode.requires_docker && !ready) {
      banner.innerHTML =
        '<div class="prereq-banner">This mode needs the range running (Docker stack up and healthy). <button class="btn-primary" id="prereq-start-btn">Start Range</button></div>';
      banner.querySelector("#prereq-start-btn").addEventListener("click", () => document.getElementById("btn-up").click());
      runBtn.disabled = true;
    } else {
      banner.innerHTML = "";
      runBtn.disabled = _lastStatus?.busy || false;
    }
  };
  select.addEventListener("change", _prereqUpdate);
  _prereqUpdate();
}

function wireDrawerClose() {
  const overlay = document.getElementById("drawer-overlay");
  const close = () => {
    overlay.hidden = true;
    overlay._releaseFocus?.();
    currentDrawerScenario = null;
    _prereqUpdate = null;
  };
  document.getElementById("drawer-close").addEventListener("click", close);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) close();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !overlay.hidden) close();
  });
}

function wireScenarioCards() {
  document.querySelectorAll(".scenario-card").forEach((card) => {
    const open = () => openDrawer(card.dataset.scenario);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        open();
      }
    });
  });
}

// ==================== flags data + progress/reports ====================

async function loadFlags() {
  const resp = await fetch("/api/flags");
  FLAGS = await resp.json();
  renderLifecycleBadges(FLAGS);
  renderProgressSection(SCENARIOS, FLAGS, exportOneScenarioReport);
}

function exportOneScenarioReport(scenarioId) {
  const report = buildCompletionReport(scenarioId, SCENARIOS_BY_ID[scenarioId], FLAGS[scenarioId] || []);
  exportJSON(report, `ot-range-${scenarioId}-report.json`);
}

function wireProgressExports() {
  document.getElementById("export-course-json").addEventListener("click", () => {
    exportJSON(buildCourseReport(SCENARIOS, FLAGS), "ot-range-course-report.json");
  });
  document.getElementById("export-course-print").addEventListener("click", () => {
    const report = buildCourseReport(SCENARIOS, FLAGS);
    openPrintableReport(report.scenarios, "Cedar Hollow OT Range — Course Progress Report");
  });
}

// ==================== network map ====================

async function loadTopology() {
  const resp = await fetch("/api/topology");
  TOPOLOGY = await resp.json();
  initNetworkMap(TOPOLOGY);
}

// The attack-path overlay (which edges are the attack path, which
// nodes are "affected"/"detection points", S05's ground-truth-vs-
// spoofed labels, S06's monitoring-gap annotation) visually answers
// several flags outright — it's spoiler content, not neutral
// infrastructure diagramming. Gated the same way the answer key is:
// available once the attempt is genuinely done (completed, completed
// with assistance, or the solution was explicitly revealed) or in
// guided mode, where more scaffolding up front is the point. The base
// topology (zones, services, ports, health) stays visible always —
// that's real environment awareness, not a spoiler.
function isOverlayUnlocked(scenarioId) {
  const state = TrainingStore.getScenario(scenarioId);
  if (state.mode === "guided") return true;
  return ["completed", "completed_with_assistance", "solution_revealed"].includes(state.status);
}

function tryShowMapOverlay(scenarioId) {
  const select = document.getElementById("map-scenario-select");
  const note = document.getElementById("map-overlay-locked-note");
  const message = document.getElementById("map-overlay-locked-message");
  if (!scenarioId) {
    select.value = "";
    note.hidden = true;
    message.textContent = "";
    setMapOverlay(null);
    return;
  }
  if (isOverlayUnlocked(scenarioId)) {
    select.value = scenarioId;
    note.hidden = true;
    message.textContent = "";
    setMapOverlay(scenarioId);
  } else {
    // Keep the locked scenario selected. Resetting the select to None
    // here left the warning visible while None was already selected,
    // so choosing None could not fire another change event to clear it.
    select.value = scenarioId;
    note.hidden = false;
    message.textContent =
      `${scenarioId}'s attack-path overlay is hidden until that attempt is complete or its solution is revealed — showing it now would hand you the investigation. Open the scenario and investigate normally; the overlay unlocks automatically once you're done, or switch to guided mode for more scaffolding up front.`;
    setMapOverlay(null);
  }
}

function wireMapControls() {
  document.getElementById("map-scenario-select").addEventListener("change", (ev) => tryShowMapOverlay(ev.target.value));
  document.getElementById("map-overlay-locked-close").addEventListener("click", () => tryShowMapOverlay(""));
  document.getElementById("map-toggle-ports").addEventListener("click", (ev) => {
    const showing = toggleMapPorts();
    ev.target.textContent = `Ports/protocols: ${showing ? "on" : "off"}`;
  });
  document.getElementById("map-reset-view").addEventListener("click", resetMapView);
  document.getElementById("map-refresh-health").addEventListener("click", refreshStatus);
}

// ==================== sidebar nav ====================

function wireSidebarNav() {
  // "Console" isn't a scrollable page section — it's a fixed bottom
  // drawer — so it can't be tracked the same way as the other five.
  // It used to point at a nonexistent #console-section anchor, which
  // meant it could never highlight or navigate anywhere; it now opens
  // the drawer directly instead.
  const consoleLink = document.getElementById("nav-console-link");
  const allLinks = [...document.querySelectorAll(".nav-link")];
  const scrollLinks = allLinks.filter((l) => l !== consoleLink);
  const linksByTarget = new Map(scrollLinks.map((link) => [link.dataset.nav, link]));
  // Scroll-position comparisons must follow page order, not sidebar
  // order. Those currently differ for Plant Services and Scenarios;
  // walking the links made Plant Services overwrite Scenarios after
  // both section tops had crossed the reference line.
  const sections = [...document.querySelectorAll(".workspace-section")]
    .map((el) => ({ link: linksByTarget.get(el.id), el }))
    .filter((s) => s.link);

  function setActive(link) {
    allLinks.forEach((l) => l.classList.toggle("active", l === link));
  }

  // Deliberately not IntersectionObserver with a thin rootMargin band:
  // that only reliably fires for sections taller than the band, which
  // varies by viewport size — Plant Services (a handful of short
  // service rows) could fall entirely outside the band on some
  // screens. Find the last section whose top has crossed a fixed
  // reference line below the sticky header instead — works regardless
  // of a section's height.
  const REFERENCE_OFFSET = 84;

  function sectionForScrollPosition() {
    // Bottom-of-page special case: the last section's top can only
    // reach the reference line if there's enough scrollable page left
    // below it. If the page is short, max scroll stops short and its
    // top never crosses the line, so it could never highlight at all.
    const atBottom = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 4;
    if (atBottom) return sections[sections.length - 1];
    let current = sections[0];
    for (const s of sections) {
      if (s.el.getBoundingClientRect().top - REFERENCE_OFFSET <= 0) current = s;
    }
    return current;
  }

  // Clicking a link sets the active state directly and immediately —
  // no ambiguity, no race with the scroll-driven fallback below (which
  // is for organic mouse-wheel scrolling only, and is suppressed for
  // a moment after a click so it can't stomp on a click that hasn't
  // finished scrolling yet).
  let suppressScrollUpdates = false;
  scrollLinks.forEach((link) => {
    link.addEventListener("click", (ev) => {
      ev.preventDefault();
      const target = document.getElementById(link.dataset.nav);
      if (!target) return;
      setActive(link);
      suppressScrollUpdates = true;
      target.scrollIntoView({ behavior: "auto", block: "start" });
      document.querySelector(".sidebar").classList.remove("open");
      window.setTimeout(() => {
        suppressScrollUpdates = false;
      }, 350);
    });
  });

  let ticking = false;
  window.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      if (!suppressScrollUpdates) {
        const current = sectionForScrollPosition();
        if (current) setActive(current.link);
      }
      ticking = false;
    });
  });
  setActive(sectionForScrollPosition()?.link);

  consoleLink.addEventListener("click", (ev) => {
    ev.preventDefault();
    const wrap = document.getElementById("console-wrap");
    wrap.hidden = false;
    wrap.classList.remove("collapsed");
    setActive(consoleLink);
    document.querySelector(".sidebar").classList.remove("open");
  });

  // Console is a drawer rather than a page section. Once it closes,
  // restore the highlight for the section still visible underneath.
  document.getElementById("console-close").addEventListener("click", () => {
    const current = sectionForScrollPosition();
    if (current) setActive(current.link);
  });

  document.getElementById("mobile-menu-btn")?.addEventListener("click", () => {
    document.querySelector(".sidebar").classList.toggle("open");
  });
}

// ==================== init ====================

async function init() {
  wireStackButtons();
  wireConsole();
  wireModalClose();
  wireDrawerClose();
  wireScenarioCards();
  wireProgressExports();
  wireMapControls();
  wireSidebarNav();
  initInstructorPanel(SCENARIOS, () => window.location.reload());

  await Promise.all([loadFlags(), loadTopology()]);
  await refreshStatus();
  setInterval(refreshStatus, 4000);
}

init();
