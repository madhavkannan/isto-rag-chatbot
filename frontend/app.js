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
document.querySelectorAll(".starter-chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    el("message-input").value = btn.dataset.prompt;
    el("composer").requestSubmit();
  });
});

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
    el("starter-prompts").classList.remove("hidden");
    el("session-label").innerHTML = `Signed in as <strong>${username}</strong>`;
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

  el("starter-prompts").classList.add("hidden");
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
    appendMessage("assistant", payload.reply, payload.escalated, payload.visual);
  } catch (err) {
    showError(err.message);
  }
}

function appendMessage(role, text, escalated, visual) {
  const div = document.createElement("div");
  div.className = `msg ${role}${escalated ? " escalated" : ""}`;

  const isUser = role === "user";
  const initial = isUser ? (currentUser || "?").slice(-1).toUpperCase() : "I";
  const name = isUser ? "You" : escalated ? "ISTO Assistant · Escalated" : "ISTO Assistant";

  const header = document.createElement("div");
  header.className = "msg-header";
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.textContent = initial;
  const label = document.createElement("span");
  label.textContent = name;
  header.appendChild(avatar);
  header.appendChild(label);
  div.appendChild(header);

  const body = document.createElement("div");
  body.textContent = text;
  div.appendChild(body);

  if (visual) {
    const rendered = renderVisual(visual);
    if (rendered) div.appendChild(rendered);
  }

  el("chat-window").appendChild(div);
  el("chat-window").scrollTop = el("chat-window").scrollHeight;
}

// --- Structured visuals -----------------------------------------------
// The backend attaches real numbers behind whichever tool it called this
// turn (see lambda/orchestrator/app.py's `visual` field) so these render
// actual data rather than parsing it back out of the model's prose.

function renderVisual(visual) {
  if (visual.type === "work_hours") return renderHoursVisual(visual);
  if (visual.type === "travel_coverage") return renderTravelVisual(visual);
  return null;
}

function renderHoursVisual(visual) {
  const atCap = visual.remaining <= 0;
  const pct = visual.cap > 0 ? Math.max(0, Math.min(100, (visual.logged / visual.cap) * 100)) : 0;

  const container = document.createElement("div");
  container.className = "visual meter-visual";

  const row = document.createElement("div");
  row.className = "visual-row";
  row.innerHTML = `<span class="visual-label">Weekly work hours</span><span class="visual-value">${visual.logged} / ${visual.cap} hrs</span>`;
  container.appendChild(row);

  const track = document.createElement("div");
  track.className = "meter-track" + (atCap ? " warn" : "");
  const fill = document.createElement("div");
  fill.className = "meter-fill" + (atCap ? " warn" : "");
  fill.style.width = `${pct}%`;
  track.appendChild(fill);
  container.appendChild(track);

  const caption = document.createElement("div");
  caption.className = "visual-caption" + (atCap ? " warn" : "");
  caption.textContent = atCap
    ? "At your weekly limit — resets next week"
    : `${visual.remaining} hour${visual.remaining === 1 ? "" : "s"} remaining this week`;
  container.appendChild(caption);

  return container;
}

function renderTravelVisual(visual) {
  const departure = new Date(`${visual.departure}T00:00:00Z`);
  const returnDate = new Date(`${visual.return}T00:00:00Z`);
  const expiry = new Date(`${visual.expiry}T00:00:00Z`);
  // "today" comes from the server (the same source of truth the escalation
  // decision uses), not the viewer's clock, so wording stays consistent
  // regardless of who's looking at it or when.
  const today = visual.today ? new Date(`${visual.today}T00:00:00Z`) : new Date();
  const expiryIsPast = expiry < today;
  const expiryVerb = expiryIsPast ? "expired" : "expires";
  const expiryWhat = expiryIsPast ? "Expired" : "Expires";

  const totalMs = returnDate - departure;
  let splitPct;
  if (totalMs <= 0) {
    splitPct = expiry >= departure ? 100 : 0;
  } else {
    splitPct = Math.max(0, Math.min(100, ((expiry - departure) / totalMs) * 100));
  }
  const fullyCovered = splitPct >= 100;
  const fullyUncovered = splitPct <= 0;

  const container = document.createElement("div");
  container.className = "visual timeline-visual";

  const label = document.createElement("div");
  label.className = "visual-label";
  label.textContent = "Trip coverage";
  container.appendChild(label);

  const track = document.createElement("div");
  track.className = "timeline-track";
  if (fullyCovered) {
    track.appendChild(makeTimelineSeg("safe", 0, 100));
  } else if (fullyUncovered) {
    track.appendChild(makeTimelineSeg("gap", 0, 100));
  } else {
    track.appendChild(makeTimelineSeg("safe", 0, splitPct));
    track.appendChild(makeTimelineSeg("gap", splitPct, 100 - splitPct));
    const tick = document.createElement("div");
    tick.className = "timeline-tick";
    tick.style.left = `${splitPct}%`;
    track.appendChild(tick);
  }
  container.appendChild(track);

  const ticks = document.createElement("div");
  ticks.className = "timeline-ticks";
  ticks.appendChild(makeTickmark("start", 0, formatDate(visual.departure), "Depart"));
  if (!fullyCovered && !fullyUncovered) {
    const labelPct = Math.max(14, Math.min(86, splitPct));
    ticks.appendChild(makeTickmark("mid warn", labelPct, formatDate(visual.expiry), expiryWhat));
  }
  ticks.appendChild(makeTickmark("end", 100, formatDate(visual.return), "Return"));
  container.appendChild(ticks);

  const caption = document.createElement("div");
  caption.className = "visual-caption" + (fullyCovered ? "" : " warn");
  if (fullyCovered) {
    caption.textContent = `Endorsement valid through ${formatDate(visual.expiry)} — covers the whole trip`;
  } else if (fullyUncovered) {
    caption.textContent = `Endorsement ${expiryVerb} ${formatDate(visual.expiry)} — not valid for any of this trip`;
  } else {
    caption.textContent = `Covered through ${formatDate(visual.expiry)}, then a gap until your return`;
  }
  container.appendChild(caption);

  return container;
}

function makeTimelineSeg(kind, leftPct, widthPct) {
  const seg = document.createElement("div");
  seg.className = `timeline-seg ${kind}`;
  seg.style.left = `${leftPct}%`;
  seg.style.width = `${widthPct}%`;
  return seg;
}

function makeTickmark(kind, pct, dateText, whatText) {
  const mark = document.createElement("div");
  mark.className = `timeline-tickmark ${kind}`;
  mark.style.left = `${pct}%`;
  mark.innerHTML = `<span class="date">${dateText}</span><span class="what">${whatText}</span>`;
  return mark;
}

function formatDate(iso) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

function showError(message) {
  const banner = el("error-banner");
  banner.textContent = message;
  banner.classList.remove("hidden");
}

function clearError() {
  el("error-banner").classList.add("hidden");
}
