// A contiguous span of items in order, chosen with two handles on one range (0..1000).
// Each item takes a stretch as wide as its weight; the handles snap to whole items, cannot
// cross, and keep at least one item included. Arrow, Page and Home/End keys move a handle
// by whole items. Used for audio recordings and for transcripts to reuse.
export function createRange({ start, end, fill, onChange }) {
  let edges = [0]; // cumulative position of each boundary, 0..1000
  let count = 0;
  let from = 0, to = 0; // included items: [from .. to - 1]

  const nearest = (value) => edges.reduce((best, edge, i) => Math.abs(edge - value) < Math.abs(edges[best] - value) ? i : best, 0);

  start.addEventListener("input", () => { from = Math.min(nearest(+start.value), to - 1); onChange(); });
  end.addEventListener("input", () => { to = Math.max(nearest(+end.value), from + 1); onChange(); });
  for (const [handle, isStart] of [[start, true], [end, false]]) {
    handle.addEventListener("keydown", (event) => {
      const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -10, PageUp: 10, Home: -Infinity, End: Infinity }[event.key];
      if (step === undefined) return;
      event.preventDefault();
      const by = Number.isFinite(step) ? step : step > 0 ? count : -count;
      if (isStart) from = Math.max(0, Math.min(to - 1, from + by));
      else to = Math.min(count, Math.max(from + 1, to + by));
      onChange();
    });
  }

  return {
    get from() { return from; },
    get to() { return to; },
    // New items select them all.
    setWeights(weights) {
      const total = weights.reduce((sum, weight) => sum + weight, 0) || 1;
      edges = [0];
      for (const weight of weights) edges.push(edges.at(-1) + (weight / total) * 1000);
      count = weights.length;
      from = 0; to = count;
    },
    // Where boundary `i` sits, 0..1000.
    edge(i) { return edges[i] ?? (i <= 0 ? 0 : 1000); },
    draw() {
      start.value = String(Math.round(this.edge(from)));
      end.value = String(Math.round(this.edge(to)));
      fill.style.left = `${this.edge(from) / 10}%`;
      fill.style.right = `${100 - this.edge(to) / 10}%`;
    },
  };
}
