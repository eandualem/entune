// History card rendering keeps each player, panel and retry menu attached to its card. A
// card is led by its waveform, which is also the player; the text gets the space; details
// show on hover, and failures stay in view.

import { ICON, THIS_DEVICE, errorText, modelName, whenLabel } from "./ui.js";

// The model that actually produced an attempt, kept as recorded at the time.
const attemptLabel = (t, models) => (t.provider === "none" ? "no model set" : modelName(`${t.provider}/${t.model}`, models));
const clock = (s) => (Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "–:––");
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const node = (tag, cls = "", text = "") => Object.assign(document.createElement(tag), { className: cls, textContent: text });
const LONG_WORDS = 150;

// ---- The waveform: one bar per stretch of audio, from the recording's own levels ----
// Bars are fetched once per recording and bar count, when the card scrolls into view.
const levels = new Map();
const queue = [];
let loading = 0;
function pump() {
  while (loading < 3 && queue.length) {
    const { key, url, done } = queue.shift();
    loading++;
    fetch(url).then((res) => (res.ok ? res.json() : null)).catch(() => null).then((body) => {
      loading--;
      if (body?.levels) levels.set(key, body.levels);
      done(levels.get(key));
      pump();
    });
  }
}
const seen = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    seen.unobserve(entry.target);
    entry.target._load();
  }
}, { rootMargin: "200px" });
const paint = (bars, values) => values?.forEach((v, i) => { if (bars[i]) bars[i].style.height = `${Math.round(12 + 88 * Math.pow(v, 0.7))}%`; });

function player(id, seconds, label) {
  const audio = node("audio");
  audio.preload = "none";
  audio.setAttribute("aria-label", label);
  const play = node("button", "play-btn");
  play.type = "button";
  play.title = "Play";
  play.setAttribute("aria-label", `Play ${label}`);
  play.innerHTML = ICON.play;
  // As wide as the recording is long, up to nine minutes; the row may narrow it further.
  const count = Math.max(24, Math.min(160, Math.round((seconds || 0) / 3.2)));
  const wave = node("div", "wave");
  wave.style.width = `${Math.round(110 + Math.min(seconds || 0, 540) * 1.9)}px`;
  wave.title = "Click to play from here";
  const bars = Array.from({ length: count }, () => wave.appendChild(node("span")));
  const key = `${id}:${count}`;
  if (levels.has(key)) paint(bars, levels.get(key));
  else {
    wave._load = () => { queue.push({ key, url: `/api/recordings/${id}/waveform?bars=${count}`, done: (v) => paint(bars, v) }); pump(); };
    seen.observe(wave);
  }
  const time = node("span", "clock", clock(seconds));
  let played = 0;
  function draw() {
    const total = Number.isFinite(audio.duration) ? audio.duration : seconds;
    const upTo = total ? Math.round((audio.currentTime / total) * count) : 0;
    for (let i = Math.min(played, upTo); i < Math.max(played, upTo); i++) bars[i]?.classList.toggle("played", i < upTo);
    played = upTo;
    const started = !audio.paused || audio.currentTime > 0;
    time.textContent = started ? `${clock(audio.currentTime)} / ${clock(total)}` : clock(total);
  }
  audio.addEventListener("timeupdate", draw);
  for (const event of ["loadedmetadata", "durationchange"]) audio.addEventListener(event, draw);
  audio.addEventListener("play", () => { play.innerHTML = ICON.pause; play.title = "Pause"; play.classList.add("on"); wave.closest(".card")?.classList.add("playing"); });
  audio.addEventListener("pause", () => { play.innerHTML = ICON.play; play.title = "Play"; play.classList.remove("on"); wave.closest(".card")?.classList.remove("playing"); });
  audio.addEventListener("ended", () => { audio.currentTime = 0; draw(); });
  async function start(fraction = null) {
    if (!audio.src) audio.src = `/api/recordings/${id}/audio`;
    try {
      if (fraction !== null) {
        if (audio.readyState < 1) {
          audio.preload = "metadata";
          await new Promise((resolve) => { audio.addEventListener("loadedmetadata", resolve, { once: true }); audio.load(); });
          if (audio.dataset.gone) return; // the card left the page meanwhile
        }
        audio.currentTime = fraction * audio.duration;
        await audio.play();
      } else if (audio.paused) await audio.play();
      else audio.pause();
    } catch (err) { play.title = `Playback failed: ${errorText(err)}`; }
  }
  play.addEventListener("click", () => start());
  wave.addEventListener("click", (e) => {
    const box = wave.getBoundingClientRect();
    start(Math.min(1, Math.max(0, (e.clientX - box.left) / box.width)));
  });
  return { audio, play, wave, time };
}

// ---- What happened after speech recognition: each stage, its summary and its edits ----
const DECISION_MODELS = { jev: "Jev", laya: "Laya", openai: "OpenAI", perplexity: "Perplexity" };
const took = (stage) => [DECISION_MODELS[stage.model] ?? "", stage.attempts > 1 ? `after ${plural(stage.attempts - 1, "retry", "retries")}` : "", stage.seconds ? `${stage.seconds.toFixed(1)} s` : ""].filter(Boolean).join(" · ");
function summary(name, stage) {
  if (stage.status === "failed") return `Failed, so its changes are not in the text: ${stage.error}`;
  if (stage.status === "skipped") return stage.error ? `Skipped: ${stage.error}` : name === "Dictionary" ? "No known confusions in this dictation" : "Nothing to change";
  if (stage.status === "pending") return "Not finished";
  const changes = stage.changes?.length ?? null;
  if (name === "Dictionary") {
    const parts = [stage.replacements ? plural(stage.replacements, "correction") : "No corrections needed"];
    if (stage.abstained) parts.push(`${plural(stage.abstained, "word")} left as heard (unclear meaning)`);
    return parts.join(" · ");
  }
  if (name === "Filler removal") return stage.removed_words ? `Removed ${plural(stage.removed_words, "filler word")}` : "No filler words removed";
  return changes === null ? "Applied (changes not recorded)" : changes ? plural(changes, "change") : "No changes";
}
// Line breaks and edge spaces are drawn, so paragraph and list edits can be seen.
const visible = (text) => text === "" ? "(nothing)" : text.replace(/\n/g, "↵").replace(/^ +| +$/g, (spaces) => "␣".repeat(spaces.length));
const stagesOf = (t) => [["Dictionary", t.correction], ["Filler removal", t.cleanup], ["Formatting", t.formatting]].filter(([, stage]) => stage && stage.status !== "disabled");

function changesPanel(t) {
  const panel = node("div", "card-panel changes-panel");
  for (const [name, stage] of stagesOf(t)) {
    const block = node("div", "stage");
    const head = node("div", "stage-head");
    head.append(node("b", "", name), node("span", stage.status === "failed" ? "stage-summary err" : "stage-summary", summary(name, stage)), node("span", "spacer"), node("span", "took", took(stage)));
    block.append(head);
    if (stage.changes?.length) {
      const list = node("div", "stage-changes");
      for (const change of stage.changes) {
        const chip = node("span", "change");
        chip.append(node("del", "", visible(change.before)), node("span", "to", "→"), node("ins", "", visible(change.after)));
        list.append(chip);
      }
      block.append(list);
    }
    panel.append(block);
  }
  if (t.correction?.status === "failed") {
    const recover = node("button", "btn fill sm safe-copy", "Apply safe mappings and copy");
    recover.type = "button";
    recover.dataset.attempt = t.id;
    recover.title = "Use only explicitly approved direct mappings. Ambiguous words stay original; history and already-pasted text are unchanged.";
    panel.append(recover);
  }
  return panel;
}

// Every stage edits the same raw text, so rebuilding the final text from their edits shows
// which words the dictionary changed. Anything that doesn't rebuild exactly stays unmarked.
function marked(t) {
  const edits = stagesOf(t).filter(([, s]) => s.status === "succeeded")
    .flatMap(([name, s]) => (s.changes ?? []).map((c) => ({ ...c, dictionary: name === "Dictionary" })))
    .sort((a, b) => a.start - b.start || a.end - b.end);
  if (t.raw_text == null || !edits.some((e) => e.dictionary)) return null;
  // The offsets count characters as Python does (code points), not as JavaScript strings do.
  const raw = Array.from(t.raw_text);
  const parts = [];
  let at = 0;
  for (const e of edits) {
    if (e.start < at) return null;
    parts.push(raw.slice(at, e.start).join(""), e.dictionary ? { text: e.after, heard: e.before } : e.after);
    at = e.end;
  }
  parts.push(raw.slice(at).join(""));
  return parts.map((p) => (typeof p === "string" ? p : p.text)).join("") === t.text ? parts : null;
}
function transcript(t) {
  const block = node("div", "transcript");
  const parts = marked(t);
  if (!parts) block.textContent = t.text;
  else for (const part of parts) {
    if (typeof part === "string") { block.append(part); continue; }
    const word = node("span", "fixed", part.text);
    // What was heard shows on hover, except where anonymous mode hides the text: a
    // tooltip is never blurred.
    word.addEventListener("mouseenter", () => {
      const hidden = document.documentElement.hasAttribute("data-anonymous") && !word.closest(".card")?.hasAttribute("data-reveal");
      word.title = hidden ? "" : `Heard “${part.heard}”`;
    });
    block.append(word);
  }
  block.title = "Click to copy";
  return block;
}

// ---- Try another model: a menu of the models, with what each has done for this recording ----
let openMenu = null;
function closeMenu() {
  if (!openMenu) return;
  openMenu.menu.remove();
  openMenu.button.setAttribute("aria-expanded", "false");
  openMenu.button.closest(".card")?.classList.remove("menu-open");
  openMenu = null;
}
document.addEventListener("pointerdown", (e) => { if (openMenu && !openMenu.menu.contains(e.target) && !openMenu.button.contains(e.target)) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && openMenu) { const { button } = openMenu; closeMenu(); button.focus(); } });
function retryMenu(button, r, models, isLocal) {
  if (openMenu?.button === button) { closeMenu(); return; }
  closeMenu();
  const [latest] = r.transcriptions;
  const tried = new Set(r.transcriptions.map((t) => `${t.provider}/${t.model}`));
  const menu = node("div", "retry-menu");
  menu.setAttribute("role", "menu");
  menu.append(node("p", "retry-menu-head", "Transcribe again with"));
  for (const m of models) {
    const current = latest && `${latest.provider}/${latest.model}` === m.id;
    const item = node("button", "retry-pick");
    item.type = "button";
    item.setAttribute("role", "menuitem");
    item.dataset.model = m.id;
    item.dataset.card = r.id; // the menu is gone by the time the page handles the pick
    item.dataset.label = modelName(m.id, models).split(" · ")[0];
    item.addEventListener("click", closeMenu); // the retry itself is the page's (app.js)
    item.append(node("span", "retry-name", m.label), node("span", current ? "retry-note now" : "retry-note",
      current ? "This text" : tried.has(m.id) ? "Tried" : isLocal(m.id) ? `On ${THIS_DEVICE}` : "Cloud"));
    menu.append(item);
  }
  if (!models.length) menu.append(node("p", "retry-menu-foot", "Add a speech model under Models first."));
  menu.append(node("p", "retry-menu-foot", "The current text stays as an earlier attempt."));
  button.closest(".card-foot").append(menu);
  button.setAttribute("aria-expanded", "true");
  button.closest(".card")?.classList.add("menu-open");
  openMenu = { button, menu };
  menu.querySelector("button")?.focus();
}

// `currentModels` reads the models when the retry menu opens, so a key added since the card
// was drawn shows its models there too.
export function renderCard(r, models, { isLocal = () => false, currentModels = () => models } = {}) {
  const card = node("article", "card");
  card.dataset.id = r.id;
  card.dataset.day = new Date(r.created_at).toDateString();
  const [latest, ...earlier] = r.transcriptions;
  const seconds = r.transcriptions.find((t) => Number.isFinite(t.audio_seconds))?.audio_seconds ?? null;
  card.dataset.seconds = seconds ?? 0;
  const pending = ["processing", "cancelled"].includes(latest?.processing_state);
  const failed = Boolean(latest && !pending && latest.status !== "ok");

  // The waveform leads: play, the wave itself, the clock, and the time of day.
  const top = node("div", "card-top");
  const when = node("time", "when", new Date(r.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  when.dateTime = r.created_at;
  when.title = new Date(r.created_at).toLocaleString();
  const { audio, play, wave, time } = player(r.id, seconds, `recording from ${when.title}`);
  top.append(play, wave, time, node("span", "spacer"), when, audio);
  card.append(top);

  if (r.notice) card.append(node("p", "card-note", r.notice));
  if (latest?.processing_state === "cancelled") card.append(node("p", "card-note", "Canceled — audio saved. No text delivered."));
  else if (latest?.processing_state === "processing") {
    const note = node("p", "card-note", "Processing… Final text will appear when complete.");
    note.setAttribute("role", "status");
    card.append(note);
  } else if (failed) {
    const box = node("div", "failed");
    box.append(node("div", "label", "Couldn't transcribe"), node("pre", "", latest.error ?? ""));
    card.append(box);
    card.dataset.copy = latest.error ?? "";
  } else if (latest && !latest.text) {
    card.append(node("div", "transcript empty", "No speech detected"));
    card.dataset.copy = "";
  } else if (latest) {
    // Long dictations fold after nine lines; copying always takes all of it.
    const words = (latest.text.match(/\S+/g) || []).length;
    const wrap = node("div", "transcript-wrap");
    const block = transcript(latest);
    wrap.append(block);
    card.append(wrap);
    card.dataset.copy = latest.text;
    if (words > LONG_WORDS) {
      const more = node("button", "more-link");
      more.type = "button";
      const fold = (folded) => {
        wrap.classList.toggle("folded", folded);
        more.textContent = folded ? `Show all · ${plural(words, "word")}` : "Show less";
        more.setAttribute("aria-expanded", String(!folded));
      };
      fold(true);
      more.addEventListener("click", () => fold(!wrap.classList.contains("folded")));
      card.append(more);
    }
  }
  const stages = latest && latest.status === "ok" && !pending ? stagesOf(latest) : [];
  const stageFailed = stages.some(([, stage]) => stage.status === "failed");
  const changed = stages.reduce((sum, [, stage]) => sum + (stage.status === "succeeded" ? stage.changes?.length ?? 0 : 0), 0);
  if (latest?.status === "ok" && latest.error) card.append(node("p", "card-note err", latest.error));

  // The footer: what needs attention and what just happened stay; the rest shows on hover.
  const foot = node("div", "card-foot");
  const panels = {};
  const pill = (text, cls, panel) => {
    const b = node("button", `meta-pill ${cls}`, text);
    b.type = "button";
    b.setAttribute("aria-expanded", "false");
    b.addEventListener("click", () => {
      const open = panels[panel].hidden;
      for (const [name, box] of Object.entries(panels)) {
        box.hidden = !(open && name === panel);
        for (const toggle of foot.querySelectorAll(`[data-panel="${name}"]`)) toggle.setAttribute("aria-expanded", String(!box.hidden));
      }
      card.classList.toggle("panel-open", open);
    });
    b.dataset.panel = panel;
    return b;
  };
  if (stageFailed) foot.append(pill("Needs attention", "alert", "changes"));
  const copied = node("span", "copied-tag", "Copied");
  const status = node("span", "status");
  status.setAttribute("role", "status");
  foot.append(copied, status);
  const meta = node("div", "card-meta");
  if (latest) {
    const model = node("span", "meta-pill quiet", attemptLabel(latest, models).split(" · ")[0]);
    model.title = `${attemptLabel(latest, models)}${latest.fast ? " · Fast mode" : ""} · ${isLocal(`${latest.provider}/${latest.model}`) ? `on ${THIS_DEVICE}` : "cloud"}`;
    meta.append(model);
  }
  // The stages' details, timings included, open from here even when nothing changed.
  if (stages.length && !stageFailed) meta.append(pill(changed ? plural(changed, "change") : "No changes", "", "changes"));
  if (earlier.length) meta.append(pill(plural(earlier.length, "earlier attempt"), "", "attempts"));
  meta.append(node("span", "spacer"));
  if (!pending && latest?.raw_text != null) {
    const raw = node("button", "btn ghost sm copy-raw", "Copy original");
    raw.type = "button";
    raw.title = "Copy the untouched speech-provider result";
    card.dataset.raw = latest.raw_text;
    meta.append(raw);
  }
  const again = node("button", "btn ghost sm retry-open");
  again.type = "button";
  again.innerHTML = ICON.retry;
  again.append(failed ? "Try again" : "Try another model");
  again.setAttribute("aria-haspopup", "menu");
  again.setAttribute("aria-expanded", "false");
  again.addEventListener("click", () => retryMenu(again, r, currentModels(), isLocal));
  const download = node("a", "btn-icon");
  download.href = `/api/recordings/${r.id}/audio`;
  download.download = "";
  download.title = "Download audio";
  download.setAttribute("aria-label", "Download audio");
  download.innerHTML = ICON.download;
  meta.append(again, download);
  foot.append(meta);
  card.append(foot);
  card.classList.toggle("failed-card", failed);

  if (stages.length) { panels.changes = changesPanel(latest); panels.changes.hidden = true; card.append(panels.changes); }
  if (earlier.length) {
    const box = node("div", "card-panel attempts-panel");
    box.hidden = true;
    for (const t of earlier) {
      const attempt = node("div", "attempt");
      const head = node("p", "meta", `${attemptLabel(t, models)} · `);
      const at = node("time", "", whenLabel(t.created_at));
      at.dateTime = t.created_at;
      head.append(at);
      const was = ["processing", "cancelled"].includes(t.processing_state);
      const text = node("p", t.status === "ok" || was ? "text" : "text err",
        was ? (t.processing_state === "cancelled" ? "Canceled — audio saved. No text delivered." : "Processing…")
          : t.status === "ok" ? t.text || "No speech detected" : t.error ?? "");
      attempt.append(head, text);
      // Its processing stays inspectable: what each stage changed or why it failed, and the time.
      const steps = t.status === "ok" && !was ? stagesOf(t) : [];
      if (t.status === "ok" && !was && t.error) attempt.append(node("p", "card-note err", t.error));
      if (steps.length) {
        const failedStep = steps.some(([, stage]) => stage.status === "failed");
        const count = steps.reduce((sum, [, stage]) => sum + (stage.status === "succeeded" ? stage.changes?.length ?? 0 : 0), 0);
        const details = node("details", "attempt-details");
        details.append(node("summary", failedStep ? "summary err" : "summary", failedStep ? "Needs attention" : count ? plural(count, "change") : "No changes"), changesPanel(t));
        attempt.append(details);
      }
      box.append(attempt);
    }
    panels.attempts = box;
    card.append(box);
  }
  return card;
}

// A card leaving the page stops playing and stops waiting to draw its waveform.
export function dispose(card) {
  const audio = card.querySelector("audio");
  if (audio) { audio.dataset.gone = "true"; audio.pause(); }
  const wave = card.querySelector(".wave");
  if (wave) seen.unobserve(wave);
}

// A day's header: its name, how many dictations and how long, and on the first, the drop hint.
export function dayHeader(cards, first) {
  const date = new Date(cards[0].dataset.day);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const label = date.toDateString() === today.toDateString() ? "Today" : date.toDateString() === yesterday.toDateString() ? "Yesterday"
    : date.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
  const seconds = cards.reduce((sum, card) => sum + Number(card.dataset.seconds || 0), 0);
  const head = node("div", "day-head");
  head.append(node("h2", "day-label", label),
    node("span", "day-summary", `${plural(cards.length, "dictation")} · ${seconds < 60 ? `${Math.round(seconds)} s` : `${Math.round(seconds / 60)} min`}`));
  if (first) head.append(node("span", "spacer"), node("span", "day-summary", "Drop an audio file anywhere to transcribe it"));
  return head;
}
