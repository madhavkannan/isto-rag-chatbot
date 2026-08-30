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
    const rendered = renderVisual(visual, escalated);
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

function renderVisual(visual, escalated) {
  if (visual.type === "trip_attendance") return renderTripAttendanceVisual(visual, escalated);
  if (visual.type === "course_drop") return renderCourseDropVisual(visual);
  if (visual.type === "rcl_ticket") return renderRclTicketVisual(visual);
  if (visual.type === "course_list") return renderCourseListVisual(visual);
  return null;
}

function renderCourseListVisual(visual) {
  const container = document.createElement("div");
  container.className = "visual course-list-visual";

  const label = document.createElement("div");
  label.className = "visual-label";
  label.textContent = "Your courses";
  container.appendChild(label);

  const list = document.createElement("div");
  list.className = "course-mode-list";
  for (const course of visual.courses) {
    list.appendChild(makeCourseModeRow(course.name, course.delivery_mode));
  }
  container.appendChild(list);

  return container;
}

function renderCourseDropVisual(visual) {
  const container = document.createElement("div");
  container.className = "visual course-drop-visual";

  const label = document.createElement("div");
  label.className = "visual-label";
  label.textContent = "Course drop impact";
  container.appendChild(label);

  const subtitle = document.createElement("div");
  subtitle.className = "visual-value drop-subtitle";
  subtitle.textContent = `Dropping ${visual.courseName} (${visual.credits} credit${visual.credits === 1 ? "" : "s"})`;
  container.appendChild(subtitle);

  const meters = document.createElement("div");
  meters.className = "credit-meters";
  meters.appendChild(makeCreditMeter("Total credits", visual.total));
  meters.appendChild(makeCreditMeter("In-person credits", visual.inPerson));
  container.appendChild(meters);

  const caption = document.createElement("div");
  caption.className = "visual-caption" + (visual.compliant ? "" : " warn");
  caption.textContent = visual.compliant
    ? "Both stay at or above the minimum — this drop is fine."
    : "Falls below the minimum on at least one count — not compliant on its own.";
  container.appendChild(caption);

  if (!visual.compliant && visual.alternatives && visual.alternatives.length) {
    const list = document.createElement("div");
    list.className = "alt-list";
    const altLabel = document.createElement("div");
    altLabel.className = "alt-list-label";
    altLabel.textContent = "Suggested alternatives";
    list.appendChild(altLabel);
    for (const alt of visual.alternatives) {
      const row = document.createElement("div");
      row.className = "alt-row";
      const modeLabel = alt.delivery_mode === "in_person" ? "in-person" : alt.delivery_mode;
      row.textContent = `${alt.name} — ${modeLabel}, ${alt.credits}cr`;
      list.appendChild(row);
    }
    container.appendChild(list);
  }

  return container;
}

function makeCreditMeter(label, counts) {
  const wrap = document.createElement("div");
  wrap.className = "credit-meter";

  const row = document.createElement("div");
  row.className = "credit-meter-row";
  row.innerHTML = `<span class="credit-meter-label">${escapeHtml(label)}</span><span class="credit-meter-value">${counts.projected} / ${counts.min} min</span>`;
  wrap.appendChild(row);

  const trackWrap = document.createElement("div");
  trackWrap.className = "credit-track-wrap";

  const track = document.createElement("div");
  track.className = "meter-track credit-track" + (counts.meetsMinimum ? "" : " warn");
  const fillPct = counts.current > 0 ? Math.max(0, (counts.projected / counts.current) * 100) : 0;
  const fill = document.createElement("div");
  fill.className = "meter-fill" + (counts.meetsMinimum ? "" : " danger");
  fill.style.width = `${fillPct}%`;
  track.appendChild(fill);
  trackWrap.appendChild(track);

  const markerPct = counts.current > 0 ? Math.min(100, (counts.min / counts.current) * 100) : 0;
  const marker = document.createElement("div");
  marker.className = "credit-marker";
  marker.style.left = `calc(${markerPct}% - 1px)`;
  trackWrap.appendChild(marker);

  wrap.appendChild(trackWrap);
  return wrap;
}

function renderRclTicketVisual(visual) {
  const container = document.createElement("div");
  container.className = "visual rcl-ticket-visual";

  const label = document.createElement("div");
  label.className = "visual-label";
  label.textContent = "Advisor escalation filed";
  container.appendChild(label);

  const rows = document.createElement("div");
  rows.className = "ticket-rows";
  rows.innerHTML =
    `<div class="ticket-row"><span class="ticket-key">Course</span><span class="ticket-val">${escapeHtml(visual.course_name)}</span></div>` +
    `<div class="ticket-row"><span class="ticket-key">Routed to</span><span class="ticket-val">${escapeHtml(visual.routing_queue.replace(/_/g, " "))}</span></div>` +
    `<div class="ticket-row"><span class="ticket-key">Risk level</span><span class="ticket-badge">${escapeHtml(visual.risk_level.toLowerCase().replace(/_/g, " "))}</span></div>`;
  container.appendChild(rows);

  const summary = document.createElement("div");
  summary.className = "ticket-summary";
  summary.textContent = visual.context_summary;
  container.appendChild(summary);

  return container;
}

function makeCourseModeRow(name, deliveryMode) {
  const row = document.createElement("div");
  row.className = "course-mode-row";
  const badgeClass = deliveryMode === "in_person" ? "in-person" : deliveryMode === "hybrid" ? "hybrid" : "online";
  const badgeLabel = deliveryMode === "in_person" ? "In-person" : deliveryMode === "hybrid" ? "Hybrid" : "Online";
  row.innerHTML =
    `<span class="course-mode-name">${escapeHtml(name)}</span>` +
    `<span class="course-mode-badge ${badgeClass}">${badgeLabel}</span>`;
  return row;
}

function renderTripAttendanceVisual(visual, escalated) {
  const container = document.createElement("div");
  container.className = "visual trip-visual";

  const label = document.createElement("div");
  label.className = "visual-label";
  label.textContent = "Trip attendance check";
  container.appendChild(label);

  if (visual.courseModes && visual.courseModes.length) {
    const modeList = document.createElement("div");
    modeList.className = "course-mode-list";
    for (const course of visual.courseModes) {
      modeList.appendChild(makeCourseModeRow(course.name, course.deliveryMode));
    }
    container.appendChild(modeList);
  }

  const strip = document.createElement("div");
  strip.className = "calendar-strip";
  for (const day of visual.days) {
    strip.appendChild(makeDayChip(day));
  }
  container.appendChild(strip);

  const legend = document.createElement("div");
  legend.className = "legend";
  legend.innerHTML =
    '<span class="legend-item"><span class="legend-dot break-day"></span>Break</span>' +
    '<span class="legend-item"><span class="legend-dot safe"></span>Safe</span>' +
    '<span class="legend-item"><span class="legend-dot conflict"></span>Conflict</span>';
  container.appendChild(legend);

  const conflictDays = visual.days.filter((d) => d.status === "conflict");
  if (conflictDays.length) {
    const list = document.createElement("div");
    list.className = "conflict-list";
    for (const day of conflictDays) {
      const row = document.createElement("div");
      row.className = "conflict-row";
      row.innerHTML = `<span class="date">${formatDate(day.date)}</span><span class="what">${escapeHtml(day.conflicts.join(", "))}</span>`;
      list.appendChild(row);
    }
    container.appendChild(list);
  }

  const captionList = document.createElement("ul");
  captionList.className = "caption-list";
  for (const part of buildTripCaptionParts(visual)) {
    const li = document.createElement("li");
    li.className = "caption-item" + (part.warn ? " warn" : "");
    li.textContent = part.text;
    captionList.appendChild(li);
  }
  container.appendChild(captionList);

  if (visual.recommendedReturn) {
    const rec = document.createElement("div");
    rec.className = "recommendation";
    rec.innerHTML =
      '<div class="rec-label">Recommended compliant alternative</div>' +
      `<div class="rec-text">Keep your ${formatDate(visual.departure)} departure, but return by ` +
      `<strong>${formatDate(visual.recommendedReturn)}</strong> instead — every day through then is clear.</div>`;
    container.appendChild(rec);
  }

  const sig = visual.signature;
  const sigOk = sig.status === "ok";
  // Once a case is actually filed (escalated), the model's own reply
  // already explains the signature reason when that's part of why — the
  // "a new case is needed" framing here would just repeat it. Only show
  // this on an escalated message when the signature is fine, since that's
  // still useful context and isn't said anywhere else.
  if (!escalated || sigOk) {
    const doc = document.createElement("div");
    doc.className = "doc-status";
    const sigText = sigOk
      ? `Re-entry signature valid through ${formatDate(sig.expiry)}`
      : sig.status === "expired"
        ? `Re-entry signature expired ${formatDate(sig.expiry)} — a new case is needed either way`
        : `Re-entry signature expires ${formatDate(sig.expiry)} (within 30 days) — a new case is recommended`;
    doc.innerHTML = `<span class="doc-dot ${sigOk ? "ok" : "bad"}"></span>${escapeHtml(sigText)}`;
    container.appendChild(doc);
  }

  return container;
}

function buildTripCaptionParts(visual) {
  const parts = [];
  const breakDays = visual.days.filter((d) => d.status === "break");
  if (breakDays.length) {
    const label = breakDays[0].label;
    const first = formatDate(breakDays[0].date);
    const last = formatDate(breakDays[breakDays.length - 1].date);
    parts.push({ text: breakDays.length > 1 ? `${first}–${last} ${label}` : `${first} ${label}` });
  }
  for (const day of visual.days) {
    if (day.status === "safe" && day.label) {
      parts.push({ text: `${formatDate(day.date)} ${day.label}` });
    }
  }
  if (visual.compliant && visual.hardDeadline) {
    parts.push({ text: `Back by ${formatDate(visual.hardDeadline)} for your next required session` });
  } else if (!visual.compliant) {
    const n = visual.days.filter((d) => d.status === "conflict").length;
    parts.push({
      text: `${n} mandatory in-person session${n === 1 ? "" : "s"} conflict${n === 1 ? "s" : ""} with this trip`,
      warn: true,
    });
  }
  return parts;
}

function makeDayChip(day) {
  const chip = document.createElement("div");
  const d = new Date(`${day.date}T00:00:00Z`);
  const dow = d.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
  const dom = d.getUTCDate();
  let cls = "day-chip";
  if (day.status === "break") cls += " break-day";
  if (day.status === "conflict") cls += " conflict";
  chip.className = cls;
  chip.innerHTML = `<span class="dow">${dow}</span><span class="dom">${dom}</span>`;
  return chip;
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
