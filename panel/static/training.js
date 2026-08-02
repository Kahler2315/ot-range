// Student training UI backed by authoritative Flask/SQLite state.
// Accepted answers and unrevealed hint text remain server-only. The
// browser keeps only an in-memory rendering cache; localStorage is not
// consulted for progress, scores, hints, notes, policy, or auth state.

import { apiRequest } from "./api.js";

function emptyScenarioState() {
  return {
    status: "not_started",
    mode: null,
    scored: true,
    startedAt: null,
    completedAt: null,
    flagAttempts: {},
    hintsRevealed: {},
    flagsSolved: [],
    pointsEarned: {},
    documentsOpened: [],
    walkthroughOpened: false,
    answerKeyOpened: false,
    solutionLocked: false,
    notes: "",
  };
}

let _store = { profileId: null, policies: {}, scenarios: {} };

function getScenarioState(scenarioId) {
  if (!_store.scenarios[scenarioId]) {
    _store.scenarios[scenarioId] = emptyScenarioState();
  }
  return _store.scenarios[scenarioId];
}

export const TrainingStore = {
  async initialize() {
    _store = await apiRequest("/api/training");
    return _store;
  },
  get settings() {
    return _store.policies;
  },
  get profileId() {
    return _store.profileId;
  },
  getScenario: getScenarioState,
  async refreshScenario(scenarioId) {
    const data = await apiRequest(`/api/training/${scenarioId}`);
    _store.scenarios[scenarioId] = data.state;
    return data.state;
  },
  async refreshAll() {
    return this.initialize();
  },
  async resetScenario(scenarioId) {
    const data = await apiRequest(
      `/api/profiles/${_store.profileId}/progress/${scenarioId}/reset`,
      { method: "POST" }
    );
    _store.scenarios[scenarioId] = data.state;
    return data.state;
  },
  async resetAll() {
    const data = await apiRequest(`/api/profiles/${_store.profileId}/reset-all`, {
      method: "POST",
    });
    _store = data.training;
    return _store;
  },
  async exportAll() {
    return apiRequest(`/api/profiles/${_store.profileId}/export`);
  },
  replaceScenario(scenarioId, state) {
    _store.scenarios[scenarioId] = state;
  },
};

// ====================================================================
// Scoring display fallback. Earned values come from the backend's
// pointsEarned map; these helpers only render not-yet-solved flags.
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
  return apiRequest(`/api/training/${scenarioId}`, {
    method: "PATCH",
    body: { mode: state.mode, start: true },
  }).then((data) => {
    TrainingStore.replaceScenario(scenarioId, data.state);
    return data.state;
  });
}

export function recomputeStatus(scenarioId, flags) {
  void flags;
  return TrainingStore.refreshScenario(scenarioId).then((state) => state.status);
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

let _solutionDocs = [];
export function setSolutionDocs(docs) {
  _solutionDocs = docs;
}
export function isSolutionDoc(docKey) {
  return _solutionDocs.includes(docKey);
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
    title: "Opening the answer key will lock this attempt",
    message:
      "If you continue, your current score will be preserved, but you will be locked out of all further flag submissions for this attempt. You must reset the scenario attempt to earn more points. Go back if you opened this by accident.",
    confirmLabel: "Continue and lock attempt",
    cancelLabel: "Go back",
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
    const remaining = isSolved
      ? state.pointsEarned[flag.id]
      : remainingPoints(flag.points, effectiveRevealed);
    const attempts = state.flagAttempts[flag.id] || 0;

    const item = document.createElement("div");
    item.className = `flag-item ${isSolved ? "correct" : ""} ${isLocked ? "locked" : ""}`.trim();
    item.innerHTML = `
      <div class="flag-item-head">
        <span class="sid">${flag.id}</span>
        <span class="flag-category">${flag.category}</span>
        <span class="flag-points">${state.scored ? `${remaining} / ${flag.points} pts` : "Not scored"}</span>
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
    if (!TrainingStore.settings.hintsEnabled) {
      hintRow.innerHTML = '<span class="flag-evidence">Hints are unavailable under current instructor policies.</span>';
    }
    for (let level = 1; level <= (flag.hintCosts || []).length; level++) {
      if (!TrainingStore.settings.hintsEnabled) break;
      if (revealed.includes(level)) continue;
      const btn = document.createElement("button");
      btn.className = "btn-ghost";
      const cost = flag.hintCosts[level - 1];
      btn.textContent = state.mode === "guided" ? `Hint ${level} (free)` : `Hint ${level} (−${cost} pts)`;
      btn.disabled = isSolved || isLocked || (level > 1 && !revealed.includes(level - 1));
      btn.addEventListener("click", async () => {
        const ok = await confirmRevealHint(scenarioId, flag, level);
        if (!ok) return;
        try {
          const data = await apiRequest(`/api/flags/${scenarioId}/${flag.id}/hint/${level}`);
          TrainingStore.replaceScenario(scenarioId, data.state);
          renderFlagsSection(container, scenarioId, flags, onProgressChange);
          onProgressChange?.();
        } catch (error) {
          btn.textContent = error.message;
        }
      });
      hintRow.appendChild(btn);
    }

    for (const level of revealed) {
      const div = document.createElement("div");
      div.className = "hint-revealed";
      div.textContent = "Loading hint…";
      item.appendChild(div);
      apiRequest(`/api/flags/${scenarioId}/${flag.id}/hint/${level}`)
        .then((d) => {
          div.innerHTML = `<b>Hint ${level}:</b> ${escapeHtml(d.text)}`;
        })
        .catch((error) => {
          div.textContent = error.message;
        });
    }

    const input = item.querySelector("input");
    const checkBtn = item.querySelector(".check-btn");
    const statusIcon = item.querySelector(".flag-status-icon");
    checkBtn?.addEventListener("click", async () => {
      const answer = input.value.trim();
      if (!answer) return;
      let data;
      try {
        data = await apiRequest("/api/flags/check", {
          method: "POST",
          body: { scenario: scenarioId, flag_id: flag.id, answer, training_mode: state.mode },
        });
        await TrainingStore.refreshScenario(scenarioId);
      } catch (error) {
        statusIcon.textContent = "✕";
        statusIcon.className = "flag-status-icon incorrect";
        let msg = item.querySelector(".flag-status-text");
        if (!msg) {
          msg = document.createElement("span");
          msg.className = "flag-status-text";
          item.querySelector(".flag-input-row").after(msg);
        }
        msg.textContent = error.message;
        return;
      }
      const updatedState = getScenarioState(scenarioId);
      if (data.correct) {
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
          `${updatedState.flagAttempts[flag.id]} attempt${updatedState.flagAttempts[flag.id] === 1 ? "" : "s"}`;
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
  const policies = TrainingStore.settings;
  if (!state.mode) {
    state.mode = policies.independentModeEnabled ? "independent" : "guided";
  }
  const independentDisabled = !policies.independentModeEnabled;
  const guidedDisabled = !policies.guidedModeEnabled;
  container.innerHTML = `
    <label class="field-label" for="mode-select-${scenarioId}">Training mode</label>
    <div class="row" style="margin-top:4px">
      <select id="mode-select-${scenarioId}" ${locked ? "disabled" : ""}>
        <option value="independent" ${state.mode === "independent" ? "selected" : ""} ${independentDisabled ? "disabled" : ""}>Independent investigation${policies.scoredModeEnabled ? " (scored)" : ""}</option>
        <option value="guided" ${state.mode === "guided" ? "selected" : ""} ${guidedDisabled ? "disabled" : ""}>Guided learning</option>
      </select>
    </div>
    ${locked ? '<p class="flag-evidence">Mode is locked once an attempt has started. Reset the attempt to change it.</p>' : ""}
  `;
  const select = container.querySelector("select");
  if (!locked) {
    select.addEventListener("change", async () => {
      try {
        const data = await apiRequest(`/api/training/${scenarioId}`, {
          method: "PATCH",
          body: { mode: select.value, start: false },
        });
        TrainingStore.replaceScenario(scenarioId, data.state);
        onModeChange?.();
      } catch (error) {
        select.value = state.mode;
      }
    });
  }
}

export function wireNotesField(textarea, scenarioId) {
  const state = getScenarioState(scenarioId);
  textarea.value = state.notes || "";
  let saveTimer;
  textarea.addEventListener("input", () => {
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(async () => {
      const data = await apiRequest(`/api/training/${scenarioId}`, {
        method: "PATCH",
        body: { notes: textarea.value },
      });
      TrainingStore.replaceScenario(scenarioId, data.state);
    }, 250);
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
  const earned = state.scored
    ? solvedFlags.reduce((sum, flag) => sum + (state.pointsEarned[flag.id] ?? flag.points), 0)
    : null;
  const maxPoints = state.scored ? flags.reduce((sum, f) => sum + f.points, 0) : null;
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
    percentage: maxPoints > 0 ? Math.round((earned / maxPoints) * 100) : null,
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
      "Local training progress only — stored on this installation, not tamper-resistant, not certification evidence.",
  };
}

export function buildCourseReport(scenariosMeta, allFlags) {
  return {
    generatedAt: new Date().toISOString(),
    disclaimer:
      "Local training progress only — stored on this installation, not tamper-resistant, not certification evidence.",
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
  const score = r.finalScore ?? r.score;
  const maximum = r.maxScore ?? r.maximumScore;
  const status = r.attemptStatusLabel ?? r.attemptStatus;
  return `
    <h2>${escapeHtml(r.scenarioTitle)} (${r.scenario})</h2>
    <table>
      <tr><th>Status</th><td>${escapeHtml(status)}</td></tr>
      <tr><th>Mode</th><td>${escapeHtml(r.trainingMode || "—")}</td></tr>
      <tr><th>Score</th><td>${score === null ? "Not scored" : `${score} / ${maximum}`}</td></tr>
      <tr><th>Flags solved</th><td>${r.flagsSolved} / ${r.totalFlags}</td></tr>
      <tr><th>Hints used</th><td>${r.hintsUsed}</td></tr>
      <tr><th>Incorrect attempts</th><td>${r.incorrectAttempts}</td></tr>
      <tr><th>Started</th><td>${r.startedAt || "—"}</td></tr>
      <tr><th>Completed</th><td>${r.completedAt || "—"}</td></tr>
      <tr><th>Learning objectives</th><td>${r.learningObjectives.map(escapeHtml).join("; ")}</td></tr>
      <tr><th>Documentation opened</th><td>${(r.documentationOpened || []).map(escapeHtml).join(", ") || "—"}</td></tr>
      <tr><th>Answer key opened</th><td>${r.answerKeyOpened || r.solutionRevealed ? "Yes" : "No"}</td></tr>
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
  const reportBundle =
    !Array.isArray(reportOrReports) && reportOrReports?.scenarios ? reportOrReports : null;
  const rows = reportBundle
    ? reportBundle.scenarios
    : Array.isArray(reportOrReports)
      ? reportOrReports
      : [reportOrReports];
  const profile = reportBundle?.profile;
  const profileHtml = profile
    ? `<table>
      <tr><th>Learner</th><td>${escapeHtml(profile.displayName)}</td></tr>
      <tr><th>Learner ID</th><td>${escapeHtml(profile.learnerId || "—")}</td></tr>
      <tr><th>Organization</th><td>${escapeHtml(profile.organization || "—")}</td></tr>
      <tr><th>Course</th><td>${escapeHtml(profile.course || "—")}</td></tr>
      <tr><th>Section</th><td>${escapeHtml(profile.section || "—")}</td></tr>
      <tr><th>Instructor</th><td>${escapeHtml(profile.instructorName || "—")}</td></tr>
      <tr><th>Local profile ID</th><td>${escapeHtml(profile.localProfileId)}</td></tr>
    </table>`
    : "";
  win.document.write(`<!doctype html><html><head><meta charset="utf-8"><meta name="color-scheme" content="light only"><title>${escapeHtml(title)}</title>
    <style>
      :root { color-scheme: light only; }
      body { background: #fff; font-family: -apple-system, "Segoe UI", Arial, sans-serif; padding: 28px; color: #111; }
      h1 { font-size: 1.3rem; } h2 { font-size: 1.05rem; margin-top: 24px; }
      table { border-collapse: collapse; width: 100%; margin-bottom: 12px; }
      td, th { border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 0.85rem; vertical-align: top; }
      .disclaimer { color: #a15c00; font-size: 0.8rem; margin-top: 24px; }
    </style></head><body>
    <h1>${escapeHtml(title)}</h1>
    ${profileHtml}
    ${rows.map(reportRowsHtml).join("")}
    <p class="disclaimer">Local training progress only — stored on this installation, not tamper-resistant, not suitable as formal certification evidence.</p>
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
    totalEarned += report.finalScore || 0;
    totalMax += report.maxScore || 0;
    totalSolved += report.flagsSolved;
    totalFlagsCount += report.totalFlags;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${meta.id} — ${escapeHtml(meta.title)}</td>
      <td>${escapeHtml(lifecycleLabel(state))}</td>
      <td>${escapeHtml(state.mode || "—")}</td>
      <td>${report.flagsSolved}/${report.totalFlags}</td>
      <td>${report.finalScore === null ? "Not scored" : `${report.finalScore}/${report.maxScore}`}</td>
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
