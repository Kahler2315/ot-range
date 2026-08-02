import { apiRequest, downloadJSON } from "./api.js";

let profiles = [];
let policies = null;

function text(value) {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

function renderAnalytics(analytics) {
  const metrics = [
    [analytics.profiles, "learner profiles"],
    [analytics.attempts, "scenario attempts"],
    [analytics.independent_attempts, "independent"],
    [analytics.guided_attempts, "guided"],
    [analytics.completed_attempts, "completed"],
    [analytics.solution_revealed_attempts, "solution revealed"],
    [analytics.unread_integrity_events, "unread integrity events"],
    [text(analytics.average_solved_flag_points), "average solved-flag points"],
  ];
  document.getElementById("analytics-grid").innerHTML = metrics
    .map(([value, label]) => `<div class="analytics-card"><span class="metric-value">${value}</span><span class="metric-label">${label}</span></div>`)
    .join("");
  renderRankedList("most-used-hints", analytics.most_used_hints, "uses");
  renderRankedList("missed-flags", analytics.frequently_missed_flags, "misses");
}

function renderRankedList(id, items, measure) {
  const target = document.getElementById(id);
  if (!items.length) {
    target.innerHTML = '<p class="status-empty">No activity recorded yet.</p>';
    return;
  }
  target.innerHTML = items
    .map((item) => `<div><span>${item.scenario_id} · ${item.flag_id}</span><b>${item[measure]} ${measure}</b></div>`)
    .join("");
}

function renderRangeHealth(status) {
  const healthy = status.docker.containers.filter((item) => item.ok).length;
  const state = status.busy ? "Busy" : status.docker.all_healthy ? "Ready" : status.docker.any_present ? "Degraded" : "Offline";
  document.getElementById("instructor-range-health").innerHTML = `
    <div class="health-summary"><span class="readiness-dot ${status.docker.all_healthy ? "ok" : "bad"}"></span><b>${state}</b></div>
    <p class="meta-line">${healthy} of ${status.docker.containers.length} containers healthy · ${status.busy ? "A range job is running." : "No range job is running."}</p>`;
}

function renderIntegrityEvents(events) {
  const target = document.getElementById("integrity-event-list");
  const unread = events.filter((event) => !event.acknowledged).length;
  document.getElementById("integrity-unread-count").textContent = `${unread} unread`;
  if (!events.length) {
    target.innerHTML = '<p class="status-empty">No integrity-relevant activity recorded yet.</p>';
    return;
  }
  target.innerHTML = events.map((event) => {
    const details = event.details || {};
    const resetAfterSolution = event.event_type === "attempt_reset" && details.prior_solution_exposure;
    const label = event.event_type.replaceAll("_", " ");
    return `<div class="integrity-event ${resetAfterSolution ? "integrity-event-prominent" : ""}">
      <span><b>${escapeHtml(event.display_name)}</b> · ${escapeHtml(event.scenario_id || "All scenarios")}<small>${escapeHtml(label)} · ${escapeHtml(event.actor_type)} · ${escapeHtml(event.occurred_at)}</small></span>
      ${event.acknowledged ? '<b>Reviewed</b>' : `<button class="btn-ghost" data-integrity-ack="${event.id}">Mark reviewed</button>`}
    </div>`;
  }).join("");
  target.querySelectorAll("[data-integrity-ack]").forEach((button) => {
    button.addEventListener("click", async () => {
      await apiRequest(`/api/instructor/integrity-events/${button.dataset.integrityAck}/acknowledge`, { method: "POST" });
      await refresh();
    });
  });
}

function renderProfiles() {
  const tbody = document.getElementById("instructor-profile-rows");
  if (!profiles.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="status-empty">No local learner profiles.</td></tr>';
    return;
  }
  tbody.innerHTML = profiles.map((profile) => `
    <tr>
      <td><b>${escapeHtml(profile.display_name)}</b><small>${escapeHtml(text(profile.learner_id))}</small></td>
      <td>${escapeHtml(text(profile.course))}<small>${escapeHtml(text(profile.section))}</small></td>
      <td>${escapeHtml(text(profile.last_activity_at))}</td>
      <td class="table-actions">
        <button class="btn-ghost" data-profile-history="${profile.id}">View history</button>
        <button class="btn-ghost" data-profile-export="${profile.id}">Export</button>
        <button class="btn-ghost" data-profile-reset="${profile.id}">Reset progress</button>
        <button class="btn-secondary-danger" data-profile-delete="${profile.id}">Delete</button>
      </td>
    </tr>`).join("");
  tbody.querySelectorAll("[data-profile-export]").forEach((button) => button.addEventListener("click", () => exportProfile(button.dataset.profileExport)));
  tbody.querySelectorAll("[data-profile-history]").forEach((button) => button.addEventListener("click", () => viewProfileHistory(button.dataset.profileHistory)));
  tbody.querySelectorAll("[data-profile-reset]").forEach((button) => button.addEventListener("click", () => destructiveProfileAction(button.dataset.profileReset, "reset")));
  tbody.querySelectorAll("[data-profile-delete]").forEach((button) => button.addEventListener("click", () => destructiveProfileAction(button.dataset.profileDelete, "delete")));
}

function renderPolicies() {
  const form = document.getElementById("policy-form");
  for (const input of form.querySelectorAll("input[type=checkbox]")) input.checked = Boolean(policies[input.name]);
  for (const input of document.querySelectorAll("[data-scenario-policy]")) {
    input.checked = Boolean(policies.scenario_availability[input.dataset.scenarioPolicy]);
  }
}

async function refresh() {
  try {
    const [overview, profileData, policyData] = await Promise.all([
      apiRequest("/api/instructor/overview"),
      apiRequest("/api/instructor/profiles"),
      apiRequest("/api/instructor/policies"),
    ]);
    profiles = profileData.profiles;
    policies = policyData;
    renderAnalytics(overview.analytics);
    renderIntegrityEvents(overview.integrityEvents || []);
    renderRangeHealth(overview.status);
    renderProfiles();
    renderPolicies();
  } catch (error) {
    if (error.status === 401) window.location.assign("/instructor/login");
    else document.getElementById("policy-status").textContent = error.message;
  }
}

function requestReauthentication(message, confirmLabel) {
  return new Promise((resolve) => {
    const overlay = document.getElementById("reauth-overlay");
    const form = document.getElementById("reauth-form");
    const password = document.getElementById("reauth-password");
    const status = document.getElementById("reauth-status");
    document.getElementById("reauth-message").textContent = message;
    document.getElementById("reauth-confirm").textContent = confirmLabel;
    password.value = "";
    status.textContent = "";
    overlay.hidden = false;
    password.focus();
    const finish = (value) => {
      overlay.hidden = true;
      form.removeEventListener("submit", submit);
      document.getElementById("reauth-cancel").removeEventListener("click", cancel);
      resolve(value);
    };
    const submit = (event) => { event.preventDefault(); finish(password.value); };
    const cancel = () => finish(null);
    form.addEventListener("submit", submit);
    document.getElementById("reauth-cancel").addEventListener("click", cancel);
  });
}

async function destructiveProfileAction(profileId, action) {
  const profile = profiles.find((item) => item.id === profileId);
  const deleting = action === "delete";
  const password = await requestReauthentication(
    deleting
      ? `Permanently delete ${profile.display_name} and only that learner's local progress?`
      : `Reset all progress for ${profile.display_name}? The learner profile itself will remain.`,
    deleting ? "Delete learner" : "Reset progress"
  );
  if (!password) return;
  try {
    await apiRequest(`/api/instructor/profiles/${profileId}${deleting ? "" : "/reset"}`, {
      method: deleting ? "DELETE" : "POST",
      body: { password },
    });
    await refresh();
  } catch (error) {
    document.getElementById("policy-status").textContent = error.message;
  }
}

async function exportProfile(profileId) {
  const report = await apiRequest(`/api/instructor/profiles/${profileId}/export`);
  downloadJSON(report, `ot-range-${profileId}-report.json`);
}

async function viewProfileHistory(profileId) {
  const report = await apiRequest(`/api/instructor/profiles/${profileId}/export`);
  document.getElementById("history-title").textContent = `${report.profile.displayName} — attempt history`;
  const scenarios = report.scenarios.filter((scenario) => scenario.totalAttempts > 0);
  document.getElementById("history-body").innerHTML = scenarios.length
    ? scenarios.map((scenario) => `<section class="history-scenario">
        <h3>${escapeHtml(scenario.scenario)} — ${escapeHtml(scenario.scenarioTitle)}</h3>
        ${scenario.attemptHistory.map((attempt) => `<div class="profile-list-item">
          <span><b>Attempt ${attempt.attemptNumber}</b><small>${escapeHtml(attempt.mode || "Mode not selected")} · ${escapeHtml(attempt.status)} · score ${text(attempt.score)}/${text(attempt.maximumScore)}</small><small>${attempt.solutionRevealed ? "Solution viewed · " : ""}${attempt.resetAt ? `Reset by ${escapeHtml(attempt.resetActor)} at ${escapeHtml(attempt.resetAt)}` : attempt.current ? "Current attempt" : "Closed"}</small></span>
          <b>${attempt.practiceAfterSolutionReview ? "Practice after solution review" : attempt.hintsUsed ? "Hint assisted" : "Independent record"}</b>
        </div>`).join("")}
      </section>`).join("")
    : '<p class="status-empty">No attempts recorded.</p>';
  document.getElementById("history-overlay").hidden = false;
}

function wirePolicyForm() {
  document.getElementById("policy-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const body = Object.fromEntries([...form.querySelectorAll("input[type=checkbox]")].map((input) => [input.name, input.checked]));
    body.scenarioAvailability = Object.fromEntries([...document.querySelectorAll("[data-scenario-policy]")].map((input) => [input.dataset.scenarioPolicy, input.checked]));
    const status = document.getElementById("policy-status");
    try {
      policies = await apiRequest("/api/instructor/policies", { method: "PUT", body });
      renderPolicies();
      status.textContent = "Policies saved and enforced by the backend.";
    } catch (error) {
      status.textContent = error.message;
    }
  });
}

function wireSettings() {
  document.getElementById("password-change-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    const status = document.getElementById("settings-status");
    if (values.new_password !== values.confirm_password) {
      status.textContent = "The new passwords do not match.";
      return;
    }
    try {
      await apiRequest("/api/instructor/change-password", { method: "POST", body: values });
      window.location.assign("/instructor/login");
    } catch (error) { status.textContent = error.message; }
  });
  document.getElementById("session-invalidate-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      await apiRequest("/api/instructor/sessions/invalidate", { method: "POST", body: values });
      window.location.assign("/instructor/login");
    } catch (error) { document.getElementById("settings-status").textContent = error.message; }
  });
}

function wireNavigation() {
  const links = [...document.querySelectorAll(".sidebar-nav .nav-link")];
  links.forEach((link) => link.addEventListener("click", () => {
    links.forEach((item) => item.classList.toggle("active", item === link));
    document.querySelector(".sidebar").classList.remove("open");
  }));
  document.getElementById("mobile-menu-btn").addEventListener("click", () => document.querySelector(".sidebar").classList.toggle("open"));
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

document.getElementById("instructor-refresh").addEventListener("click", refresh);
document.getElementById("instructor-logout").addEventListener("click", async () => {
  await apiRequest("/api/instructor/logout", { method: "POST" });
  window.location.assign("/");
});
document.getElementById("export-all-records").addEventListener("click", async () => {
  downloadJSON(await apiRequest("/api/instructor/profiles/export-all"), "ot-range-all-learner-records.json");
});
wirePolicyForm();
wireSettings();
wireNavigation();
document.getElementById("history-close").addEventListener("click", () => {
  document.getElementById("history-overlay").hidden = true;
});
refresh();
