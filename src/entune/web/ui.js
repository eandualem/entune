// Shared DOM helpers used by the window views. No application state lives here.

export const el = (id) => document.getElementById(id);
// What the page calls the computer it runs on: written for a Mac, "this computer" elsewhere.
export const THIS_DEVICE = /Macintosh/.test(navigator.userAgent) ? "this Mac" : "this computer";

export const ICON = {
  check: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>',
  play: '<svg class="i12" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 2l7 4-7 4z" fill="currentColor"/></svg>',
  pause: '<svg class="i12" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 2h2.5v8H3zM6.5 2H9v8H6.5z" fill="currentColor"/></svg>',
  download: '<svg class="i14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2v8M4.5 6.5L8 10l3.5-3.5M3 13h10"/></svg>',
  retry: '<svg class="i14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.6-3.7M13 3v3h-3"/></svg>',
  chevron: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" style="width:0.625rem;height:0.625rem"><path d="M4 6l4 4 4-4"/></svg>',
};

export async function api(path, init) {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export function flash(target, message, kind) {
  target.textContent = message;
  target.classList.add("save-status", "show");
  target.classList.toggle("ok", kind === "ok");
  target.classList.toggle("err", kind === "err");
  clearTimeout(target._timer);
  target._timer = setTimeout(() => target.classList.remove("show"), kind === "ok" ? 1800 : 8000);
}

export const errorText = (err) => String(err?.message ?? err);

export function segmentedGroup(buttons, onPick) {
  for (const [name, button] of Object.entries(buttons)) {
    button.addEventListener("click", () => { select(name); onPick(name); });
  }
  function select(name) {
    for (const [key, button] of Object.entries(buttons)) button.setAttribute("aria-selected", String(key === name));
  }
  return select;
}
export function whenLabel(iso) {
  const d = new Date(iso);
  const now = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(d, now)) return `Today ${time}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, yesterday)) return `Yesterday ${time}`;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}
// "Parakeet · tdt-0.6b-v3" rather than "Parakeet / parakeet-tdt-0.6b-v3".
function shortModel(name, provider, model) {
  return `${name} · ${model.replace(new RegExp(`^${provider}[-_]`, "i"), "")}`;
}
// A saved `provider/model` id in short form, named from the known models when possible.
export function modelName(id, models) {
  const [provider, ...rest] = id.split("/");
  const label = models.find((m) => m.id === id)?.label;
  const name = label ? label.split(" / ")[0] : provider;
  return shortModel(name, provider, rest.join("/"));
}

export function fillModels(models, select, selected, emptyLabel) {
  select.replaceChildren();
  if (models.length === 0) {
    select.append(new Option(emptyLabel, "", true, true));
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const m of models) select.append(new Option(m.label, m.id, false, m.id === selected));
}

// A table cell: the figure, and an optional line of context beneath it.
export function figure(main, sub = "", subClass = "") {
  const cell = Object.assign(document.createElement("span"), { className: "num" });
  cell.append(Object.assign(document.createElement("span"), { className: "cell-main", textContent: main }));
  if (sub) cell.append(Object.assign(document.createElement("span"), { className: `cell-sub ${subClass}`.trim(), textContent: sub }));
  return cell;
}
