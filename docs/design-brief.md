# Design brief: a lighter, calmer Dictum

A prompt for a design-focused model. Elias, 2026-09-18: "it doesn't feel like
a polished application". Loose on how, exact on what the system is and does.
Hand it over with a screenshot of the current window.

---

You are redesigning the window of **Dictum**, a personal dictation
workbench for macOS. One person opens it all day. They press a key, speak,
release, and the transcript is typed into whatever app they were in and
copied to the clipboard. The window is where they see what was recorded,
fix what the speech model got wrong, and set things up. It is open source,
runs entirely on the person's machine, has no accounts and no cloud of its
own: the person brings their own API keys, or downloads a local model.

## What I want from you

**Usability is the primary goal.** Then: light, calm, elegant, something a
person enjoys looking at and never has to think about. Not popping, not
loud, no big colour blocks: the kind of good looks that come from tone and
spacing, from two close shades meeting, from soft depth rather than hard
outlines. Very simple. Very light.

You have complete freedom to reorganise, regroup, rename, merge or split
views, move controls, change the navigation, and change how anything is
presented. Present things in logical groups a person can infer without
reading. Prefer a hint on hover, or a quiet caption, over an instruction
on the page. Every feature below must still exist and be reachable; how it
looks and where it lives is yours.

Deliver whatever shows the design best: annotated mockups, a description
of the structure and the reasoning behind the grouping, and ideally a
single HTML/CSS prototype of the main view. The current window is in the
attached image; treat it as the inventory, not the direction.

## Constraints

- It is one web page in a native macOS window (WebKit, resizable to any
  size, often narrow) and also opens in a normal browser. Plain HTML, CSS
  and JavaScript, no framework, no build step, system font stack, inline
  SVG for icons. Light and dark, following the system or a setting. The
  whole window scales with a text size setting; content fills the window's
  width.
- The person dictates for a living. Text they will copy must be easy to
  read and easy to select. Failures must show the provider's error
  verbatim; nothing is hidden or summarised.
- A menu-bar app runs beside the window (tray icon, quit). While recording
  a small pill floats on screen ("Recording", then "Transcribing…"), which
  the person can drag anywhere; that pill is native, not part of the page,
  but its look may follow yours.

## Everything the window does today

**Toolbar.** Record/Stop button with a running timer (records in the
window, for testing a model). The default model picker: one list of every
model the person has a key for or has downloaded; picking one applies at
once and is what the shortcut uses. Beside it, a chart button that opens a
small table of performance by model (runs, minutes of audio, median wait,
speed as seconds of audio per second waited, plain versus fast mode). A
status line for what is happening now. Three views: History, Dictionary,
Settings.

**History.** Every recording, newest first, kept forever: when it was
made, which model transcribed it, an audio player and a download, and the
transcript, which copies on click. A failed transcription shows the
provider's error in the transcript's place. Any recording can be
transcribed again with any other model; earlier attempts stay, collapsed
under the latest. The list refreshes itself as shortcut dictations arrive.
When there is nothing yet, a three-step getting started list (add a
provider, pick the model, dictate) that ticks itself off.

**Dictionary.** An entry is a term as the person spells it, what it means
to them, and the phrases speech models write instead; every heard phrase
is fixed in every transcript, and with Jev on each match is decided in
context from the description. Two sections: *Pinned by you*, entered
by hand or pinned from a proposal, shared by every model, never changed by
a machine; and *Learned for <the current model>*, what a language model
proposed from that model's own transcripts and the person accepted. A
Build (or Refine) from history button sends that model's recent
transcripts, with the pinned list as approved context, to a language model
of the person's choice and shows a proposal as added and removed entries
with Accept and Discard; nothing is saved before Accept. Each learned entry
has Pin and Remove; Pin all. There is no limit on the list's size. One add
row: what was heard, what was meant, and an optional description. An
"Edit as JSON" editor for the whole file. Agents the person dictates to can
post confirmed corrections through a local API; they land in Pinned.

**Settings.** *Providers*: API keys for AssemblyAI, Groq and Soniox
(masked once saved; a Save keys button, since a half-typed key must not
be sent), and local models: a list of whisper.cpp models with size and
state and a Download or Remove button with progress, and NVIDIA Parakeet
through an engine the person installs once. *Dictation*: the default model
(the same setting as the toolbar); fast mode, an opt-in upload while
recording, offered only for the provider that supports it; two shortcuts,
hold-to-talk (one key, records while held) and hands-free (a combination,
press to start and again to stop), each set by pressing the keys, with
Clear. *Dictionary model*: Anthropic and OpenAI keys and a model field
with suggestions, used only for the Build button. *Jev*: a TypeSafe key
and two switches, contextual dictionary and formatting, each saying what
it adds in time, and a line summing up what Jev has done. *Appearance*: theme
(system, light, dark) and text size (four steps, also cmd+ cmd− cmd0).
Everything applies as soon as it is changed, except keys.

## Words that matter

Provider (a company or engine), model (one of its speech models), default
model (the one dictation uses), transcript, raw text (what the model
returned before the dictionary), pinned, learned, entry (spelling,
description, heard), proposal, Jev, fast mode, hold-to-talk, hands-free.
