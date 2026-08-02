import { apiRequest } from "./api.js";

const profile = JSON.parse(document.getElementById("profile-data").textContent);
const status = document.getElementById("student-profile-status");

async function copyToolLogin(button) {
  const loginStatus = document.getElementById("tool-login-status");
  try {
    await navigator.clipboard.writeText(button.dataset.copyValue);
    loginStatus.textContent = `${button.getAttribute("aria-label").replace("Copy ", "")} copied.`;
    button.classList.add("copied");
    window.setTimeout(() => button.classList.remove("copied"), 1200);
  } catch {
    loginStatus.textContent = "Clipboard access was unavailable. Select the visible value and copy it manually.";
  }
}

document.querySelectorAll("[data-copy-value]").forEach((button) => {
  button.addEventListener("click", () => copyToolLogin(button));
});

function showStatus(message, error = false) {
  status.textContent = message;
  status.className = error ? "form-status error" : "form-status";
}

document.getElementById("student-profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await apiRequest(`/api/profiles/${profile.id}`, {
      method: "PATCH",
      body: Object.fromEntries(new FormData(event.currentTarget)),
    });
    showStatus("Local learner profile saved.");
    document.querySelector(".active-profile-chip b").textContent = data.profile.display_name;
  } catch (error) {
    showStatus(error.message, true);
  }
});

document.getElementById("student-reset-all").addEventListener("click", async () => {
  const button = document.getElementById("student-reset-all");
  if (button.dataset.armed !== "true") {
    button.dataset.armed = "true";
    button.textContent = "Click again to confirm reset";
    showStatus("This will clear every scenario attempt, score, hint, note, and opened-document record for this profile.");
    window.setTimeout(() => {
      button.dataset.armed = "false";
      button.textContent = "Reset all my progress";
    }, 8000);
    return;
  }
  try {
    await apiRequest(`/api/profiles/${profile.id}/reset-all`, { method: "POST" });
    window.location.reload();
  } catch (error) {
    showStatus(error.message, true);
  }
});

document.getElementById("student-delete-profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await apiRequest(`/api/profiles/${profile.id}`, {
      method: "DELETE",
      body: Object.fromEntries(new FormData(event.currentTarget)),
    });
    localStorage.removeItem("ot-range-last-profile-id");
    window.location.assign("/");
  } catch (error) {
    showStatus(error.message, true);
  }
});
