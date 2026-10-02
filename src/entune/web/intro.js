// The first launch opens through a short introduction: Entune's mark appears and
// listens, its name and what it is for follow, and the scene dissolves into Get
// started. It is how the app opens the first time, not a demo, so it has no skip;
// about six seconds, once. The page's head script holds the app back
// (`intro-pending`) until this decides, so nothing flashes before it.

const SEEN = "entune-intro";
const LINES = ["Your speech model.", "Your vocabulary.", "Corrections that consider the context."];
const LEAVE_AT = 4900; // ms: when the scene starts to dissolve
const LEAVE_FOR = 900;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// `firstLaunch`: nothing set up and nothing recorded. Anyone else just sees the app.
export async function openApp(firstLaunch) {
  const root = document.documentElement;
  if (!root.classList.contains("intro-pending")) return;
  try { localStorage.setItem(SEEN, "1"); } catch (e) {}
  if (!firstLaunch) { root.classList.remove("intro-pending"); return; }

  const scene = document.createElement("div");
  scene.className = "intro";
  scene.setAttribute("aria-hidden", "true");
  const mark = document.createElement("div");
  mark.className = "intro-mark";
  try {
    // The app's own icon, inline, so its five level bars can move.
    mark.innerHTML = await (await fetch("/static/favicon.svg")).text();
  } catch (e) { /* the name and lines still introduce it */ }
  const name = Object.assign(document.createElement("div"), { className: "intro-name", textContent: "Entune" });
  const lines = document.createElement("div");
  lines.className = "intro-lines";
  for (const text of LINES) lines.append(Object.assign(document.createElement("p"), { textContent: text }));
  scene.append(mark, name, lines);
  document.body.append(scene);

  await wait(LEAVE_AT);
  root.classList.add("intro-leaving");
  root.classList.remove("intro-pending");
  await wait(LEAVE_FOR);
  scene.remove();
  root.classList.remove("intro-leaving");
}
