// Talks directly to Cognito's public InitiateAuth endpoint (no SDK needed)
// and then to the deployed chat API with the resulting ID token. Meant to be
// opened as a local file or served with any static file server for a
// recorded demo — no build step, no hosting infrastructure.

const cfg = window.ISTO_DEMO_CONFIG;
let idToken = null;
let currentUser = null;
let history = [];

// Demo-only credentials (CloudFormation TestUserAPassword/TestUserBPassword
// defaults) — pre-filled purely so clicking through the demo doesn't need
// retyping them each time. Not a real secret: this whole stack is a
// throwaway fictional demo, and these are already documented in the
// project's own README.
const DEMO_PASSWORDS = {
  usera: "MeridianDemo!2026A",
  userb: "MeridianDemo!2026B",
};

const DISPLAY_NAMES = {
  usera: "User A",
  userb: "User B",
};

const el = (id) => document.getElementById(id);

el("login-btn").addEventListener("click", onLogin);
el("logout-btn").addEventListener("click", onLogout);
el("composer").addEventListener("submit", onSend);
el("user-select").addEventListener("change", syncPasswordField);
document.querySelectorAll(".starter-chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    el("message-input").value = btn.dataset.prompt;
    el("composer").requestSubmit();
  });
});

syncPasswordField();

function syncPasswordField() {
  el("password-input").value = DEMO_PASSWORDS[el("user-select").value] || "";
}

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
    el("session-label").innerHTML = `Signed in as <strong>${DISPLAY_NAMES[username] || username}</strong>`;
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
  syncPasswordField();
  el("chat-panel").classList.add("hidden");
  el("login-panel").classList.remove("hidden");
}

async function onSend(e) {
  e.preventDefault();
  clearError();
  const input = el("message-input");
  const sendBtn = el("composer").querySelector('button[type="submit"]');
  const message = input.value.trim();
  if (!message) return;

  el("starter-prompts").classList.add("hidden");
  appendMessage("user", message, false);
  input.value = "";
  input.disabled = true;
  sendBtn.disabled = true;
  const typingEl = appendTypingIndicator();

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
    typingEl.remove();
    if (!res.ok) {
      throw new Error(payload.error || "Request failed");
    }

    history.push({ role: "user", content: [{ text: message }] });
    history.push({ role: "assistant", content: [{ text: payload.reply }] });
    appendMessage("assistant", payload.reply, payload.escalated, payload.visual);
  } catch (err) {
    typingEl.remove();
    showError(err.message);
  } finally {
    input.disabled = false;
    sendBtn.disabled = false;
    input.focus();
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
  body.className = "msg-body";
  body.appendChild(renderFormattedText(text));
  div.appendChild(body);

  if (visual) {
    const rendered = renderVisual(visual);
    if (rendered) div.appendChild(rendered);
  }

  el("chat-window").appendChild(div);
  el("chat-window").scrollTop = el("chat-window").scrollHeight;
}

// --- Lightweight markdown (bold + bullet lists only) -------------------
// A small custom parser rather than a library: the model is asked to
// prefer short bullet points over long paragraphs (see prompts.py), and
// this is just enough to render that — and any **bold** — as real HTML
// instead of literal asterisks and dashes.

function renderFormattedText(text) {
  const container = document.createElement("div");
  const lines = text.split("\n");
  let paragraphLines = [];
  let currentList = null;

  const flushParagraph = () => {
    if (paragraphLines.length) {
      const p = document.createElement("p");
      p.innerHTML = renderInline(paragraphLines.join(" "));
      container.appendChild(p);
      paragraphLines = [];
    }
  };
  const flushList = () => {
    if (currentList) {
      container.appendChild(currentList);
      currentList = null;
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (line === "") {
      flushParagraph();
      flushList();
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.*)/);
    if (bullet) {
      flushParagraph();
      if (!currentList) currentList = document.createElement("ul");
      const li = document.createElement("li");
      li.innerHTML = renderInline(bullet[1]);
      currentList.appendChild(li);
    } else {
      flushList();
      paragraphLines.push(line);
    }
  }
  flushParagraph();
  flushList();

  return container;
}

function renderInline(str) {
  return escapeHtml(str).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function appendTypingIndicator() {
  const div = document.createElement("div");
  div.className = "msg assistant typing";

  const header = document.createElement("div");
  header.className = "msg-header";
  header.innerHTML = `<span class="avatar">I</span>ISTO Assistant`;
  div.appendChild(header);

  const dots = document.createElement("div");
  dots.className = "typing-dots";
  dots.innerHTML = "<span></span><span></span><span></span>";
  div.appendChild(dots);

  el("chat-window").appendChild(div);
  el("chat-window").scrollTop = el("chat-window").scrollHeight;
  return div;
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
  const cap = visual.cap;
  const total = visual.total;
  const overBy = visual.overBy || 0;
  const atCapOrOver = visual.remaining <= 0;

  // Segment widths as a share of the cap. If the total exceeds the cap,
  // scale both segments down proportionally so they still sum to exactly
  // 100% of the bar (their ratio to each other is preserved either way) —
  // the actual numbers in the header and caption carry the overage, not
  // the bar spilling past its own edge.
  const scale = total > cap && total > 0 ? cap / total : 1;
  const coursePct = cap > 0 ? Math.max(0, ((visual.courseHours * scale) / cap) * 100) : 0;
  const workPct = cap > 0 ? Math.max(0, ((visual.workHours * scale) / cap) * 100) : 0;

  const container = document.createElement("div");
  container.className = "visual meter-visual";

  const row = document.createElement("div");
  row.className = "visual-row";
  row.innerHTML = `<span class="visual-label">Weekly hours</span><span class="visual-value">${total} / ${cap} hrs</span>`;
  container.appendChild(row);

  const track = document.createElement("div");
  track.className = "meter-track" + (atCapOrOver ? " warn" : "");
  const courseFill = document.createElement("div");
  courseFill.className = "meter-fill course";
  courseFill.style.width = `${coursePct}%`;
  const workFill = document.createElement("div");
  workFill.className = "meter-fill work";
  workFill.style.width = `${workPct}%`;
  track.appendChild(courseFill);
  track.appendChild(workFill);
  container.appendChild(track);

  const breakdown = document.createElement("div");
  breakdown.className = "hours-breakdown";
  for (const course of visual.courses || []) {
    breakdown.appendChild(makeHoursRow("course", course.name, course.hours_this_week));
  }
  breakdown.appendChild(makeHoursRow("work", "Worked this week", visual.workHours));
  container.appendChild(breakdown);

  const caption = document.createElement("div");
  caption.className = "visual-caption" + (atCapOrOver ? " warn" : "");
  if (overBy > 0) {
    caption.textContent = `${overBy} hour${overBy === 1 ? "" : "s"} over your ${cap}-hour weekly cap`;
  } else if (atCapOrOver) {
    caption.textContent = "At your weekly limit — resets next week";
  } else {
    caption.textContent = `${visual.remaining} hour${visual.remaining === 1 ? "" : "s"} remaining this week`;
  }
  container.appendChild(caption);

  return container;
}

function makeHoursRow(kind, label, hours) {
  const row = document.createElement("div");
  row.className = "hours-row";
  row.innerHTML = `<span class="hours-dot ${kind}"></span><span class="hours-row-label">${escapeHtml(label)}</span><span class="hours-row-value">${hours} hr${hours === 1 ? "" : "s"}</span>`;
  return row;
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
