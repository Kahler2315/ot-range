// Training lifecycle, scoring, hints, and instructor settings.
//
// All attempt/score state lives in localStorage — this project has no
// server-side user accounts, and the spec this was built against is
// explicit that a local, single-user implementation is correct for
// now, structured so it could later be swapped for real accounts
// without changing the UI code that reads it (see TrainingStore).
//
// Accepted flag answers and hint text are NEVER stored here — only
// which flag ids were solved, which hint *levels* were revealed, and
// timestamps/notes. Hint text is re-fetched from the server each time
// it's displayed, never cached into localStorage.

const STORE_KEY = "ot-range-training-v1";

const DEFAULT_SETTINGS = {
  scoredModeEnabled: true,
  hintsEnabled: true,
  answerKeyEnabled: true,
  walkthroughEnabled: true,
  passphraseHash: null,
  scenarioAvailability: {},
};

function emptyScenarioState() {
  return {
    status: "not_started",
    mode: null,
    startedAt: null,
    completedAt: null,
    flagAttempts: {},
    hintsRevealed: {},
    flagsSolved: [],
    walkthroughOpened: false,
    answerKeyOpened: false,
    solutionLocked: false,
    notes: "",
  };
}

function loadStore() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
    if (raw && raw.version === 1 && raw.settings && raw.scenarios) return raw;
  } catch {
    /* fall through to a fresh store */
  }
  return { version: 1, settings: { ...DEFAULT_SETTINGS }, scenarios: {} };
}

let _store = loadStore();

function saveStore() {
  localStorage.setItem(STORE_KEY, JSON.stringify(_store));
}

function getScenarioState(scenarioId) {
  if (!_store.scenarios[scenarioId]) {
    _store.scenarios[scenarioId] = emptyScenarioState();
  }
  return _store.scenarios[scenarioId];
}

export const TrainingStore = {
  get settings() {
    return _store.settings;
  },
  getScenario: getScenarioState,
  save: saveStore,
  resetScenario(scenarioId) {
    delete _store.scenarios[scenarioId];
    saveStore();
  },
  resetAll() {
    _store = { version: 1, settings: { ..._store.settings }, scenarios: {} };
    saveStore();
  },
  exportAll() {
    return JSON.parse(JSON.stringify(_store));
  },
  updateSettings(patch) {
    _store.settings = { ..._store.settings, ...patch };
    saveStore();
  },
};

// ====================================================================
// Scoring — mirrors scenarios/scoring.py exactly (see
// tests/test_scoring.py for the tested Python spec this transliterates;
// there is no JS test runner in this repo, so this block is verified
// by hand against that file rather than independently). If the point
// values or hint-level rates ever change, both files must change
// together. For this project's actual point values (5,6,7,8,9,10,12)
// and rates (0.20, 0.35), Math.round (round-half-away-from-zero) and
// Python's round() (round-half-to-even) never diverge — the only exact
// .5 case in the current data (10 * 0.35 = 3.5) rounds to 4 under both
// rules, since 4 happens to be both "round up" and "nearest even".
// ====================================================================

const HINT_LEVEL_RATES = { 1: 0.2, 2: 0.35 };
const MINIMUM_POINTS = 1;

export function hintCost(basePoints, level) {
  const rate = HINT_LEVEL_RATES[level];
  if (rate === undefined) throw new Error(`no such hint level: ${level}`);
  return Math.round(basePoints * rate);
}

export function remainingPoints(basePoints, revealedLevels) {
  const uniqueLevels = [...new Set(revealedLevels)];
  const totalCost = uniqueLevels.reduce((sum, lvl) => sum + hintCost(basePoints, lvl), 0);
  return Math.max(MINIMUM_POINTS, basePoints - totalCost);
}

function effectiveRevealedLevels(state, flagId) {
  // Guided mode: hints are free — never deduct, regardless of what
  // was clicked.
  if (state.mode === "guided") return [];
  return state.hintsRevealed[flagId] || [];
}

// ====================================================================
// Lifecycle
// ====================================================================

export function ensureAttemptStarted(scenarioId) {
  const state = getScenarioState(scenarioId);
  if (state.status === "not_started") {
    if (!state.mode) state.mode = "independent";
    state.status = "in_progress";
    state.startedAt = new Date().toISOString();
    saveStore();
  }
}

export function recomputeStatus(scenarioId, flags) {
  const state = getScenarioState(scenarioId);
  if (state.solutionLocked) {
    state.status = "solution_revealed";
    saveStore();
    return state.status;
  }
  const allSolved = flags.length > 0 && flags.every((f) => state.flagsSolved.includes(f.id));
  if (allSolved) {
    const usedHints = Object.values(state.hintsRevealed).some((levels) => levels.length > 0);
    state.status = usedHints && state.mode !== "guided" ? "completed_with_assistance" : "completed";
    if (!state.completedAt) state.completedAt = new Date().toISOString();
  } else if (state.status !== "not_started") {
    state.status = "in_progress";
  }
  saveStore();
  return state.status;
}

export function lifecycleLabel(state) {
  const labels = {
    not_started: "Not started",
    in_progress: "In progress",
    completed: state.mode === "guided" ? "Guided completion" : "Completed",
    completed_with_assistance: "Completed with assistance",
    solution_revealed: "Completed with solution",
  };
  return labels[state.status] || state.status;
}

export function recordFlagAttempt(scenarioId, flagId) {
  const state = getScenarioState(scenarioId);
  state.flagAttempts[flagId] = (state.flagAttempts[flagId] || 0) + 1;
  saveStore();
}

export function markFlagSolved(scenarioId, flagId) {
  const state = getScenarioState(scenarioId);
  if (!state.flagsSolved.includes(flagId)) state.flagsSolved.push(flagId);
  saveStore();
}

export function revealHintLevel(scenarioId, flagId, level) {
  const state = getScenarioState(scenarioId);
  state.hintsRevealed[flagId] = state.hintsRevealed[flagId] || [];
  if (!state.hintsRevealed[flagId].includes(level)) state.hintsRevealed[flagId].push(level);
  saveStore();
}

let _solutionDocs = [];
export function setSolutionDocs(docs) {
  _solutionDocs = docs;
}
export function isSolutionDoc(docKey) {
  return _solutionDocs.includes(docKey);
}

export function markDocOpened(scenarioId, docKey) {
  const state = getScenarioState(scenarioId);
  if (isSolutionDoc(docKey)) {
    state.answerKeyOpened = true;
    if (state.mode !== "guided") state.solutionLocked = true;
  } else {
    state.walkthroughOpened = true;
  }
  saveStore();
}

// ====================================================================
// Custom confirm dialog — replaces every window.confirm()/alert() in
// the app, per the training spec's explicit requirement.
// ====================================================================

export function showConfirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
}) {
  return new Promise((resolve) => {
    const overlay = document.getElementById("dialog-overlay");
    document.getElementById("dialog-title").textContent = title;
    document.getElementById("dialog-message").textContent = message;
    const confirmBtn = document.getElementById("dialog-confirm");
    const cancelBtn = document.getElementById("dialog-cancel");
    confirmBtn.textContent = confirmLabel;
    cancelBtn.textContent = cancelLabel;
    confirmBtn.className = danger ? "btn-danger" : "btn-primary";

    const cleanup = (result) => {
      overlay.hidden = true;
      confirmBtn.removeEventListener("click", onConfirm);
      cancelBtn.removeEventListener("click", onCancel);
      overlay.removeEventListener("keydown", onKeydown);
      resolve(result);
    };
    const onConfirm = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onKeydown = (ev) => {
      if (ev.key === "Escape") cleanup(false);
    };

    confirmBtn.addEventListener("click", onConfirm);
    cancelBtn.addEventListener("click", onCancel);
    overlay.addEventListener("keydown", onKeydown);
    overlay.hidden = false;
    confirmBtn.focus();
  });
}

export async function confirmRevealHint(scenarioId, flag, level) {
  const state = getScenarioState(scenarioId);
  const already = effectiveRevealedLevels(state, flag.id);
  if (state.mode === "guided") return true; // free in guided mode, no prompt needed
  const before = remainingPoints(flag.points, already);
  const after = remainingPoints(flag.points, [...already, level]);
  return showConfirmDialog({
    title: `Reveal Hint ${level}?`,
    message: `This will reduce the maximum value of this flag from ${before} points to ${after} points.`,
    confirmLabel: "Reveal hint",
    cancelLabel: "Keep investigating",
  });
}

export function confirmRevealSolution() {
  return showConfirmDialog({
    title: "Open the answer key?",
    message:
      "Opening this resource will reveal solution information and permanently lock flag submission for this attempt. Your current score will be preserved, but you will not be able to earn additional points unless the scenario attempt is reset.",
    confirmLabel: "Reveal solution and lock attempt",
    cancelLabel: "Continue investigation",
    danger: true,
  });
}

export function confirmResetAttempt() {
  return showConfirmDialog({
    title: "Reset this attempt?",
    message:
      "This clears this scenario's local progress, hints used, attempt history, notes, and score. It does not change any source files or accepted answers. This cannot be undone.",
    confirmLabel: "Reset attempt",
    cancelLabel: "Cancel",
    danger: true,
  });
}

// ====================================================================
// Flags UI (rendered inside the scenario detail drawer)
// ====================================================================

export function renderFlagsSection(container, scenarioId, flags, onProgressChange) {
  const state = getScenarioState(scenarioId);
  container.innerHTML = "";

  for (const flag of flags) {
    const revealed = state.hintsRevealed[flag.id] || [];
    const effectiveRevealed = effectiveRevealedLevels(state, flag.id);
    const isSolved = state.flagsSolved.includes(flag.id);
    const isLocked = state.solutionLocked && state.mode !== "guided";
    const remaining = remainingPoints(flag.points, effectiveRevealed);
    const attempts = state.flagAttempts[flag.id] || 0;

    const item = document.createElement("div");
    item.className = `flag-item ${isSolved ? "correct" : ""} ${isLocked ? "locked" : ""}`.trim();
    item.innerHTML = `
      <div class="flag-item-head">
        <span class="sid">${flag.id}</span>
        <span class="flag-category">${flag.category}</span>
        <span class="flag-points">${remaining} / ${flag.points} pts</span>
      </div>
      <p class="flag-prompt">${escapeHtml(flag.prompt)}</p>
      <p class="flag-evidence">Evidence: ${escapeHtml(flag.evidenceSource)}</p>
      <div class="flag-input-row">
        <input type="text" placeholder="your answer" ${isSolved || isLocked ? "disabled" : ""}
               value="${isSolved ? "✓ captured" : ""}"
               aria-label="Answer for flag ${flag.id}">
        <button class="btn-primary check-btn" ${isSolved || isLocked ? "disabled" : ""}>Check</button>
        <span class="flag-status-icon ${isSolved ? "correct" : ""}" role="status">${isSolved ? "✓" : ""}</span>
      </div>
      <span class="flag-attempts">${attempts} attempt${attempts === 1 ? "" : "s"}</span>
      <div class="hint-row"></div>
      ${isLocked ? '<p class="locked-note">Locked — the answer key was opened for this attempt. Reset the attempt to earn more points.</p>' : ""}
    `;
    container.appendChild(item);

    const hintRow = item.querySelector(".hint-row");
    for (let level = 1; level <= (flag.hintCosts || []).length; level++) {
      if (revealed.includes(level)) continue;
      const btn = document.createElement("button");
      btn.className = "btn-ghost";
      const cost = flag.hintCosts[level - 1];
      btn.textContent = state.mode === "guided" ? `Hint ${level} (free)` : `Hint ${level} (−${cost} pts)`;
      btn.disabled = isSolved || isLocked || (level > 1 && !revealed.includes(level - 1));
      btn.addEventListener("click", async () => {
        const ok = await confirmRevealHint(scenarioId, flag, level);
        if (!ok) return;
        revealHintLevel(scenarioId, flag.id, level);
        renderFlagsSection(container, scenarioId, flags, onProgressChange);
        onProgressChange?.();
      });
      hintRow.appendChild(btn);
    }

    for (const level of revealed) {
      const div = document.createElement("div");
      div.className = "hint-revealed";
      div.textContent = "Loading hint…";
      item.appendChild(div);
      fetch(`/api/flags/${scenarioId}/${flag.id}/hint/${level}`)
        .then((r) => r.json())
        .then((d) => {
          div.innerHTML = `<b>Hint ${level}:</b> ${escapeHtml(d.text)}`;
        });
    }

    const input = item.querySelector("input");
    const checkBtn = item.querySelector(".check-btn");
    const statusIcon = item.querySelector(".flag-status-icon");
    checkBtn?.addEventListener("click", async () => {
      const answer = input.value.trim();
      if (!answer) return;
      ensureAttemptStarted(scenarioId);
      recordFlagAttempt(scenarioId, flag.id);
      const resp = await fetch("/api/flags/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: scenarioId, flag_id: flag.id, answer }),
      });
      const data = await resp.json();
      if (data.correct) {
        markFlagSolved(scenarioId, flag.id);
        recomputeStatus(scenarioId, flags);
        renderFlagsSection(container, scenarioId, flags, onProgressChange);
      } else {
        statusIcon.textContent = "✕";
        statusIcon.setAttribute("aria-label", "Incorrect");
        statusIcon.className = "flag-status-icon incorrect";
        let msg = item.querySelector(".flag-status-text");
        if (!msg) {
          msg = document.createElement("span");
          msg.className = "flag-status-text";
          item.querySelector(".flag-input-row").after(msg);
        }
        msg.textContent = "Not accepted. Recheck the evidence and try again.";
        item.querySelector(".flag-attempts").textContent =
          `${state.flagAttempts[flag.id]} attempt${state.flagAttempts[flag.id] === 1 ? "" : "s"}`;
      }
      onProgressChange?.();
    });
    input?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") checkBtn.click();
    });
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

// ====================================================================
// Mode selector + notes (rendered inside the scenario detail drawer)
// ====================================================================

export function renderModeSelector(container, scenarioId, onModeChange) {
  const state = getScenarioState(scenarioId);
  const locked = state.status !== "not_started";
  if (!state.mode) state.mode = "independent";
  container.innerHTML = `
    <label class="field-label" for="mode-select-${scenarioId}">Training mode</label>
    <div class="row" style="margin-top:4px">
      <select id="mode-select-${scenarioId}" ${locked ? "disabled" : ""}>
        <option value="independent" ${state.mode === "independent" ? "selected" : ""}>Independent investigation (scored)</option>
        <option value="guided" ${state.mode === "guided" ? "selected" : ""}>Guided learning (informational)</option>
      </select>
    </div>
    ${locked ? '<p class="flag-evidence">Mode is locked once an attempt has started. Reset the attempt to change it.</p>' : ""}
  `;
  const select = container.querySelector("select");
  if (!locked) {
    select.addEventListener("change", () => {
      state.mode = select.value;
      saveStore();
      onModeChange?.();
    });
  }
}

export function wireNotesField(textarea, scenarioId) {
  const state = getScenarioState(scenarioId);
  textarea.value = state.notes || "";
  textarea.addEventListener("input", () => {
    state.notes = textarea.value;
    saveStore();
  });
}

// ====================================================================
// Lifecycle badges on scenario cards + header flags total
// ====================================================================

export function renderLifecycleBadges(allFlags) {
  let totalSolved = 0;
  let totalFlags = 0;
  for (const [scenarioId, flags] of Object.entries(allFlags)) {
    const state = getScenarioState(scenarioId);
    const solvedCount = flags.filter((f) => state.flagsSolved.includes(f.id)).length;
    totalSolved += solvedCount;
    totalFlags += flags.length;

    const badge = document.querySelector(`[data-lifecycle="${scenarioId}"]`);
    if (badge) {
      badge.textContent = lifecycleLabel(state);
      badge.className = `lifecycle-badge status-${state.status}`;
    }
    const countEl = document.querySelector(`[data-flag-count="${scenarioId}"]`);
    if (countEl) {
      countEl.textContent = `${solvedCount}/${flags.length} flags`;
      countEl.classList.toggle("complete", solvedCount === flags.length && flags.length > 0);
    }
  }
  const totalEl = document.getElementById("flags-total");
  if (totalEl) totalEl.textContent = `${totalSolved} / ${totalFlags}`;
  const metricFlags = document.getElementById("metric-flags");
  if (metricFlags) metricFlags.textContent = `${totalSolved}/${totalFlags}`;
}

// ====================================================================
// Completion reports
// ====================================================================

export function buildCompletionReport(scenarioId, scenarioMeta, flags) {
  const state = getScenarioState(scenarioId);
  const solvedFlags = flags.filter((f) => state.flagsSolved.includes(f.id));
  const earned = solvedFlags.reduce(
    (sum, f) => sum + remainingPoints(f.points, effectiveRevealedLevels(state, f.id)),
    0
  );
  const maxPoints = flags.reduce((sum, f) => sum + f.points, 0);
  const hintsUsedCount = Object.values(state.hintsRevealed).reduce((sum, levels) => sum + levels.length, 0);
  const incorrectAttempts = Object.entries(state.flagAttempts).reduce((sum, [flagId, count]) => {
    const wrongAttempts = state.flagsSolved.includes(flagId) ? count - 1 : count;
    return sum + Math.max(0, wrongAttempts);
  }, 0);
  const durationMs =
    state.startedAt && state.completedAt ? new Date(state.completedAt) - new Date(state.startedAt) : null;

  return {
    scenario: scenarioId,
    scenarioTitle: scenarioMeta.title,
    attemptStatus: state.status,
    attemptStatusLabel: lifecycleLabel(state),
    trainingMode: state.mode,
    finalScore: earned,
    maxScore: maxPoints,
    percentage: maxPoints > 0 ? Math.round((earned / maxPoints) * 100) : 0,
    flagsSolved: solvedFlags.length,
    totalFlags: flags.length,
    hintsUsed: hintsUsedCount,
    incorrectAttempts,
    startedAt: state.startedAt,
    completedAt: state.completedAt,
    durationMs,
    learningObjectives: scenarioMeta.objectives || [],
    walkthroughOpened: state.walkthroughOpened,
    answerKeyOpened: state.answerKeyOpened,
    detectionOutcome: scenarioMeta.caught_by,
    notes: state.notes,
    generatedAt: new Date().toISOString(),
    disclaimer:
      "Local training progress only — stored in this browser, not tamper-resistant, not certification evidence.",
  };
}

export function buildCourseReport(scenariosMeta, allFlags) {
  return {
    generatedAt: new Date().toISOString(),
    disclaimer:
      "Local training progress only — stored in this browser, not tamper-resistant, not certification evidence.",
    scenarios: scenariosMeta.map((meta) => buildCompletionReport(meta.id, meta, allFlags[meta.id] || [])),
  };
}

export function exportJSON(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function reportRowsHtml(r) {
  return `
    <h2>${escapeHtml(r.scenarioTitle)} (${r.scenario})</h2>
    <table>
      <tr><th>Status</th><td>${escapeHtml(r.attemptStatusLabel)}</td></tr>
      <tr><th>Mode</th><td>${escapeHtml(r.trainingMode || "—")}</td></tr>
      <tr><th>Score</th><td>${r.finalScore} / ${r.maxScore} (${r.percentage}%)</td></tr>
      <tr><th>Flags solved</th><td>${r.flagsSolved} / ${r.totalFlags}</td></tr>
      <tr><th>Hints used</th><td>${r.hintsUsed}</td></tr>
      <tr><th>Incorrect attempts</th><td>${r.incorrectAttempts}</td></tr>
      <tr><th>Started</th><td>${r.startedAt || "—"}</td></tr>
      <tr><th>Completed</th><td>${r.completedAt || "—"}</td></tr>
      <tr><th>Learning objectives</th><td>${r.learningObjectives.map(escapeHtml).join("; ")}</td></tr>
      <tr><th>Walkthrough opened</th><td>${r.walkthroughOpened ? "Yes" : "No"}</td></tr>
      <tr><th>Answer key opened</th><td>${r.answerKeyOpened ? "Yes" : "No"}</td></tr>
      <tr><th>Detection outcome</th><td>${escapeHtml(r.detectionOutcome)}</td></tr>
      <tr><th>Notes</th><td>${escapeHtml(r.notes || "—")}</td></tr>
    </table>`;
}

export function openPrintableReport(reportOrReports, title) {
  const win = window.open("", "_blank");
  if (!win) {
    alert("Pop-up blocked — allow pop-ups for this page to view the printable report.");
    return;
  }
  const rows = Array.isArray(reportOrReports) ? reportOrReports : [reportOrReports];
  win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>
    <style>
      body { font-family: -apple-system, "Segoe UI", Arial, sans-serif; padding: 28px; color: #111; }
      h1 { font-size: 1.3rem; } h2 { font-size: 1.05rem; margin-top: 24px; }
      table { border-collapse: collapse; width: 100%; margin-bottom: 12px; }
      td, th { border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 0.85rem; vertical-align: top; }
      .disclaimer { color: #a15c00; font-size: 0.8rem; margin-top: 24px; }
    </style></head><body>
    <h1>${escapeHtml(title)}</h1>
    ${rows.map(reportRowsHtml).join("")}
    <p class="disclaimer">Local training progress only — stored in this browser, not tamper-resistant, not suitable as formal certification evidence.</p>
    </body></html>`);
  win.document.close();
  win.focus();
}

// ====================================================================
// Progress section (Investigation Progress nav)
// ====================================================================

export function renderProgressSection(scenariosMeta, allFlags, onExportOne) {
  const tbody = document.getElementById("progress-table-body");
  const summary = document.getElementById("progress-summary");
  if (!tbody) return;
  tbody.innerHTML = "";
  let totalEarned = 0;
  let totalMax = 0;
  let totalSolved = 0;
  let totalFlagsCount = 0;

  for (const meta of scenariosMeta) {
    const flags = allFlags[meta.id] || [];
    const state = getScenarioState(meta.id);
    const report = buildCompletionReport(meta.id, meta, flags);
    totalEarned += report.finalScore;
    totalMax += report.maxScore;
    totalSolved += report.flagsSolved;
    totalFlagsCount += report.totalFlags;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${meta.id} — ${escapeHtml(meta.title)}</td>
      <td>${escapeHtml(lifecycleLabel(state))}</td>
      <td>${escapeHtml(state.mode || "—")}</td>
      <td>${report.flagsSolved}/${report.totalFlags}</td>
      <td>${report.finalScore}/${report.maxScore}</td>
      <td>${report.hintsUsed}</td>
      <td><button class="btn-ghost export-one-btn" data-scenario="${meta.id}">Export</button></td>
    `;
    tbody.appendChild(tr);
    tr.querySelector(".export-one-btn").addEventListener("click", () => onExportOne?.(meta.id));
  }

  summary.innerHTML = `
    <div class="metric"><span class="metric-value">${totalSolved}/${totalFlagsCount}</span><span class="metric-label">flags solved</span></div>
    <div class="metric"><span class="metric-value">${totalEarned}/${totalMax}</span><span class="metric-label">points</span></div>
  `;
}

// ====================================================================
// Instructor settings — an OPTIONAL local convenience lock, not real
// authentication. Only the SHA-256 hash of the passphrase is ever
// stored (Web Crypto SubtleCrypto, native, no dependency); the
// passphrase itself never touches localStorage.
// ====================================================================

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function initInstructorPanel(scenariosMeta, onSettingsChanged) {
  const lockedView = document.getElementById("instructor-locked-view");
  const settingsView = document.getElementById("instructor-settings-view");
  const unlockBtn = document.getElementById("instructor-unlock-btn");
  const passInput = document.getElementById("instructor-passphrase-input");

  const renderSettings = () => {
    const s = TrainingStore.settings;
    settingsView.innerHTML = `
      <div class="settings-row"><label for="set-scored">Scored mode enabled</label><input type="checkbox" id="set-scored" ${s.scoredModeEnabled ? "checked" : ""}></div>
      <div class="settings-row"><label for="set-hints">Hints enabled</label><input type="checkbox" id="set-hints" ${s.hintsEnabled ? "checked" : ""}></div>
      <div class="settings-row"><label for="set-answerkey">Answer key access</label><input type="checkbox" id="set-answerkey" ${s.answerKeyEnabled ? "checked" : ""}></div>
      <div class="settings-row"><label for="set-walkthrough">Detection/impact docs access</label><input type="checkbox" id="set-walkthrough" ${s.walkthroughEnabled ? "checked" : ""}></div>
      ${scenariosMeta
        .map(
          (m) => `<div class="settings-row"><label>${m.id} available</label><input type="checkbox" class="set-scenario-avail" data-scenario="${m.id}" ${s.scenarioAvailability[m.id] !== false ? "checked" : ""}></div>`
        )
        .join("")}
      <div class="settings-row"><label for="set-passphrase">New passphrase (blank = remove lock)</label><input type="password" id="set-passphrase" placeholder="leave blank for none"></div>
      <div class="row" style="margin-top:12px">
        <button class="btn-primary" id="settings-save-btn">Save settings</button>
        <button class="btn-ghost" id="settings-export-btn">Export all local progress</button>
        <button class="btn-secondary-danger" id="settings-reset-all-btn">Reset all local progress</button>
      </div>
    `;
    settingsView.querySelector("#settings-save-btn").addEventListener("click", async () => {
      const patch = {
        scoredModeEnabled: settingsView.querySelector("#set-scored").checked,
        hintsEnabled: settingsView.querySelector("#set-hints").checked,
        answerKeyEnabled: settingsView.querySelector("#set-answerkey").checked,
        walkthroughEnabled: settingsView.querySelector("#set-walkthrough").checked,
        scenarioAvailability: {},
      };
      settingsView.querySelectorAll(".set-scenario-avail").forEach((cb) => {
        patch.scenarioAvailability[cb.dataset.scenario] = cb.checked;
      });
      const newPass = settingsView.querySelector("#set-passphrase").value;
      if (newPass) patch.passphraseHash = await sha256Hex(newPass);
      TrainingStore.updateSettings(patch);
      onSettingsChanged?.();
    });
    settingsView.querySelector("#settings-export-btn").addEventListener("click", () => {
      exportJSON(TrainingStore.exportAll(), "ot-range-local-progress.json");
    });
    settingsView.querySelector("#settings-reset-all-btn").addEventListener("click", async () => {
      const ok = await showConfirmDialog({
        title: "Reset ALL local progress?",
        message:
          "This erases every scenario's local progress, scores, hints, and notes in this browser. Instructor settings are kept. This cannot be undone.",
        confirmLabel: "Reset everything",
        cancelLabel: "Cancel",
        danger: true,
      });
      if (ok) {
        TrainingStore.resetAll();
        onSettingsChanged?.();
      }
    });
  };

  const tryUnlock = async () => {
    const hash = TrainingStore.settings.passphraseHash;
    if (!hash || (await sha256Hex(passInput.value)) === hash) {
      lockedView.hidden = true;
      settingsView.hidden = false;
      renderSettings();
    } else {
      passInput.value = "";
      passInput.placeholder = "incorrect passphrase";
    }
  };
  unlockBtn.addEventListener("click", tryUnlock);
  passInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") tryUnlock();
  });
}

// ====================================================================
// Small shared overlay utilities (Escape-to-close, basic focus trap)
// used by app.js for the drawer and doc-viewer modal.
// ====================================================================

export function trapFocus(container) {
  const focusable = container.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  if (focusable.length === 0) return () => {};
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const onKeydown = (ev) => {
    if (ev.key !== "Tab") return;
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault();
      first.focus();
    }
  };
  container.addEventListener("keydown", onKeydown);
  first.focus();
  return () => container.removeEventListener("keydown", onKeydown);
}
