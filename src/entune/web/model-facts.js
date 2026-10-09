// What the Models page says about a speech model before anyone uses it: published accuracy
// and speed, price and free use. Providers change these often; CHECKED is when they were
// last checked against the sources below.
//
// Local models: word error rate on FLEURS English and speed in × realtime on a reference
// laptop (Ryzen 4750U, Vulkan), from the Handy models benchmark's model cards
// (huggingface.co/handy-computer). The compact turbo has no card of its own: its figures are
// estimates from the full model's. Cloud models: AA-WER and speed factor from Artificial
// Analysis' speech-to-text index (artificialanalysis.ai/speech-to-text; speed excludes
// upload), list price per hour of audio, and the free use each provider publishes.
//
// AssemblyAI's speed is Universal-3 Pro's: the index has no speed for 3.5 Pro yet.
//
// Keyed by `provider/model`. `dot` is the chart label, `place` moves it off a neighbour.
export const CHECKED = "2026-10-09";

export const FACTS = {
  "parakeet/parakeet-tdt-0.6b-v3": { name: "Parakeet TDT 0.6B v3", dot: "Parakeet v3", place: "below", sub: "English and 24 more · Apple Silicon", wer: 4.83, speed: 25.06, verdict: "Recommended" },
  "local/large-v3-turbo": { name: "Whisper large-v3-turbo", dot: "Whisper turbo", place: "above", sub: "All languages", wer: 4.38, speed: 3.09, verdict: "Most accurate" },
  "local/large-v3-turbo-q5_0": { name: "Whisper large-v3-turbo, compact", dot: "turbo compact", sub: "A third the size", wer: 4.5, estimated: true, speed: 3.8 },
  "local/small.en": { name: "Whisper small, English", dot: "Whisper small", sub: "English", wer: 6.14, speed: 12.47 },
  "local/base.en": { name: "Whisper base, English", dot: "Whisper base", sub: "English", wer: 7.6, speed: 32.02, verdict: "Fastest" },

  "assemblyai/universal-3-5-pro": { name: "AssemblyAI Universal", dot: "AssemblyAI", sub: "universal-3-5-pro", tag: "AssemblyAI", wer: 3.02, speed: 110, price: 0.21, verdict: "Recommended", free: "$50 credit", freeSub: "≈238 h, no expiry", link: "https://www.assemblyai.com/dashboard/api-keys" },
  "elevenlabs/scribe_v2": { name: "ElevenLabs Scribe v2", dot: "Scribe v2", place: "below", sub: "scribe_v2", tag: "ElevenLabs", wer: 2.18, speed: 73.7, price: 0.22, verdict: "Most accurate", free: "≈30 min", freeSub: "every month", link: "https://elevenlabs.io/app/developers/api-keys" },
  "xai/grok-voice-transcribe-2.0": { name: "Grok Voice Transcribe 2", dot: "Grok Voice 2", place: "above", sub: "grok-voice-transcribe-2.0", tag: "xAI", wer: 2.29, speed: 185.4, price: 0.1, verdict: "Fastest", free: null, freeSub: "none published", link: "https://console.x.ai/" },
  "soniox/stt-async-v5": { name: "Soniox v5", dot: "Soniox v5", sub: "stt-async-v5", tag: "Soniox", wer: 3.81, speed: 41.3, price: 0.1, free: null, freeSub: "pay as you go", link: "https://console.soniox.com/" },
  "groq/whisper-large-v3-turbo": { name: "Whisper large-v3-turbo", dot: "Groq turbo", sub: "via Groq", tag: "Groq", wer: 4.62, speed: 101.7, price: 0.04, verdict: "Most free use", free: "8 h a day", freeSub: "free plan", link: "https://console.groq.com/keys" },
};
