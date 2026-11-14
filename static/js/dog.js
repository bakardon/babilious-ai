// Babilious' expression controller. CSS does the drawing; this only picks a state.

export const STATES = ["idle", "listening", "thinking", "speaking", "happy", "excited",
  "curious", "confused", "annoyed", "sleeping", "error"];

const LABELS = {
  idle: "Idle", listening: "Listening...", thinking: "Thinking...", speaking: "Speaking...",
  happy: "Pleased with himself", excited: "Excited", curious: "Curious", confused: "Confused",
  annoyed: "Unimpressed", sleeping: "Asleep. Say something to wake him.", error: "Something broke",
};

const SLEEP_AFTER_MS = 3 * 60 * 1000;

export class Dog {
  constructor(svg, portrait, statusEl) {
    this.svg = svg;
    this.portrait = portrait;
    this.statusEl = statusEl;
    this.state = "idle";
    this.revertTimer = null;
    this.sleepTimer = null;
    this.touch();
  }

  // Set a state. Transient moods fall back to idle after `holdMs`.
  set(state, { holdMs = 0, label } = {}) {
    if (!STATES.includes(state)) state = "idle";
    clearTimeout(this.revertTimer);
    this.state = state;
    this.svg.dataset.state = state;
    this.portrait.classList.toggle("listening", state === "listening");
    this.portrait.classList.toggle("speaking", state === "speaking");
    this.portrait.classList.toggle("error", state === "error");
    this.statusEl.textContent = label || LABELS[state];
    if (holdMs) this.revertTimer = setTimeout(() => this.set("idle"), holdMs);
    if (state !== "sleeping") this.touch();
  }

  // Any interaction resets the nap timer.
  touch() {
    clearTimeout(this.sleepTimer);
    this.sleepTimer = setTimeout(() => {
      if (this.state === "idle") this.set("sleeping");
    }, SLEEP_AFTER_MS);
    if (this.state === "sleeping") this.set("idle");
  }
}
