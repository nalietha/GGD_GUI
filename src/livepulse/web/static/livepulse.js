// LivePulse pages: writes through /api/v1, live updates over SSE.
//
// Deliberately small and dependency-free — the Pi never needs a Node
// toolchain. Markup opts in with data attributes:
//
//   [data-patch="/api/v1/…"][name]  PATCH {name: value} on change (or click, for buttons)
//   form[data-post="/api/v1/…"]     POST the form's fields as JSON
//   [data-delete="/api/v1/…"]       DELETE, after data-confirm if present
//   [data-live][id]                 replaced from a fresh render when something changes
//
// Rendering stays on the server: a refresh re-fetches this page and swaps the
// live regions, so there is one set of templates, not a second copy in here.
"use strict";

const LIVE_EVENTS = [
  "ChannelStateChanged",
  "ChannelConfigChanged",
  "NodeConfigChanged",
  "DisplayModeChanged",
  "SettingChanged",
  "ProviderHealthChanged",
];

function notice(message) {
  const box = document.getElementById("notice");
  if (!box) return;
  box.textContent = message || "";
  box.hidden = !message;
}

async function send(method, url, body) {
  const response = await fetch(url, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (Array.isArray(payload.detail)) detail = payload.detail.map((d) => d.msg).join("; ");
    } catch (_) {
      // Not JSON; the status line will do.
    }
    throw new Error(detail);
  }
  notice("");
  await refresh({ force: true });
}

function valueOf(el) {
  if (el.type === "checkbox") return el.checked;
  if (el.dataset.type === "int") return parseInt(el.value, 10);
  if (el.value === "" && "nullable" in el.dataset) return null;
  return el.value;
}

// ------------------------------------------------------------------------- //
// Refreshing live regions
// ------------------------------------------------------------------------- //

let pending = false;

function focusedLiveControl() {
  const active = document.activeElement;
  return active && active.closest("[data-live]") && active.matches("input, select, textarea")
    ? active
    : null;
}

// Swapping a region out from under an open colour picker or a half-dragged
// slider would be maddening, so a refresh the user did not cause waits until
// they leave the control. One they did cause (their own write) goes straight
// through and puts focus back where it was.
async function refresh({ force = false } = {}) {
  const focused = focusedLiveControl();
  if (focused && !force) {
    pending = true;
    return;
  }
  pending = false;

  const response = await fetch(location.href, { headers: { Accept: "text/html" } });
  if (!response.ok) return;
  const fresh = new DOMParser().parseFromString(await response.text(), "text/html");

  for (const region of document.querySelectorAll("[data-live][id]")) {
    const replacement = fresh.getElementById(region.id);
    if (replacement) region.replaceWith(replacement);
  }

  if (focused && focused.dataset.patch) {
    const selector = `[data-patch="${focused.dataset.patch}"][name="${focused.name}"]`;
    const again = document.querySelector(selector);
    if (again) again.focus();
  }
}

document.addEventListener("focusout", () => {
  if (!pending) return;
  // Wait for focus to land, so tabbing between two live controls still waits.
  setTimeout(() => {
    if (pending && !focusedLiveControl()) refresh();
  }, 0);
});

// ------------------------------------------------------------------------- //
// Writes
// ------------------------------------------------------------------------- //

function patch(el) {
  return send("PATCH", el.dataset.patch, { [el.name]: valueOf(el) }).catch((err) =>
    notice(err.message),
  );
}

document.addEventListener("change", (event) => {
  const el = event.target.closest("input[data-patch], select[data-patch]");
  if (el) patch(el);
});

// A slider shows its value as it moves, but writes only once it settles.
document.addEventListener("input", (event) => {
  const el = event.target;
  if (el.type !== "range") return;
  const output = el.closest("label")?.querySelector("output");
  if (output) output.textContent = el.value;
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-patch]");
  if (button) {
    send("PATCH", button.dataset.patch, { [button.name]: button.value }).catch((err) =>
      notice(err.message),
    );
    return;
  }

  const remove = event.target.closest("[data-delete]");
  if (remove) {
    if (remove.dataset.confirm && !confirm(remove.dataset.confirm)) return;
    send("DELETE", remove.dataset.delete).catch((err) => notice(err.message));
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-post]");
  if (!form) return;
  event.preventDefault();

  // Empty fields are omitted, so the API applies its own defaults.
  const body = {};
  for (const [key, value] of new FormData(form)) {
    if (value !== "") body[key] = value;
  }

  const submit = form.querySelector("[type=submit]");
  if (submit) submit.disabled = true;
  try {
    await send("POST", form.dataset.post, body);
    form.reset();
  } catch (err) {
    notice(err.message);
  } finally {
    if (submit) submit.disabled = false;
  }
});

// ------------------------------------------------------------------------- //
// Live feed
// ------------------------------------------------------------------------- //

function connect() {
  const indicator = document.getElementById("link-state");
  const source = new EventSource("/api/v1/events");

  // Changes often arrive in bursts (one edit can announce a node and a
  // channel); coalesce them into one re-render. The delay also lets the
  // coordinator rebuild its snapshot before the dashboard asks for it.
  let timer = null;
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(() => refresh(), 150);
  };
  for (const name of LIVE_EVENTS) source.addEventListener(name, schedule);

  source.onopen = () => {
    if (indicator) indicator.dataset.state = "up";
    // Anything could have changed while we were disconnected.
    schedule();
  };
  // EventSource reconnects by itself; just say so meanwhile.
  source.onerror = () => {
    if (indicator) indicator.dataset.state = "down";
  };
}

if (document.querySelector("[data-live]")) connect();
