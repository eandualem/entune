// A contiguous span of items in order, chosen on a timeline: each item is one segment as
// wide as its weight, and two handles snap to whole items, cannot cross, and keep at least
// one item included. Dragging anywhere on the track moves the nearer handle; Arrow, Page
// and Home/End keys move a focused handle by whole items. Used for audio recordings and
// for transcripts to reuse.
export function createRange({ track, onChange, label = "items" }) {
  let edges = [0]; // where each boundary sits, 0..1
  let count = 0;
  let from = 0, to = 0; // included items: [from .. to - 1]
  let handles = true;
  let dragging = null;

  const layer = (cls) => Object.assign(document.createElement("div"), { className: cls });
  const base = layer("tl-bars");
  const chosen = layer("tl-bars tl-chosen");
  const handle = (name) => {
    const h = layer("tl-handle");
    h.tabIndex = 0;
    h.setAttribute("role", "slider");
    h.setAttribute("aria-label", `${name} included ${label}`);
    h.dataset.handle = name === "Oldest" ? "from" : "to";
    return h;
  };
  const start = handle("Oldest"), end = handle("Newest");
  track.replaceChildren(base, chosen, start, end);

  const fraction = (x) => {
    const box = track.getBoundingClientRect();
    return Math.min(1, Math.max(0, (x - box.left) / (box.width || 1)));
  };
  const nearest = (x) => {
    const f = fraction(x);
    return edges.reduce((best, edge, i) => Math.abs(edge - f) < Math.abs(edges[best] - f) ? i : best, 0);
  };
  function move(which, i) {
    if (which === "from") from = Math.max(0, Math.min(i, to - 1));
    else to = Math.min(count, Math.max(i, from + 1));
    onChange();
  }
  track.addEventListener("pointerdown", (event) => {
    if (!handles || !count || event.button !== 0) return;
    event.preventDefault();
    // The handle nearer on screen moves, then snaps to the nearest whole item.
    const f = fraction(event.clientX);
    const toFrom = Math.abs(f - edges[from]), toTo = Math.abs(f - edges[to]);
    dragging = event.target.dataset?.handle ?? (toFrom < toTo || (toFrom === toTo && f < edges[from]) ? "from" : "to");
    const i = nearest(event.clientX);
    (dragging === "from" ? start : end).focus({ focusVisible: false }); // arrow keys fine-tune the drag
    track.setPointerCapture(event.pointerId);
    move(dragging, i);
  });
  track.addEventListener("pointermove", (event) => { if (dragging) move(dragging, nearest(event.clientX)); });
  for (const type of ["pointerup", "pointercancel"]) track.addEventListener(type, () => { dragging = null; });
  for (const h of [start, end]) {
    h.addEventListener("keydown", (event) => {
      const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -10, PageUp: 10, Home: -Infinity, End: Infinity }[event.key];
      if (step === undefined) return;
      event.preventDefault();
      const by = Number.isFinite(step) ? step : step > 0 ? count : -count;
      const which = h.dataset.handle;
      move(which, (which === "from" ? from : to) + by);
    });
  }

  return {
    get from() { return from; },
    get to() { return to; },
    // New items: the span given, or all of them. `kinds` names each segment's look
    // ("used" or "new") where the timeline shows what a run reads rather than a span.
    setWeights(weights, span = [0, weights.length], kinds = null) {
      const total = weights.reduce((sum, weight) => sum + weight, 0) || 1;
      edges = [0];
      for (const weight of weights) edges.push(edges.at(-1) + weight / total);
      count = weights.length;
      [from, to] = span;
      for (const [box, look] of [[base, (i) => kinds?.[i] ?? ""], [chosen, () => ""]]) {
        box.replaceChildren(...weights.map((_, i) => {
          const segment = layer(`tl-seg ${look(i)}`.trim());
          segment.style.left = `${edges[i] * 100}%`;
          segment.style.width = `${(edges[i + 1] - edges[i]) * 100}%`;
          return segment;
        }));
      }
      // Gaps between segments only while each segment can still be seen.
      track.classList.toggle("dense", count * 3 > (track.clientWidth || 440));
    },
    // Where boundary `i` sits, 0..1.
    edge(i) { return edges[i] ?? (i <= 0 ? 0 : 1); },
    // With handles, the chosen span is lit; without, every segment keeps its own look.
    draw({ withHandles = true, describe = (i) => String(i) } = {}) {
      handles = withHandles;
      track.classList.toggle("fixed", !handles);
      start.hidden = end.hidden = chosen.hidden = !handles;
      chosen.style.clipPath = `inset(0 ${(1 - this.edge(to)) * 100}% 0 ${this.edge(from) * 100}%)`;
      for (const [h, at, value] of [[start, from, from], [end, to, to - 1]]) {
        h.style.left = `${this.edge(at) * 100}%`;
        h.setAttribute("aria-valuemin", "0");
        h.setAttribute("aria-valuemax", String(Math.max(0, count - 1)));
        h.setAttribute("aria-valuenow", String(value));
        h.setAttribute("aria-valuetext", describe(value));
      }
    },
  };
}
