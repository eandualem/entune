import { PART_CHARS, day, duration } from "./audio-onboarding.js";
import { createRange } from "./range.js";
import { api, el, errorText } from "./ui.js";

const plural = (n, one) => `${n} ${n === 1 ? one : `${one}s`}`;

// Reuse earlier transcripts: every transcript of the speech model on a timeline, oldest on
// the left, with the ones suggestions you applied have read marked under the line. The same
// range as audio, so a span can be read again instead of everything.
export function createHistoryReuse({ getSettings, getRunSettings }) {
  const reuse = el("learning-reuse");
  let items = [];   // oldest first: id, created_at, characters, used
  let timing = null;
  let loads = 0;      // only the latest load's answer is shown
  let ready = false;  // the list shown belongs to the current speech model
  const range = createRange({
    start: el("history-range-start"), end: el("history-range-end"), fill: el("history-range-fill"), onChange: () => draw(),
  });

  // Used transcripts as dark stretches of a thin line under the range.
  function shade() {
    const stops = [];
    items.forEach((item, i) => {
      const color = item.used ? "var(--text-muted)" : "transparent";
      stops.push(`${color} ${range.edge(i) / 10}%`, `${color} ${range.edge(i + 1) / 10}%`);
    });
    el("history-used-marks").style.background = stops.length ? `linear-gradient(to right, ${stops.join(", ")})` : "none";
  }

  function state() {
    const used = items.filter((item) => item.used).length;
    if (!items.length) return "No transcripts from this speech model yet.";
    if (!used) return `Suggestions have not read any of your ${plural(items.length, "transcript")} yet.`;
    const fresh = items.length - used;
    return `Suggestions you applied have read ${used} of your ${plural(items.length, "transcript")}; ${fresh ? `${fresh} ${fresh === 1 ? "is" : "are"} new` : "none is new"}.`;
  }

  let problem = ""; // why the latest load failed
  function draw() {
    const said = problem || (ready ? state() : "Loading your transcripts…");
    el("history-state").textContent = said;
    el("guide-reuse-state").textContent = said;
    const open = ready && reuse.checked && items.length > 0;
    el("history-pick").hidden = !open;
    if (!open) return;
    range.draw();
    const chosen = items.slice(range.from, range.to);
    const characters = chosen.reduce((sum, item) => sum + item.characters, 0);
    const used = chosen.filter((item) => item.used).length;
    el("history-selected").textContent = `${plural(chosen.length, "transcript")} selected`;
    el("history-available").textContent = `${used} used before · ${characters.toLocaleString()} characters`;
    el("history-start-label").textContent = day(chosen[0].created_at);
    el("history-end-label").textContent = day(chosen.at(-1).created_at);
    const parts = Math.max(1, Math.ceil(characters / PART_CHARS));
    const measured = timing?.suggestion?.[`${getSettings()?.dictionaryModel}|${getRunSettings().effort}`];
    el("history-estimate").textContent = `Suggestions: about ${plural(parts, "part")}${measured
      ? `, about ${duration(measured.secondsPerPart)} each, about ${duration(parts * measured.secondsPerPart)} in all`
      : "; not timed yet with these settings"}`;
  }

  async function load() {
    const mine = ++loads;
    ready = false;
    problem = "";
    draw(); // the previous model's timeline goes while this one loads
    let listed, measured;
    try {
      [listed, measured] = await Promise.all([api("/api/dictionary/history"), api("/api/dictionary/timing").catch(() => null)]);
    } catch (err) {
      if (mine === loads) { problem = `Could not load your transcripts: ${errorText(err)}`; draw(); }
      throw err;
    }
    if (mine !== loads) return; // a newer load, for another speech model, is under way
    ({ items } = listed);
    timing = measured;
    ready = true;
    range.setWeights(items.map((item) => Math.max(item.characters, 1)));
    shade();
    draw();
  }

  return {
    load,
    redraw: draw,
    // What Get suggestions reads: new transcripts, or the span chosen on the timeline;
    // null while the timeline is still loading.
    selection() {
      if (!reuse.checked) return { scope: "new" };
      if (!ready) return null;
      return { scope: "all", transcript_ids: items.slice(range.from, range.to).map((item) => item.id) };
    },
  };
}
