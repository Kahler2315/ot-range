import { apiRequest } from "./api.js";

const heading = document.getElementById("auth-heading");
const description = document.getElementById("auth-description");
const form = document.getElementById("instructor-auth-form");
const confirmRow = document.getElementById("password-confirm-row");
const submit = document.getElementById("auth-submit");
const status = document.getElementById("auth-status");
let configured = false;

function showStatus(message, error = false) {
  status.textContent = message;
  status.className = error ? "form-status error" : "form-status";
}

async function initialize() {
  const state = await apiRequest("/api/instructor/status");
  if (state.authenticated) {
    window.location.assign("/instructor");
    return;
  }
  configured = state.configured;
  heading.textContent = configured ? "Instructor Console" : "Create instructor password";
  description.textContent = configured
    ? "Enter the instructor password for this local installation."
    : "No default password exists. Create the instructor password for this installation.";
  confirmRow.hidden = configured;
  submit.textContent = configured ? "Open Instructor Console" : "Create password";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = document.getElementById("instructor-password").value;
  if (!configured && password !== document.getElementById("instructor-password-confirm").value) {
    showStatus("The password confirmation does not match.", true);
    return;
  }
  try {
    showStatus(configured ? "Verifying…" : "Creating instructor password…");
    await apiRequest(configured ? "/api/instructor/login" : "/api/instructor/setup", {
      method: "POST",
      body: { password },
    });
    window.location.assign("/instructor");
  } catch (error) {
    showStatus(error.message, true);
  }
});

initialize().catch((error) => showStatus(error.message, true));
