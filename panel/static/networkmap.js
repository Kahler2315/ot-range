// Inline-SVG network map — no external diagramming library, no
// remote dependency. Topology data comes from /api/topology
// (panel/topology.py), which is itself cross-checked against
// docker-compose.yml in tests/test_topology.py — this file only
// renders what that data says, it doesn't invent layout facts beyond
// x/y screen positions.

// Manual layout (not force-directed) — 8 fixed nodes, deterministic
// positions are easier to reason about and verify than a physics
// simulation, and the topology is small and stable.
const NODE_LAYOUT = {
  attacker: { x: 90, y: 230 },
  router: { x: 270, y: 230 },
  openplc: { x: 460, y: 150 },
  "process-sim": { x: 460, y: 340 },
  hmi: { x: 650, y: 80 },
  historian: { x: 650, y: 230 },
  postgres: { x: 740, y: 340 },
  grafana: { x: 830, y: 230 },
};
const NODE_W = 122;
const NODE_H = 56;
const VIEWBOX_INITIAL = { x: 0, y: 0, w: 900, h: 460 };

const CONTAINER_NAME_BY_NODE = {
  "process-sim": "ot-range-process-sim-1",
  openplc: "ot-range-openplc-1",
  hmi: "ot-range-hmi-1",
  historian: "ot-range-historian-1",
  postgres: "ot-range-postgres-1",
  grafana: "ot-range-grafana-1",
  router: "ot-range-router-1",
};

let _topology = null;
let _statusData = null;
let _currentOverlayId = null;
let _showPorts = true;
let _viewBox = { ...VIEWBOX_INITIAL };

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
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
  if (!containerName) return null; // attacker: no persistent health to show
  const container = _statusData.docker.containers.find((c) => c.name === containerName);
  return container ? container.ok : null;
}

function render() {
  const svg = document.getElementById("map-svg");
  if (!svg || !_topology) return;
  svg.innerHTML = "";
  applyViewBox();

  const overlay = _currentOverlayId ? _topology.overlays[_currentOverlayId] : null;

  renderZones(svg);
  renderEdges(svg, overlay);
  renderNodes(svg, overlay);
}

function renderZones(svg) {
  const nodesByZone = {};
  for (const node of _topology.nodes) {
    (nodesByZone[node.zone] ||= []).push(node.id);
  }
  for (const [zoneId, nodeIds] of Object.entries(nodesByZone)) {
    if (zoneId === "boundary" || nodeIds.length === 0) continue; // router stands alone, no rect
    const pad = 55;
    const xs = nodeIds.map((id) => NODE_LAYOUT[id].x);
    const ys = nodeIds.map((id) => NODE_LAYOUT[id].y);
    const x0 = Math.min(...xs) - NODE_W / 2 - pad;
    const y0 = Math.min(...ys) - NODE_H / 2 - pad;
    const x1 = Math.max(...xs) + NODE_W / 2 + pad;
    const y1 = Math.max(...ys) + NODE_H / 2 + pad;
    svg.appendChild(
      svgEl("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, rx: 14, class: "map-zone-rect" })
    );
    const label = svgEl("text", { x: x0 + 12, y: y0 + 20, class: "map-zone-label" });
    label.textContent = _topology.zones[zoneId]?.label || zoneId;
    svg.appendChild(label);
  }
}

function renderEdges(svg, overlay) {
  const attackEdgeIds = new Set(overlay?.path_edges || []);
  for (const edge of _topology.edges) {
    const a = NODE_LAYOUT[edge.from];
    const b = NODE_LAYOUT[edge.to];
    if (!a || !b) continue;
    let cls = "map-edge";
    if (attackEdgeIds.has(edge.id)) cls += " attack-path";
    else if (edge.monitored) cls += " monitored";
    svg.appendChild(svgEl("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: cls }));
    if (_showPorts) {
      const midX = (a.x + b.x) / 2;
      const midY = (a.y + b.y) / 2 - 4;
      const label = svgEl("text", { x: midX, y: midY, "text-anchor": "middle", class: "map-zone-label" });
      label.textContent = edge.protocol;
      svg.appendChild(label);
    }
  }
}

function renderNodes(svg, overlay) {
  const affected = new Set(overlay?.affected_nodes || []);
  const detection = new Set(overlay?.detection_nodes || []);
  const truthNode = overlay?.truth_node;
  const spoofedNode = overlay?.spoofed_node;
  const compromisePort = overlay?.compromise_port;

  for (const node of _topology.nodes) {
    const pos = NODE_LAYOUT[node.id];
    if (!pos) continue;
    const classes = ["map-node"];
    if (affected.has(node.id)) classes.push("affected");
    if (detection.has(node.id)) classes.push("detection");

    const g = svgEl("g", {
      class: classes.join(" "),
      tabindex: "0",
      role: "button",
      "aria-label": `${node.label}, ${_topology.zones[node.zone]?.label || node.zone}`,
      transform: `translate(${pos.x - NODE_W / 2}, ${pos.y - NODE_H / 2})`,
    });
    g.appendChild(svgEl("rect", { width: NODE_W, height: NODE_H, rx: 8, class: "node-shape" }));

    const label = svgEl("text", { x: NODE_W / 2, y: 22, "text-anchor": "middle" });
    label.textContent = node.label;
    g.appendChild(label);

    if (_showPorts && node.ports.length > 0) {
      const portText = svgEl("text", { x: NODE_W / 2, y: 38, "text-anchor": "middle", class: "node-detail-text" });
      portText.textContent = node.ports.map((p) => p.port).join(", ");
      g.appendChild(portText);
    }

    const health = nodeHealth(node.id);
    if (health !== null) {
      g.appendChild(
        svgEl("circle", { cx: NODE_W - 10, cy: 10, r: 5, class: `health-dot ${health ? "ok" : "bad"}` })
      );
    }

    if (node.id === truthNode) {
      g.appendChild(annotationText("ground truth", "#3ecf8e"));
    } else if (node.id === spoofedNode) {
      g.appendChild(annotationText("spoofed view", "#ef5b5b"));
    } else if (compromisePort && compromisePort.node === node.id) {
      g.appendChild(annotationText(`port ${compromisePort.port}: unmonitored`, "#ef5b5b"));
    }

    g.addEventListener("click", () => showNodeDetail(node));
    g.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        showNodeDetail(node);
      }
    });
    svg.appendChild(g);
  }
}

function annotationText(text, fill) {
  const t = svgEl("text", { x: NODE_W / 2, y: NODE_H - 6, "text-anchor": "middle", class: "node-detail-text", fill });
  t.textContent = text;
  return t;
}

function showNodeDetail(node) {
  const panel = document.getElementById("map-node-detail");
  if (!panel) return;
  panel.hidden = false;
  const portsHtml = node.ports.length
    ? node.ports.map((p) => `${p.port}/${p.protocol}${p.monitored ? " (monitored)" : " (not monitored)"}`).join(", ")
    : "none published to host";
  panel.innerHTML = `<h4>${node.label}</h4><p>${node.detail}</p><p><b>Zone:</b> ${_topology.zones[node.zone]?.label || node.zone}</p><p><b>Ports:</b> ${portsHtml}</p>`;
}

function wireInteractions() {
  const svg = document.getElementById("map-svg");
  if (!svg) return;
  let dragging = false;
  let last = { x: 0, y: 0 };

  svg.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      const scale = ev.deltaY > 0 ? 1.1 : 0.9;
      const newW = clamp(_viewBox.w * scale, VIEWBOX_INITIAL.w * 0.4, VIEWBOX_INITIAL.w * 2.5);
      const newH = newW * (VIEWBOX_INITIAL.h / VIEWBOX_INITIAL.w);
      // Centered zoom (not cursor-relative) — simpler to reason about
      // and verify correct than pointer-anchored zoom math.
      _viewBox.x -= (newW - _viewBox.w) / 2;
      _viewBox.y -= (newH - _viewBox.h) / 2;
      _viewBox.w = newW;
      _viewBox.h = newH;
      applyViewBox();
    },
    { passive: false }
  );

  svg.addEventListener("mousedown", (ev) => {
    dragging = true;
    last = { x: ev.clientX, y: ev.clientY };
  });
  window.addEventListener("mousemove", (ev) => {
    if (!dragging) return;
    const dx = ev.clientX - last.x;
    const dy = ev.clientY - last.y;
    last = { x: ev.clientX, y: ev.clientY };
    const scale = _viewBox.w / (svg.clientWidth || VIEWBOX_INITIAL.w);
    _viewBox.x -= dx * scale;
    _viewBox.y -= dy * scale;
    applyViewBox();
  });
  window.addEventListener("mouseup", () => {
    dragging = false;
  });
}
