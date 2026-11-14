// Settings > Voice: models, voice, wake word, barge-in and devices.
import { api } from "./api.js";
import { voiceStore as store } from "./voice.js";

const $ = (s) => document.querySelector(s);

export function initVoiceSettings({ voice, toast, esc, onChange }) {
  let poll = null;

  async function save(patch, msg = "Saved.") {
    try {
      await api.patch("/api/settings", patch);
      Object.assign(voice.settings, patch);
      toast(msg);
      onChange?.();
      return true;
    } catch (err) { toast(err.message, true); load(); return false; }
  }

  async function load() {
    const st = await voice.refresh();
    if (!st || !st.stt) {
      $("#stt-models").innerHTML = `<li class="muted">Voice packages missing (${esc((st?.missing || []).join(", "))}).
        Run <code>.venv\\Scripts\\python -m pip install -r requirements.txt</code> and restart.</li>`;
      return;
    }
    const s = st.settings;
    $("#stt-models").innerHTML = st.stt.models.map((m) => `
      <li><label class="grow radio"><input type="radio" name="stt" value="${m.name}" ${m.name === s.stt_model ? "checked" : ""}>
        <span><b>${esc(m.label)}</b> <span class="muted small">${m.name} · ${m.download_mb} MB · ~${m.ram_mb} MB RAM
          ${m.downloaded ? "· downloaded" : "· downloads on first use"}</span><div class="sub">${esc(m.notes)}</div></span></label></li>`).join("");
    $("#stt-status").textContent = st.preparing ? "Loading..." : st.prepare_error ? `Error: ${st.prepare_error}`
      : st.stt.loaded ? `${st.stt.model} loaded` : "Loads automatically the first time you speak";
    $("#tts-voices").innerHTML = st.tts.catalog.map((v) => `
      <li data-voice="${v.name}">
        <label class="grow radio"><input type="radio" name="tts" value="${v.name}" ${v.name === s.tts_voice ? "checked" : ""} ${v.installed ? "" : "disabled"}>
          <span><b>${esc(v.label)}</b> <span class="muted small">${v.download_mb} MB${v.download ? " · " + esc(v.download) : ""}</span></span></label>
        ${v.installed ? '<button class="ghost-btn" data-act="preview">▶ Preview</button>'
          : `<button class="ghost-btn" data-act="download" ${v.download === "downloading" ? "disabled" : ""}>${v.download === "downloading" ? "Downloading..." : "Download"}</button>`}
      </li>`).join("");
    $("#speech-rate").value = s.speech_rate; $("#rate-val").textContent = `${Number(s.speech_rate).toFixed(2)}×`;
    $("#wake-engine").value = s.wake_engine;
    $("#wake-word").value = s.wake_word;
    $("#oww-model").value = s.oww_model;
    $("#wake-word-field").hidden = s.wake_engine !== "whisper";
    $("#oww-field").hidden = s.wake_engine !== "openwakeword";
    $("#barge-in").value = s.barge_in;
    $("#follow-up").value = s.follow_up_seconds; $("#follow-val").textContent = s.follow_up_seconds > 0 ? `${s.follow_up_seconds}s` : "off";
    $("#vad-sens").value = 30 - s.vad_sensitivity; $("#sens-val").textContent = sensLabel(s.vad_sensitivity);
    await loadDevices();
    clearTimeout(poll);
    if (st.preparing || st.tts.catalog.some((v) => v.download === "downloading")) poll = setTimeout(load, 1500);
  }
  const sensLabel = (db) => (db <= 9 ? "high" : db <= 14 ? "normal" : "low");

  async function loadDevices() {
    try {
      const { inputs, outputs } = await voice.devices();
      const named = inputs.some((d) => d.label);
      $("#mic-device").innerHTML = '<option value="">System default</option>' +
        inputs.filter((d) => d.deviceId !== "default").map((d, i) => `<option value="${esc(d.deviceId)}">${esc(d.label || "Microphone " + (i + 1))}</option>`).join("");
      $("#speaker-device").innerHTML = '<option value="">System default</option>' +
        outputs.filter((d) => d.deviceId !== "default").map((d, i) => `<option value="${esc(d.deviceId)}">${esc(d.label || "Speaker " + (i + 1))}</option>`).join("");
      $("#mic-device").value = store.get("micId", "");
      $("#speaker-device").value = store.get("speakerId", "");
      $("#device-note").textContent = named ? "" : "Device names appear after you allow microphone access once (use Test microphone).";
      $("#speaker-device").disabled = !("setSinkId" in AudioContext.prototype);
      if ($("#speaker-device").disabled) $("#device-note").textContent += " This browser can't choose the speaker; the system default is used.";
    } catch { $("#device-note").textContent = "This browser can't list audio devices."; }
  }

  $("#stt-models").addEventListener("change", (e) => {
    if (e.target.name === "stt") save({ stt_model: e.target.value }, `Speech model: ${e.target.value}. It loads (and downloads if needed) on first use.`);
  });
  $("#tts-voices").addEventListener("change", (e) => {
    if (e.target.name === "tts") save({ tts_voice: e.target.value }, "Voice changed.");
  });
  $("#tts-voices").addEventListener("click", async (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    const name = e.target.closest("[data-voice]")?.dataset.voice;
    if (!act || !name) return;
    if (act === "download") {
      if (!confirm(`Download the Piper voice "${name}" (about 63 MB) from huggingface.co/rhasspy/piper-voices?`)) return;
      try { await api.post("/api/voice/voices/download", { name }); toast("Downloading..."); load(); }
      catch (err) { toast(err.message, true); }
    } else if (act === "preview") {
      try {
        const res = await api.raw("/api/voice/tts", { text: "Right. I'm Babilious. Ask me something worth my time.", voice: name });
        const ctx = new AudioContext();
        const buf = await ctx.decodeAudioData(await res.arrayBuffer());
        const src = ctx.createBufferSource(); src.buffer = buf; src.connect(ctx.destination); src.start();
        src.onended = () => ctx.close();
      } catch (err) { toast(err.message, true); }
    }
  });
  $("#voice-prepare").addEventListener("click", async () => {
    try { await api.post("/api/voice/prepare"); $("#stt-status").textContent = "Loading..."; setTimeout(load, 1500); }
    catch (err) { toast(err.message, true); }
  });
  $("#speech-rate").addEventListener("input", (e) => ($("#rate-val").textContent = `${Number(e.target.value).toFixed(2)}×`));
  $("#speech-rate").addEventListener("change", (e) => save({ speech_rate: Number(e.target.value) }));
  $("#wake-engine").addEventListener("change", (e) => save({ wake_engine: e.target.value }).then(load));
  $("#wake-word").addEventListener("change", (e) => save({ wake_word: e.target.value.trim() }, `Wake word: "${e.target.value.trim()}"`));
  $("#oww-model").addEventListener("change", (e) => save({ oww_model: e.target.value }));
  $("#barge-in").addEventListener("change", (e) => save({ barge_in: e.target.value }));
  $("#follow-up").addEventListener("input", (e) => ($("#follow-val").textContent = e.target.value > 0 ? `${e.target.value}s` : "off"));
  $("#follow-up").addEventListener("change", (e) => save({ follow_up_seconds: Number(e.target.value) }));
  $("#vad-sens").addEventListener("input", (e) => ($("#sens-val").textContent = sensLabel(30 - e.target.value)));
  $("#vad-sens").addEventListener("change", (e) => save({ vad_sensitivity: 30 - Number(e.target.value) }));
  $("#mic-device").addEventListener("change", (e) => { store.set("micId", e.target.value); toast("Microphone saved (used next time the mic opens)."); });
  $("#speaker-device").addEventListener("change", (e) => { store.set("speakerId", e.target.value); toast("Speaker saved."); });

  // Mic test: shows the input level for 5 seconds. Nothing is sent anywhere.
  $("#mic-test").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    let stream, ctx;
    try {
      const id = store.get("micId", "");
      stream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: id ? { exact: id } : undefined, echoCancellation: true, noiseSuppression: true } });
      ctx = new AudioContext();
      const an = ctx.createAnalyser(); an.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(an);
      const data = new Float32Array(an.fftSize);
      btn.disabled = true; btn.textContent = "Say something...";
      const until = Date.now() + 5000;
      let peak = 0;
      await new Promise((resolve) => {
        const tick = () => {
          an.getFloatTimeDomainData(data);
          let sum = 0; for (const v of data) sum += v * v;
          const lvl = Math.min(1, Math.sqrt(sum / data.length) * 8);
          peak = Math.max(peak, lvl);
          $("#mic-test-level").style.width = `${lvl * 100}%`;
          if (Date.now() < until) requestAnimationFrame(tick); else resolve();
        };
        tick();
      });
      toast(peak > 0.15 ? "Microphone works." : "Very quiet: check the mic or raise its level in Windows sound settings.", peak <= 0.15);
      loadDevices();
    } catch (err) {
      toast(err.name === "NotAllowedError" ? "Microphone permission denied." : err.message, true);
    } finally {
      stream?.getTracks().forEach((t) => t.stop()); ctx?.close();
      btn.disabled = false; btn.textContent = "Test microphone";
      $("#mic-test-level").style.width = "0";
    }
  });

  return { load };
}
