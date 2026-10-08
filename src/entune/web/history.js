// A bounded history page. Refreshes are conditional and never overlap; unchanged
// cards retain their player, retry selection and expanded attempts.
import { dayHeader, dispose } from "./history-card.js";
import { whenLabel } from "./ui.js";

export function createHistory({ list, newer, older, renderCard, onChange, onError }) {
  const size = 25;
  const cursors = [];
  let before = null;
  let etag = null;
  let flight = null;
  let lastId = null;
  let snapshots = new Map();
  let labelDay = new Date().toDateString();

  async function refresh(force = false) {
    const today = new Date().toDateString();
    if (labelDay !== today) {
      for (const time of list.querySelectorAll(".attempt time[datetime]")) time.textContent = whenLabel(time.dateTime);
      labelDay = today;
      group();
    }
    if (flight) {
      await flight;
      if (force) return refresh(true);
      return;
    }
    flight = read(force);
    try { await flight; } finally { flight = null; }
  }

  async function read(force) {
    const query = new URLSearchParams({ limit: String(size + 1) });
    if (before !== null) query.set("before", String(before));
    const res = await fetch(`/api/recordings?${query}`, {
      headers: !force && etag ? { "If-None-Match": etag } : {},
    });
    if (res.status === 304) return;
    if (!res.ok) throw new Error(await res.text());
    const rows = await res.json();
    etag = res.headers.get("etag");
    older.hidden = rows.length <= size;
    newer.hidden = before === null;
    const page = rows.slice(0, size);
    lastId = page.at(-1)?.id ?? null;
    for (const head of list.querySelectorAll(".day-head")) head.remove();
    const existing = new Map([...list.children].map((card) => [Number(card.dataset.id), card]));
    const next = new Map();
    let position = list.firstElementChild;
    for (const recording of page) {
      const snapshot = JSON.stringify(recording);
      let card = existing.get(recording.id);
      if (!card || snapshots.get(recording.id) !== snapshot) {
        const replacement = renderCard(recording);
        if (card) {
          dispose(card);
          if (position === card) position = replacement;
          card.replaceWith(replacement);
        }
        card = replacement;
      }
      if (card !== position) list.insertBefore(card, position);
      position = card.nextElementSibling;
      next.set(recording.id, snapshot);
    }
    for (const [id, card] of existing) {
      if (!next.has(id)) { dispose(card); card.remove(); }
    }
    snapshots = next;
    group();
    onChange(page);
  }

  // Cards under a header for each day, newest first, as the page lists them.
  function group() {
    for (const head of list.querySelectorAll(".day-head")) head.remove();
    const days = [];
    for (const card of list.querySelectorAll(".card")) {
      if (days.at(-1)?.[0].dataset.day !== card.dataset.day) days.push([]);
      days.at(-1).push(card);
    }
    days.forEach((cards, i) => list.insertBefore(dayHeader(cards, i === 0), cards[0]));
  }

  async function navigate(back) {
    newer.disabled = older.disabled = true;
    const previous = before;
    try {
      if (flight) await flight;
      before = back ? cursors.at(-1) ?? null : lastId;
      etag = null;
      await refresh(true);
      if (back) cursors.pop(); else cursors.push(previous);
      list.parentElement.scrollTop = 0;
    } catch (err) {
      before = previous;
      etag = null;
      onError(err);
    } finally { newer.disabled = older.disabled = false; }
  }
  newer.addEventListener("click", () => navigate(true));
  older.addEventListener("click", () => navigate(false));
  async function latest() {
    if (flight) await flight;
    before = null;
    cursors.length = 0;
    etag = null;
    await refresh(true);
  }
  // Every card drawn again, for a change outside the recordings (the models' names).
  async function redraw() {
    snapshots = new Map();
    etag = null;
    await refresh(true);
  }
  return { refresh, latest, redraw };
}
