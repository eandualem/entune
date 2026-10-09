import { PART_CHARS, day } from "./audio-onboarding.js";
import { createRange } from "./range.js";
import { api, el, errorText, segmentedGroup } from "./ui.js";

const plural = (n, one) => `${n.toLocaleString()} ${n === 1 ? one : `${one}s`}`;
// Get suggestions reads at most this many of the newest new transcripts (the server's limit).
const NEW_LIMIT = 300;

// What Get suggestions reads, on a timeline of every transcript of the speech model, oldest
// on the left. New since last time: the transcripts suggestions you applied haven't read,
// beside the ones they have. Choose a span: any stretch, read again or not, with the read
// ones marked under the line.
export function createHistoryReuse({ getSettings, getRunSettings, onChange }) {
  let span = false;   // Advanced › Read: false for new since last time, true for a chosen span
  let items = [];     // oldest first: id, created_at, characters, used
  let timing = null;
  let loads = 0;      // only the latest load's answer is shown
  let ready = false;  // the list shown belongs to the current speech model
  const range = createRange({ track: el("history-range"), onChange: () => draw(), label: "transcript" });
  const selectRead = segmentedGroup({ new: el("read-new"), span: el("read-span") }, (name) => {
    span = name === "span";
    for (const b of [el("read-new"), el("read-span")]) b.setAttribute("aria-checked", b.getAttribute("aria-selected"));
    draw();
  });
  selectRead("new");

  // Used transcripts as dark stretches of a thin line under the timeline.
  function shade() {
    const stops = [];
    items.forEach((item, i) => {
      const color = item.used ? "var(--text-dim)" : "transparent";
      stops.push(`${color} ${range.edge(i) * 100}%`, `${color} ${range.edge(i + 1) * 100}%`);
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

  // The transcripts a run would read now, oldest first.
  function chosen() {
    if (span) return items.slice(range.from, range.to);
    return items.filter((item) => !item.used).slice(-NEW_LIMIT);
  }

  let problem = ""; // why the latest load failed
  function draw() {
    const said = problem || (ready ? state() : "Loading your transcripts…");
    el("guide-reuse-state").textContent = said;
    // The timeline says it all once loaded; the line stays for loading, errors and no transcripts.
    const open = ready && items.length > 0;
    el("history-state").textContent = open ? "" : said;
    el("history-state").hidden = open;
    el("history-pick").hidden = !open;
    if (open) {
      range.draw({ withHandles: span, describe: (i) => day(items[i]?.created_at) });
      const reading = chosen();
      const used = items.filter((item) => item.used).length;
      if (span) {
        const characters = reading.reduce((sum, item) => sum + item.characters, 0);
        el("history-selected").textContent = `${plural(reading.length, "transcript")} selected`;
        el("history-available").textContent = `${reading.filter((item) => item.used).length} read before · ${characters.toLocaleString()} characters`;
      } else {
        const fresh = items.length - used;
        el("history-selected").textContent = plural(fresh, "new transcript");
        el("history-available").textContent = `since your last suggestions · ${used} read before${fresh > NEW_LIMIT ? ` · reads the newest ${NEW_LIMIT}` : ""}`;
      }
      const ends = span ? reading : items;
      el("history-start-label").textContent = ends.length ? day(ends[0].created_at) : "";
      el("history-end-label").textContent = ends.length ? day(ends.at(-1).created_at) : "";
      el("history-used-marks").hidden = !span;
      el("history-key-used").classList.toggle("line", span);
      el("history-key-chosen").textContent = span ? "Selected" : "New: what this run reads";
    }
    onChange();
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
    range.setWeights(items.map((item) => Math.max(item.characters, 1)), undefined, items.map((item) => item.used ? "used" : "new"));
    shade();
    draw();
  }

  return {
    load,
    redraw: draw,
    get span() { return span; },
    // What Get suggestions reads: new transcripts, or the span chosen on the timeline;
    // null while the timeline is still loading.
    selection() {
      if (!span) return { scope: "new" };
      if (!ready) return null;
      return { scope: "all", transcript_ids: items.slice(range.from, range.to).map((item) => item.id) };
    },
    // How many transcripts and parts a run reads, for What happens; null until loaded.
    plan() {
      if (!ready) return null;
      const reading = chosen();
      const characters = reading.reduce((sum, item) => sum + item.characters, 0);
      const measured = timing?.suggestion?.[`${getSettings()?.dictionaryModel}|${getRunSettings().effort}`];
      return { count: reading.length, parts: Math.max(1, Math.ceil(characters / PART_CHARS)), perPart: measured?.secondsPerPart ?? null, fresh: !span };
    },
  };
}
