// The first launch opens through a short introduction: Entune's mark appears in a soft
// light with a quiet chime and listens, its name and what it is for follow, then the
// four steps that come next, and the scene dissolves into Get started. It is how the
// app opens the first time, not a demo, so it has no skip; about eight seconds, once.
// The page's head script holds the app back (`intro-pending`) until this decides, so
// nothing flashes before it.

const SEEN = "entune-intro";
const LINES = ["Your speech model.", "Your vocabulary.", "Corrections that consider the context."];
const STEPS = ["Choose your model", "Allow permissions", "Set your shortcut", "Start dictating"];
const STEPS_AT = 4300; // ms: the lines give way to the steps
const FIRST_STEP = 450;
const STEP_EVERY = 380;
const LEAVE_AT = 7500; // ms: the scene starts to dissolve
const LEAVE_FOR = 1100;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const element = (tag, className, text = "") => Object.assign(document.createElement(tag), { className, textContent: text });

// A warm chord that blooms and fades, a bell above it, and a soft tick for each step.
// Synthesized here, so there is no sound file; it stays quiet.
function chime() {
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context) return () => {};
  const ctx = new Context();
  const out = ctx.createGain();
  out.gain.value = 0.16;
  const echo = ctx.createDelay(1);
  echo.delayTime.value = 0.23;
  const feedback = ctx.createGain();
  feedback.gain.value = 0.28;
  const tone = ctx.createBiquadFilter();
  tone.type = "lowpass";
  tone.frequency.setValueAtTime(700, ctx.currentTime);
  tone.frequency.exponentialRampToValueAtTime(3800, ctx.currentTime + 1.2);
  tone.connect(out);
  tone.connect(echo);
  echo.connect(feedback);
  feedback.connect(echo);
  echo.connect(out);
  out.connect(ctx.destination);

  const note = (frequency, start, length, level, attack = 0.02, type = "sine") => {
    for (const detune of [-5, 5]) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = type;
      osc.frequency.value = frequency;
      osc.detune.value = detune;
      const t = ctx.currentTime + start;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(level, t + attack);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + length);
      osc.connect(gain);
      gain.connect(tone);
      osc.start(t);
      osc.stop(t + length + 0.05);
    }
  };
  // D, A and F#, then E and A an octave or two above: open and bright, settling softly.
  note(146.83, 0.05, 2.6, 0.22, 0.35, "triangle");
  note(220.0, 0.12, 2.4, 0.16, 0.3, "triangle");
  note(369.99, 0.2, 2.2, 0.1, 0.25);
  note(1318.5, 0.55, 1.6, 0.05, 0.01);
  note(1760.0, 0.75, 1.3, 0.03, 0.01);
  setTimeout(() => ctx.close().catch(() => {}), LEAVE_AT + LEAVE_FOR);
  return (index) => note(880 * 2 ** ([0, 2, 4, 7][index] / 12), 0, 0.5, 0.025, 0.005);
}

// `firstLaunch`: nothing set up and nothing recorded. Anyone else just sees the app.
export async function openApp(firstLaunch) {
  const root = document.documentElement;
  if (!root.classList.contains("intro-pending")) return;
  try { localStorage.setItem(SEEN, "1"); } catch (e) {}
  if (!firstLaunch) { root.classList.remove("intro-pending"); return; }

  const scene = element("div", "intro");
  scene.setAttribute("aria-hidden", "true");
  const mark = element("div", "intro-mark");
  const lines = element("div", "intro-lines");
  for (const text of LINES) lines.append(element("p", "", text));
  const steps = element("ol", "intro-steps");
  STEPS.forEach((text, index) => {
    const step = element("li", "");
    step.append(element("span", "n", String(index + 1)), element("span", "label", text));
    steps.append(step);
  });
  const stage = element("div", "intro-stage");
  stage.append(lines, steps);
  scene.append(element("div", "intro-light"), mark, element("div", "intro-name", "Entune"), stage);
  document.body.append(scene);
  // Nothing under the scene can be clicked or reached with Tab until it is revealed.
  const app = document.querySelectorAll("body > header, body > main");
  for (const part of app) part.inert = true;
  // The app's own icon, inline, so its five level bars can move. The scene is already
  // up, so a slow answer only delays the mark, never the decision above.
  fetch("/static/favicon.svg").then((res) => res.text()).then((svg) => { mark.innerHTML = svg; }).catch(() => {});
  let tick = () => {};
  try { tick = chime(); } catch (e) { /* silent where audio is unavailable */ }

  await wait(STEPS_AT);
  scene.dataset.phase = "steps";
  for (const [index, step] of [...steps.children].entries()) {
    await wait(index ? STEP_EVERY : FIRST_STEP);
    step.classList.add("on");
    tick(index);
  }
  await wait(LEAVE_AT - STEPS_AT - FIRST_STEP - STEP_EVERY * (STEPS.length - 1));
  for (const part of app) part.inert = false;
  root.classList.add("intro-leaving");
  root.classList.remove("intro-pending");
  await wait(LEAVE_FOR);
  scene.remove();
  root.classList.remove("intro-leaving");
}
