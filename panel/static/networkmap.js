// Deterministic inline-SVG topology map. The data remains grounded in
// panel/topology.py; this module owns only presentation and interaction.

const VIEWBOX_INITIAL = { x: 0, y: 0, w: 1100, h: 560 };
const NODE_W = 156;
const NODE_H = 76;

const ZONE_LAYOUT = {
  "zone-enterprise": { x: 24, y: 52, w: 236, h: 456 },
  boundary: { x: 282, y: 52, w: 164, h: 456 },
  "zone-ops": { x: 468, y: 52, w: 608, h: 456 },
};

const NODE_LAYOUT = {
  attacker: { x: 140, y: 280 },
  router: { x: 370, y: 280 },
  openplc: { x: 600, y: 280 },
  "process-sim": { x: 600, y: 410 },
  hmi: { x: 850, y: 136 },
  historian: { x: 850, y: 280 },
  postgres: { x: 1010, y: 410 },
  grafana: { x: 1010, y: 136 },
};

// Routes end at card boundaries and use intentional right-angle bends,
// avoiding the text and card collisions caused by center-to-center lines.
const EDGE_ROUTES = {
  "attacker-router": { d: "M 218 280 H 292", label: { x: 255, y: 280 } },
  "router-plc": { d: "M 448 280 H 522", label: { x: 485, y: 280 } },
  "sim-plc": { d: "M 600 372 V 318", label: { x: 645, y: 348 } },
  "plc-hmi": { d: "M 654 242 H 710 V 136 H 772", label: { x: 710, y: 136 } },
  "plc-historian": { d: "M 678 280 H 772", label: { x: 725, y: 280 } },
  "historian-pg": { d: "M 928 280 V 410 H 932", label: { x: 928, y: 346 } },
  "grafana-pg": { d: "M 1010 174 V 372", label: { x: 1010, y: 275 } },
};

const KIND_LABELS = {
  attacker: "EXT",
  sensor: "IDS",
  controller: "PLC",
  process: "SIM",
  hmi: "HMI",
  historian: "HIST",
  database: "DB",
  dashboard: "VIS",
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

const WEB_LINKS_BY_NODE = {
  openplc: { label: "Open OpenPLC Web UI", url: "http://localhost:8080" },
  hmi: { label: "Open HMI Dashboard", url: "http://localhost:8090" },
  grafana: { label: "Open Grafana Dashboard", url: "http://localhost:3000" },
};

let _topology = null;
let _statusData = null;
let _currentOverlayId = null;
let _showPorts = true;
let _viewBox = { ...VIEWBOX_INITIAL };
let _selectedNodeId = null;

function svgEl(tag, attrs = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
  return element;
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function applyViewBox() {
  const svg = document.getElementById("map-svg");
  if (svg) svg.setAttribute("viewBox", `${_viewBox.x} ${_viewBox.y} ${_viewBox.w} ${_viewBox.h}`);
}

export function initNetworkMap(topology) {
  _topology = topology;
  render();
  wireInteractions();
}

export function updateMapHealth(statusData) {
  _statusData = statusData;
  render();
}

export function setMapOverlay(scenarioId) {
  _currentOverlayId = scenarioId || null;
  _selectedNodeId = null;
  render();
}

export function toggleMapPorts() {
  _showPorts = !_showPorts;
  render();
  return _showPorts;
}

export function resetMapView() {
  _viewBox = { ...VIEWBOX_INITIAL };
  applyViewBox();
}

function nodeHealth(nodeId) {
  if (!_statusData || !_statusData.docker.any_present) return null;
  const containerName = CONTAINER_NAME_BY_NODE[nodeId];
  if (!containerName) return null;
  const container = _statusData.docker.containers.find((item) => item.name === containerName);
  return container ? container.ok : null;
}

function render() {
  const svg = document.getElementById("map-svg");
  if (!svg || !_topology) return;
  svg.replaceChildren();
  svg.classList.toggle("map-has-overlay", Boolean(_currentOverlayId));
  applyViewBox();

  const overlay = _currentOverlayId ? (_topology.overlays || {})[_currentOverlayId] : null;
  renderDefinitions(svg);
  renderZones(svg);
  renderEdges(svg, overlay);
  renderNodes(svg, overlay);
  renderOverlaySummary(overlay);
}

function renderDefinitions(svg) {
  const defs = svgEl("defs");
  for (const [id, color] of [
    ["arrow-normal", "#66717e"],
    ["arrow-monitored", "#4fb3d9"],
    ["arrow-attack", "#ff6b5f"],
  ]) {
    const marker = svgEl("marker", {
      id,
      viewBox: "0 0 10 10",
      refX: 9,
      refY: 5,
      markerWidth: 7,
      markerHeight: 7,
      orient: "auto-start-reverse",
    });
    marker.appendChild(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: color }));
    defs.appendChild(marker);
  }
  svg.appendChild(defs);
}

function renderZones(svg) {
  for (const [zoneId, layout] of Object.entries(ZONE_LAYOUT)) {
    const zone = _topology.zones[zoneId];
    const group = svgEl("g", { class: `map-zone map-zone-${zone.trust}` });
    group.appendChild(
      svgEl("rect", {
        x: layout.x,
        y: layout.y,
        width: layout.w,
        height: layout.h,
        rx: 18,
        class: "map-zone-rect",
      })
    );
    const label = svgEl("text", { x: layout.x + 18, y: layout.y + 28, class: "map-zone-title" });
    label.textContent = zone.label;
    group.appendChild(label);
    const subnet = svgEl("text", { x: layout.x + 18, y: layout.y + 46, class: "map-zone-subtitle" });
    subnet.textContent = zone.subnet || "dual-homed sensor";
    group.appendChild(subnet);
    svg.appendChild(group);
  }
}

function renderEdges(svg, overlay) {
  const attackEdgeIds = new Set(overlay?.path_edges || []);
  for (const edge of _topology.edges) {
    const route = EDGE_ROUTES[edge.id];
    if (!route) continue;
    const isAttack = attackEdgeIds.has(edge.id);
    const classes = ["map-edge"];
    if (isAttack) classes.push("attack-path");
    else if (edge.monitored) classes.push("monitored");
    else classes.push("unmonitored");
    if (overlay && !isAttack) classes.push("subdued");

    if (isAttack) {
      svg.appendChild(svgEl("path", { d: route.d, class: "map-edge-attack-halo" }));
    }
    const marker = isAttack ? "arrow-attack" : edge.monitored ? "arrow-monitored" : "arrow-normal";
    svg.appendChild(
      svgEl("path", {
        d: route.d,
        class: classes.join(" "),
        "marker-end": `url(#${marker})`,
      })
    );
    if (_showPorts) renderEdgeLabel(svg, edge.protocol, route.label, isAttack);
  }
}

function renderEdgeLabel(svg, protocol, position, attack) {
  const width = Math.max(48, protocol.length * 6.4 + 16);
  const group = svgEl("g", { class: `map-edge-label${attack ? " attack-label" : ""}` });
  group.appendChild(
    svgEl("rect", {
      x: position.x - width / 2,
      y: position.y - 12,
      width,
      height: 22,
      rx: 11,
    })
  );
  const text = svgEl("text", { x: position.x, y: position.y + 4, "text-anchor": "middle" });
  text.textContent = protocol;
  group.appendChild(text);
  svg.appendChild(group);
}

function renderNodes(svg, overlay) {
  const affected = new Set(overlay?.affected_nodes || []);
  const detection = new Set(overlay?.detection_nodes || []);
  for (const node of _topology.nodes) {
    const position = NODE_LAYOUT[node.id];
    if (!position) continue;
    const classes = ["map-node", `map-node-${node.kind}`];
    if (affected.has(node.id)) classes.push("affected");
    if (detection.has(node.id)) classes.push("detection");
    if (_selectedNodeId === node.id) classes.push("selected");

    const health = nodeHealth(node.id);
    const healthLabel = health === null ? "status unavailable" : health ? "service healthy" : "service offline";
    const group = svgEl("g", {
      class: classes.join(" "),
      "data-node": node.id,
      tabindex: 0,
      role: "button",
      "aria-pressed": String(_selectedNodeId === node.id),
      "aria-label": `${node.label}, ${_topology.zones[node.zone]?.label || node.zone}, ${healthLabel}`,
      transform: `translate(${position.x - NODE_W / 2}, ${position.y - NODE_H / 2})`,
    });
    group.appendChild(svgEl("rect", { width: NODE_W, height: NODE_H, rx: 12, class: "node-shape" }));
    group.appendChild(svgEl("rect", { x: 0, y: 0, width: 5, height: NODE_H, rx: 2.5, class: "node-accent" }));
    group.appendChild(svgEl("rect", { x: 13, y: 12, width: 38, height: 18, rx: 9, class: "node-kind-pill" }));

    const kind = svgEl("text", { x: 32, y: 25, "text-anchor": "middle", class: "node-kind-text" });
    kind.textContent = KIND_LABELS[node.kind] || node.kind.slice(0, 4).toUpperCase();
    group.appendChild(kind);
    const label = svgEl("text", { x: 13, y: 48, class: "node-label" });
    label.textContent = node.label;
    group.appendChild(label);

    const detail = svgEl("text", { x: 13, y: 65, class: "node-detail-text" });
    detail.textContent = nodeSubtitle(node);
    group.appendChild(detail);

    if (health !== null) {
      group.appendChild(svgEl("circle", { cx: NODE_W - 16, cy: 20, r: 6, class: `health-dot ${health ? "ok" : "bad"}` }));
      const healthSymbol = svgEl("text", {
        x: NODE_W - 16,
        y: 23,
        "text-anchor": "middle",
        class: `health-symbol ${health ? "ok" : "bad"}`,
        "aria-hidden": "true",
      });
      healthSymbol.textContent = health ? "✓" : "×";
      group.appendChild(healthSymbol);
    }

    const callout = nodeCallout(node.id, overlay);
    if (callout) appendNodeCallout(group, callout);

    group.addEventListener("click", () => showNodeDetail(node));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        showNodeDetail(node);
      }
    });
    svg.appendChild(group);
  }
}

function nodeSubtitle(node) {
  if (_showPorts && node.ports.length) return node.ports.map((port) => port.port).join(" · ");
  const labels = {
    attacker: "scenario source",
    router: "Zeek + Suricata",
    historian: "OpenPLC telemetry",
    postgres: "historian store",
  };
  return labels[node.id] || "select for details";
}

function nodeCallout(nodeId, overlay) {
  if (!overlay) return null;
  if (nodeId === overlay.truth_node) return { text: "GROUND TRUTH", type: "truth" };
  if (nodeId === overlay.spoofed_node) return { text: "SPOOFED VIEW", type: "affected" };
  if (overlay.compromise_port?.node === nodeId) {
    return { text: `PORT ${overlay.compromise_port.port} · UNMONITORED`, type: "affected" };
  }
  return null;
}

function appendNodeCallout(group, callout) {
  const width = callout.text.length > 18 ? 152 : 126;
  const x = (NODE_W - width) / 2;
  group.appendChild(svgEl("rect", { x, y: NODE_H + 8, width, height: 22, rx: 11, class: `map-callout ${callout.type}` }));
  const text = svgEl("text", { x: NODE_W / 2, y: NODE_H + 23, "text-anchor": "middle", class: `map-callout-text ${callout.type}` });
  text.textContent = callout.text;
  group.appendChild(text);
}

function renderOverlaySummary(overlay) {
  const summary = document.getElementById("map-overlay-summary");
  if (!summary) return;
  summary.hidden = !overlay;
  summary.textContent = overlay?.note || "";
}

function showNodeDetail(node) {
  _selectedNodeId = node.id;
  document.querySelectorAll(".map-node").forEach((element) => {
    const selected = element.dataset.node === node.id;
    element.classList.toggle("selected", selected);
    element.setAttribute("aria-pressed", String(selected));
  });
  const panel = document.getElementById("map-node-detail");
  if (!panel) return;
  panel.hidden = false;
  const portsHtml = node.ports.length
    ? node.ports
        .map((port) => `${port.port}/${port.protocol}${port.monitored ? " (monitored)" : " (not monitored)"}`)
        .join(", ")
    : "none published to host";
  const webLink = WEB_LINKS_BY_NODE[node.id];
  const linkHtml = webLink
    ? `<a class="map-detail-link" href="${webLink.url}" target="_blank" rel="noopener">${webLink.label} <span aria-hidden="true">↗</span></a>`
    : "";
  panel.innerHTML = `<div class="map-detail-heading"><span class="node-kind-badge">${KIND_LABELS[node.kind] || node.kind}</span><h4>${node.label}</h4>${linkHtml}</div><p>${node.detail}</p><dl><div><dt>Zone</dt><dd>${_topology.zones[node.zone]?.label || node.zone}</dd></div><div><dt>Ports</dt><dd>${portsHtml}</dd></div></dl>`;
}

function wireInteractions() {
  const svg = document.getElementById("map-svg");
  if (!svg || svg.dataset.interactionsWired === "true") return;
  svg.dataset.interactionsWired = "true";
  let dragging = false;
  let last = { x: 0, y: 0 };

  svg.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const scale = event.deltaY > 0 ? 1.1 : 0.9;
      const newWidth = clamp(_viewBox.w * scale, VIEWBOX_INITIAL.w * 0.55, VIEWBOX_INITIAL.w * 2.2);
      const newHeight = newWidth * (VIEWBOX_INITIAL.h / VIEWBOX_INITIAL.w);
      _viewBox.x -= (newWidth - _viewBox.w) / 2;
      _viewBox.y -= (newHeight - _viewBox.h) / 2;
      _viewBox.w = newWidth;
      _viewBox.h = newHeight;
      applyViewBox();
    },
    { passive: false }
  );

  svg.addEventListener("mousedown", (event) => {
    dragging = true;
    last = { x: event.clientX, y: event.clientY };
  });
  window.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const scale = _viewBox.w / (svg.clientWidth || VIEWBOX_INITIAL.w);
    _viewBox.x -= (event.clientX - last.x) * scale;
    _viewBox.y -= (event.clientY - last.y) * scale;
    last = { x: event.clientX, y: event.clientY };
    applyViewBox();
  });
  window.addEventListener("mouseup", () => {
    dragging = false;
  });
}
