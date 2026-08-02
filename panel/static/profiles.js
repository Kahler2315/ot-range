import { apiRequest } from "./api.js";

const LAST_PROFILE_KEY = "ot-range-last-profile-id";
const list = document.getElementById("profile-list");
const empty = document.getElementById("profile-empty");
const form = document.getElementById("profile-create-form");
const status = document.getElementById("chooser-status");

function showStatus(message, error = false) {
  status.textContent = message;
  status.className = error ? "form-status error" : "form-status";
}

async function selectProfile(profileId) {
  try {
    await apiRequest(`/api/profiles/${profileId}/select`, { method: "POST" });
    localStorage.setItem(LAST_PROFILE_KEY, profileId);
    window.location.assign("/student");
  } catch (error) {
    showStatus(error.message, true);
  }
}

function renderProfiles(profiles) {
  list.innerHTML = "";
  empty.hidden = profiles.length > 0;
  const recent = localStorage.getItem(LAST_PROFILE_KEY);
  const ordered = [...profiles].sort((a, b) => (a.id === recent ? -1 : b.id === recent ? 1 : 0));
  for (const profile of ordered) {
    const card = document.createElement("article");
    card.className = "profile-card";
    const context = [profile.course, profile.section, profile.organization].filter(Boolean).join(" · ");
    card.innerHTML = `
      <div>
        <h3></h3>
        <p class="profile-context"></p>
        <p class="local-profile-label">Local learner profile</p>
      </div>
      <button class="btn-primary">${profile.id === recent ? "Continue" : "Open Student Lab"}</button>
    `;
    card.querySelector("h3").textContent = profile.display_name;
    card.querySelector(".profile-context").textContent = context || "No course details added";
    card.querySelector("button").addEventListener("click", () => selectProfile(profile.id));
    list.appendChild(card);
  }
}

async function loadProfiles() {
  try {
    const data = await apiRequest("/api/profiles");
    renderProfiles(data.profiles);
  } catch (error) {
    showStatus(error.message, true);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(form).entries());
  try {
    showStatus("Creating local learner profile…");
    const data = await apiRequest("/api/profiles", { method: "POST", body });
    localStorage.setItem(LAST_PROFILE_KEY, data.profile.id);
    window.location.assign("/student");
  } catch (error) {
    showStatus(error.message, true);
  }
});

loadProfiles();
