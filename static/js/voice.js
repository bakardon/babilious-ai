// Voice in the browser: mic capture → localhost WebSocket → faster-whisper,
// and Piper replies played sentence-by-sentence with lip-sync and barge-in.
// Audio only leaves the page to reach this PC's own server (127.0.0.1).

import { api } from "./api.js";

const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ---------------------------------------------------------------- mic capture worklet
// Runs on the audio thread: downsample to 16 kHz mono int16, emit 20 ms frames + level.
const WORKLET = `
class Capture extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / 16000; this.pos = 0; this.out = new Int16Array(320); this.n = 0; this.acc = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const s = Math.max(-1, Math.min(1, ch[i]));
        this.out[this.n++] = s * 32767;
        this.acc += s * s;
        if (this.n === 320) {
          const rms = Math.sqrt(this.acc / 320);
          this.port.postMessage({ pcm: this.out.buffer, db: 20 * Math.log10(Math.max(rms, 1e-6)) }, [this.out.buffer]);
          this.out = new Int16Array(320); this.n = 0; this.acc = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("capture-16k", Capture);`;

class Mic {
  constructor() { this.stream = null; this.ctx = null; this.onFrame = null; }
  get open() { return !!this.stream; }
  async start(deviceId) {
    if (this.stream) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        channelCount: 1,
        echoCancellation: true,   // so Babilious doesn't hear himself (needed for barge-in)
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    this.ctx = new AudioContext();
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "capture-16k");
    this.node.port.onmessage = (e) => this.onFrame?.(e.data.pcm, e.data.db);
    src.connect(this.node);
    // The worklet must be pulled by the graph; route it to a muted gain so nothing is heard.
    const mute = this.ctx.createGain(); mute.gain.value = 0;
    this.node.connect(mute).connect(this.ctx.destination);
  }
  stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close();
    this.stream = null; this.ctx = null;
  }
}

// ---------------------------------------------------------------- Piper playback queue
class Speaker {
  constructor() { this.ctx = null; this.queue = []; this.current = null; this.playing = false; this.gen = 0; this.onstate = null; }
  async _ctx() {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 512;
      this.analyser.connect(this.ctx.destination);
      this.levels = new Float32Array(this.analyser.fftSize);
    }
    const sink = store.get("speakerId", "");
    if (sink && this.ctx.setSinkId && this.ctx.sinkId !== sink) {
      try { await this.ctx.setSinkId(sink); } catch { /* device gone: default output */ }
    }
    if (this.ctx.state === "suspended") await this.ctx.resume();
    return this.ctx;
  }
  // Start synthesising now (in parallel), play strictly in order.
  enqueue(text) {
    const gen = this.gen;
    const job = api.raw("/api/voice/tts", { text })
      .then((r) => r.arrayBuffer())
      .then(async (buf) => (await this._ctx()).decodeAudioData(buf))
      .catch((err) => { console.warn("TTS failed:", err); return null; });
    this.queue.push(job);
    if (!this.playing) this._next(gen);
  }
  async _next(gen) {
    if (gen !== this.gen) return;
    const job = this.queue.shift();
    if (!job) { this._set(false); return; }
    this._set(true);
    const buffer = await job;
    if (gen !== this.gen) return;
    if (!buffer) { this._next(gen); return; }
    const ctx = await this._ctx();
    if (ctx.state !== "running") {
      // Browser autoplay rules: no sound until the user has interacted with the page.
      this.stop();
      this.onblocked?.();
      return;
    }
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.analyser);
    src.onended = () => { if (this.current === src) { this.current = null; this._next(gen); } };
    this.current = src;
    src.start();
  }
  _set(p) { if (this.playing !== p) { this.playing = p; this.onstate?.(p); } }
  get busy() { return this.playing || this.queue.length > 0; }
  stop() {
    this.gen++;
    this.queue = [];
    try { this.current?.stop(); } catch { /* already stopped */ }
    this.current = null;
    this._set(false);
  }
  level() {
    if (!this.analyser || !this.playing) return 0;
    this.analyser.getFloatTimeDomainData(this.levels);
    let s = 0; for (const v of this.levels) s += v * v;
    return Math.sqrt(s / this.levels.length);
  }
  async beep(freq = 880, ms = 90) {  // tiny earcon when the wake word is heard
    const ctx = await this._ctx();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = freq; g.gain.value = 0.05;
    o.connect(g).connect(ctx.destination); o.start(); o.stop(ctx.currentTime + ms / 1000);
  }
}

// Split streamed text into speakable sentences, skipping code blocks.
class SentenceStream {
  constructor(onSentence) { this.buf = ""; this.inCode = false; this.onSentence = onSentence; }
  // Index just past the first sentence end at or after `from`, or -1.
  static _end(text, from = 0) {
    const re = /[.!?…]+(?=\s)|\n\s*\n/g;
    re.lastIndex = from;
    const m = re.exec(text);
    return m ? m.index + m[0].length : -1;
  }
  push(text) {
    this.buf += text;
    for (;;) {
      const fence = this.buf.indexOf("```");
      if (this.inCode) {
        if (fence < 0) { this.buf = this.buf.slice(-2); return; }  // keep a possible partial fence
        this.buf = this.buf.slice(fence + 3); this.inCode = false; continue;
      }
      let end = SentenceStream._end(this.buf);
      if (fence >= 0 && (end < 0 || fence < end)) {
        this._emit(this.buf.slice(0, fence));
        this.onSentence("I've put the code on screen.");
        this.buf = this.buf.slice(fence + 3); this.inCode = true; continue;
      }
      if (end < 0) return;
      // Merge very short fragments ("Sure.", "e.g.") with the next sentence for smoother speech.
      while (this.buf.slice(0, end).trim().length < 25) {
        const next = SentenceStream._end(this.buf, end);
        if (fence >= 0 && (next < 0 || fence < next)) break; // never merge across a code block
        if (next < 0) return; // wait for more text
        end = next;
      }
      this._emit(this.buf.slice(0, end));
      this.buf = this.buf.slice(end);
    }
  }
  flush() {
    this.push("\n\n"); // ends the last paragraph, still honouring code fences
    if (!this.inCode) this._emit(this.buf);
    this.buf = ""; this.inCode = false;
  }
  _emit(s) { if (s && s.replace(/[\s*_`#>-]/g, "").length > 1) this.onSentence(s.trim()); }
}

// ---------------------------------------------------------------- controller
export class Voice {
  constructor({ dog, toast, onTranscript, onBargeIn, setMicUi }) {
    this.dog = dog; this.toast = toast; this.onTranscript = onTranscript; this.onBargeIn = onBargeIn;
    this.setMicUi = setMicUi;
    this.mic = new Mic();
    this.speaker = new Speaker();
    this.ws = null;
    this.mode = "ptt";
    this.serverState = "idle";
    this.pttActive = false;
    this.settings = {};
    this.status = null;
    this.replyDone = true;
    this.barge = { floor: -60, run: 0 };
    this.speaker.onstate = (playing) => this._onSpeaking(playing);
    this.speaker.onblocked = () => {
      this.toast("Click anywhere on the page once so I'm allowed to speak (browser rule).", true);
      this._afterReply();
    };
    this.mic.onFrame = (pcm, db) => this._onFrame(pcm, db);
  }

  // ------------------------------------------------------------ setup
  async refresh() {
    try {
      this.status = await api.get("/api/voice/status");
      this.settings = this.status.settings || {};
    } catch { this.status = null; }
    return this.status;
  }
  get ready() { return !!(this.status && this.status.available?.stt && this.status.available?.websocket); }
  get piperReady() { return !!this.status?.tts?.ready; }

  _connect() {
    if (this.ws && this.ws.readyState <= 1) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/voice`);
      ws.binaryType = "arraybuffer";
      ws.onopen = () => { this.ws = ws; ws.send(JSON.stringify({ type: "config", mode: this.mode })); resolve(); };
      ws.onerror = () => reject(new Error("Couldn't connect to the voice service."));
      ws.onclose = () => {
        if (this.ws === ws) { this.ws = null; if (this.mode === "handsfree") this._fallbackToPtt("Voice connection closed."); }
      };
      ws.onmessage = (e) => this._onMessage(JSON.parse(e.data));
    });
  }
  _send(obj) { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(obj)); }

  // ------------------------------------------------------------ push-to-talk
  async pttStart() {
    if (!this.ready) { this.toast("Voice isn't available: see Settings > Voice.", true); return; }
    if (this.speaker.busy || this.dog.state === "speaking") this.bargeIn(false);
    try {
      await this._connect();
      await this.mic.start(store.get("micId", ""));
    } catch (err) { this._micError(err); return; }
    this.pttActive = true;
    this._duck(true);
    this._send({ type: "ptt_start" });
    this.dog.set("listening", { label: "Listening... (release to send)" });
    this.setMicUi({ recording: true });
  }
  pttStop() {
    if (!this.pttActive) return;
    this.pttActive = false;
    this._send({ type: "ptt_stop" });
    this.setMicUi({ recording: false });
    if (this.mode !== "handsfree") setTimeout(() => { if (!this.pttActive) this.mic.stop(); }, 300);
  }

  // ------------------------------------------------------------ hands-free
  async setHandsFree(on) {
    if (on) {
      if (!this.ready) { this.toast("Voice isn't available: see Settings > Voice.", true); return false; }
      try {
        this.mode = "handsfree";
        await this._connect();
        this._send({ type: "config", mode: "handsfree" });
        await this.mic.start(store.get("micId", ""));
      } catch (err) {
        this.mode = "ptt";
        this._send({ type: "config", mode: "ptt" });  // the server must stop waiting for a wake word too
        this._micError(err);
        return false;
      }
      this.setMicUi({ handsfree: true });
      this.toast(`Hands-free on. Say "${this.wakeLabel()}" to talk to me.`);
      return true;
    }
    this.mode = "ptt";
    this._send({ type: "config", mode: "ptt" });
    this.mic.stop();
    this.setMicUi({ handsfree: false });
    this._duck(false);
    if (["listening"].includes(this.dog.state)) this.dog.set("idle");
    return false;
  }
  wakeLabel() {
    const s = this.settings;
    return s.wake_engine === "openwakeword" ? String(s.oww_model || "hey jarvis").replace(/_/g, " ") : (s.wake_word || "Babilious");
  }
  _fallbackToPtt(msg) {
    this.mode = "ptt"; this.mic.stop(); this.setMicUi({ handsfree: false, recording: false });
    this.toast(msg, true);
  }
  _micError(err) {
    const msg = err?.name === "NotAllowedError" ? "Microphone permission was denied. Allow it in the browser's site settings."
      : err?.name === "NotFoundError" ? "No microphone found." : (err?.message || String(err));
    this.toast(msg, true);
    this.dog.set("error", { holdMs: 4000, label: "Can't hear you" });
    this.setMicUi({ recording: false, handsfree: false });
  }

  // ------------------------------------------------------------ audio frames
  _onFrame(pcm, db) {
    this.setMicUi({ level: db });
    // Client-side barge-in: the user talks over Babilious (echo cancellation keeps his voice out).
    if (this.mode === "handsfree" && this.speaker.playing && this.settings.barge_in === "voice") {
      const b = this.barge;
      const thresh = Math.max(b.floor + Number(this.settings.vad_sensitivity || 11) + 8, -45);
      if (db > thresh) b.run++; else { b.run = 0; b.floor = b.floor * 0.98 + Math.min(db, b.floor + 3) * 0.02; }
      if (b.run >= 12) { b.run = 0; this.bargeIn(true); }
    }
    if (this.ws?.readyState === 1 && (this.pttActive || this.mode === "handsfree")) this.ws.send(pcm);
  }

  // Stop talking now and listen to the new input.
  bargeIn(listen = true) {
    this.speaker.stop();
    this.onBargeIn?.();
    if (listen && this.mode === "handsfree") this._send({ type: "barge_in" });
  }

  // ------------------------------------------------------------ server events
  _onMessage(m) {
    switch (m.type) {
      case "state":
        this.serverState = m.state;
        if (m.state === "listening") this.dog.set("listening", { label: m.via === "follow_up" ? "Still listening..." : "Listening..." });
        else if (m.state === "transcribing") this.dog.set("thinking", { label: "Transcribing..." });
        else if (m.state === "wake" && this.dog.state === "listening") this.dog.set("idle", { label: `Idle · say "${this.wakeLabel()}"` });
        else if (m.state === "idle" && this.dog.state === "listening" && !this.pttActive) this.dog.set("idle");
        this.setMicUi({ serverState: m.state });
        break;
      case "ready":
        this.settings.wake_word = m.wake_word;
        break;
      case "wake":
        this._duck(true);
        this.speaker.beep();
        if (m.barge) this.bargeIn(false);
        this.dog.set("listening", { label: "Yes?" });
        break;
      case "transcript":
        this.onTranscript(m.text, m.via);
        break;
      case "heard_nothing":
        if (this.dog.state === "thinking" || this.dog.state === "listening") this.dog.set("confused", { holdMs: 2000, label: "Didn't catch that" });
        if (this.mode !== "handsfree") this._duck(false);
        break;
      case "listen_timeout":
        this._duck(false);
        this.dog.set("idle", { label: `Idle · say "${this.wakeLabel()}"` });
        break;
      case "error":
        this.toast(m.message, true);
        this.dog.set("error", { holdMs: 4000 });
        break;
    }
  }

  // ------------------------------------------------------------ spoken replies
  get output() { return this.settings.spoken_replies || "piper"; }
  beginReply() {
    this.speaker.stop();
    this.replyDone = false;
    this.sentences = new SentenceStream((s) => this.speaker.enqueue(s));
  }
  pushReply(token) { if (this.output === "piper" && this.piperReady) this.sentences?.push(token); }
  // Returns true if Piper will speak this reply (the caller handles other outputs).
  endReply(fullText, streamed) {
    this.replyDone = true;
    if (this.output !== "piper" || !this.piperReady) return false;
    if (!streamed) this.sentences.push(fullText);
    this.sentences.flush();
    if (!this.speaker.busy) this._afterReply();
    return true;
  }
  stopSpeaking() { this.speaker.stop(); }
  get speaking() { return this.speaker.busy; }

  _onSpeaking(playing) {
    this._send({ type: "speaking", value: playing });
    if (playing) {
      this.dog.set("speaking", { label: "Talking..." });
      this.dog.svg.classList.add("lipsync");
      const tick = () => {
        if (!this.speaker.playing) return;
        const lvl = Math.min(1, this.speaker.level() * 9);
        this.dog.svg.style.setProperty("--mouth", (0.15 + lvl * 0.85).toFixed(2));
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    } else {
      this.dog.svg.classList.remove("lipsync");
      if (this.replyDone && !this.speaker.busy) this._afterReply();
    }
  }
  _afterReply() {
    if (this.dog.state === "speaking") this.dog.set(this.pendingMood || "idle", { holdMs: 5000 });
    // Hands-free: keep listening briefly so a follow-up doesn't need the wake word.
    if (this.mode === "handsfree" && Number(this.settings.follow_up_seconds) > 0) this._send({ type: "follow_up" });
    else this._duck(false);   // the conversation is over: music back on (only if we paused it)
  }
  // Pause music while you talk to him; the server only resumes what it paused itself.
  _duck(on) {
    if (on === !!this.ducked) return;
    this.ducked = on;
    api.post("/api/music-voice", { phase: on ? "start" : "end" }).catch(() => {});
  }

  // ------------------------------------------------------------ devices
  async devices() {
    const list = await navigator.mediaDevices.enumerateDevices();
    return { inputs: list.filter((d) => d.kind === "audioinput"), outputs: list.filter((d) => d.kind === "audiooutput") };
  }
}

export { SentenceStream, store as voiceStore };
