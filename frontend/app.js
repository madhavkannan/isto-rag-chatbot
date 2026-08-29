// Talks directly to Cognito's public InitiateAuth endpoint (no SDK needed)
// and then to the deployed chat API with the resulting ID token. Meant to be
// opened as a local file or served with any static file server for a
// recorded demo — no build step, no hosting infrastructure.

const cfg = window.ISTO_DEMO_CONFIG;
let idToken = null;
let currentUser = null;
let history = [];

const el = (id) => document.getElementById(id);

el("login-btn").addEventListener("click", onLogin);
el("logout-btn").addEventListener("click", onLogout);
el("composer").addEventListener("submit", onSend);

async function onLogin() {
  clearError();
  const username = el("user-select").value;
  const password = el("password-input").value;
  if (!password) {
    showError("Enter the password for this test user.");
    return;
  }

  el("login-btn").disabled = true;
  try {
    const res = await fetch(`https://cognito-idp.${cfg.region}.amazonaws.com/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
      },
      body: JSON.stringify({
        AuthFlow: "USER_PASSWORD_AUTH",
        ClientId: cfg.userPoolClientId,
        AuthParameters: { USERNAME: username, PASSWORD: password },
      }),
    });
    const payload = await res.json();
    if (!res.ok) {
      throw new Error(payload.message || payload.__type || "Sign-in failed");
    }

    idToken = payload.AuthenticationResult.IdToken;
    currentUser = username;
    history = [];
    el("chat-window").innerHTML = "";
    el("session-label").textContent = `Signed in as ${username}`;
    el("login-panel").classList.add("hidden");
    el("chat-panel").classList.remove("hidden");
  } catch (err) {
    showError(err.message);
  } finally {
    el("login-btn").disabled = false;
  }
}

function onLogout() {
  idToken = null;
  currentUser = null;
  history = [];
  el("password-input").value = "";
  el("chat-panel").classList.add("hidden");
  el("login-panel").classList.remove("hidden");
}

async function onSend(e) {
  e.preventDefault();
  clearError();
  const input = el("message-input");
  const message = input.value.trim();
  if (!message) return;

  appendMessage("user", message, false);
  input.value = "";

  try {
    const res = await fetch(`${cfg.apiUrl}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${idToken}`,
      },
      body: JSON.stringify({ message, conversation_history: history }),
    });
    const payload = await res.json();
    if (!res.ok) {
      throw new Error(payload.error || "Request failed");
    }

    history.push({ role: "user", content: [{ text: message }] });
    history.push({ role: "assistant", content: [{ text: payload.reply }] });
    appendMessage("assistant", payload.reply, payload.escalated);
  } catch (err) {
    showError(err.message);
  }
}

function appendMessage(role, text, escalated) {
  const div = document.createElement("div");
  div.className = `msg ${role}${escalated ? " escalated" : ""}`;
  if (escalated) {
    const tag = document.createElement("div");
    tag.className = "escalation-tag";
    tag.textContent = "Escalated to ISTO";
    div.appendChild(tag);
  }
  const body = document.createElement("div");
  body.textContent = text;
  div.appendChild(body);
  el("chat-window").appendChild(div);
  el("chat-window").scrollTop = el("chat-window").scrollHeight;
}

function showError(message) {
  const banner = el("error-banner");
  banner.textContent = message;
  banner.classList.remove("hidden");
}

function clearError() {
  el("error-banner").classList.add("hidden");
}
