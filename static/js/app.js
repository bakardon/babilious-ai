import { api, streamNdjson } from "./api.js";
import { Dog } from "./dog.js";
import { initFiles } from "./files.js";
import { initGraph } from "./graph.js";
import { Voice } from "./voice.js";
import { initVoiceSettings } from "./voice-settings.js";
import { initActions } from "./actions.js";
import { initManager } from "./manager.js";
import { initIntegrations, sourcesHtml, warningsHtml, citeHtml } from "./integrations.js";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const dog = new Dog($("#dog"), $("#portrait"), $("#status-line"));
window.babilious = { dog }; // handy for trying expressions from the console

const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ---------------------------------------------------------------- helpers
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// Minimal, safe Markdown: escape first, then code blocks, inline code, bold.
function md(text) {
  const parts = esc(text).split(/```/);
  return parts.map((p, i) => {
    if (i % 2) return `<pre>${p.replace(/^[\w+-]*\n/, "")}</pre>`;
    return p.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^\n]+?)\*\*/g, "<b>$1</b>");
  }).join("");
}
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
}
function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.round(mins / 60)}h ago`;
  return d.toLocaleDateString();
}
const gb = (bytes) => (bytes / 1e9).toFixed(1) + " GB";

// ---------------------------------------------------------------- navigation
const loaders = {};
function show(view) {
  $$(".rail-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === view));
  loaders[view]?.();
  store.set("view", view);
}
$$(".rail-btn").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-goto]");
  if (go) show(go.dataset.goto);
  const ask = e.target.closest("[data-ask]");
  if (ask) { show("chat"); send(ask.dataset.ask); }
});

// ---------------------------------------------------------------- voice
// Input: faster-whisper over a localhost WebSocket. Output: Piper (default), or the
// Windows voices installed on this PC (localService only; online voices are skipped).
const voiceSelect = $("#voice-select");
const synth = window.speechSynthesis;
const micBtn = $("#mic-btn");
const handsBtn = $("#hands-btn");
let localVoices = [];

const voice = new Voice({
  dog, toast,
  onTranscript: (text) => { show("chat"); send(text, "voice"); },
  onBargeIn: () => { controller?.abort(); if (synth?.speaking) synth.cancel(); },
  setMicUi,
});
window.babilious.voice = voice;

function setMicUi({ recording, handsfree, level, serverState } = {}) {
  if (recording !== undefined) micBtn.classList.toggle("recording", recording);
  if (handsfree !== undefined) {
    handsBtn.classList.toggle("on", handsfree);
    handsBtn.setAttribute("aria-pressed", String(handsfree));
  }
  if (level !== undefined) {
    const lvl = Math.max(0, Math.min(1, (level + 60) / 45));
    micBtn.style.setProperty("--lvl", lvl.toFixed(2));
  }
  const live = voice.mic.open;
  const st = $("#st-mic");
  st.textContent = !live ? "Off" : voice.mode === "handsfree"
    ? (serverState === "listening" || voice.serverState === "listening" ? "● Listening" : `● Waiting for "${voice.wakeLabel()}"`)
    : "● Recording";
  st.className = live ? "live" : "pending";
}

async function loadVoiceOptions() {
  await voice.refresh();
  localVoices = synth ? synth.getVoices().filter((v) => v.localService) : [];
  const piper = voice.piperReady;
  const name = voice.status?.tts?.voice?.split("-")[1] || "Piper";
  voiceSelect.innerHTML = '<option value="off">🔇 Voice off</option>' +
    `<option value="piper" ${piper ? "" : "disabled"}>🔊 Babilious (${esc(name)})${piper ? "" : ": download in Settings"}</option>` +
    (localVoices.length ? '<optgroup label="Windows voices">' + localVoices.map((v) =>
      `<option value="browser:${esc(v.name)}">${esc(v.name.replace(/^Microsoft /, ""))} (${esc(v.lang)})</option>`).join("") + "</optgroup>" : "");
  const out = voice.settings.spoken_replies || "piper";
  const bv = store.get("voice", "");
  voiceSelect.value = out === "piper" && piper ? "piper"
    : out === "browser" && localVoices.some((v) => v.name === bv) ? `browser:${bv}` : "off";
  $("#st-voice").textContent = voiceSelect.selectedOptions[0]?.textContent.replace(/^🔊 |^🔇 /, "") || "Off";
  const ok = voice.ready;
  micBtn.disabled = handsBtn.disabled = !ok;
  micBtn.title = ok ? "Hold to talk (or hold Space)" : "Voice packages missing: see Settings > Voice";
  handsBtn.title = ok ? `Hands-free: say "${voice.wakeLabel()}"` : micBtn.title;
}
if (synth) synth.onvoiceschanged = () => loadVoiceOptions();
voiceSelect.addEventListener("change", async () => {
  const v = voiceSelect.value;
  const spoken = v === "off" ? "off" : v === "piper" ? "piper" : "browser";
  if (spoken === "browser") store.set("voice", v.slice(8));
  try { await api.patch("/api/settings", { spoken_replies: spoken }); } catch (err) { toast(err.message, true); }
  voice.settings.spoken_replies = spoken;
  if (spoken === "off") stopSpeaking();
  $("#st-voice").textContent = voiceSelect.selectedOptions[0]?.textContent.replace(/^🔊 |^🔇 /, "") || "Off";
});

// Fallback output: Windows voices through the Web Speech API.
function speakBrowser(text, moodAfter) {
  const bv = localVoices.find((v) => `browser:${v.name}` === voiceSelect.value);
  if (!synth || !bv) { dog.set(moodAfter, { holdMs: 6000 }); voice._afterReply(); return; }
  synth.cancel();
  const clean = text.replace(/```[\s\S]*?```/g, " (code shown on screen) ").replace(/[*`#_>]/g, "");
  const u = new SpeechSynthesisUtterance(clean);
  u.voice = bv;
  u.rate = Number(voice.settings.speech_rate || 1);
  u.pitch = 0.85;
  u.onstart = () => dog.set("speaking");
  u.onend = () => { if (dog.state === "speaking") dog.set(moodAfter, { holdMs: 5000 }); voice._afterReply(); };
  u.onerror = () => { if (dog.state === "speaking") dog.set("idle"); };
  synth.speak(u);
}
function stopSpeaking() {
  voice.stopSpeaking();
  if (synth?.speaking || synth?.pending) synth.cancel();
  if (dog.state === "speaking") dog.set("idle");
}

// Push-to-talk: hold the mic button, or hold Space while not typing.
micBtn.addEventListener("pointerdown", (e) => { e.preventDefault(); micBtn.setPointerCapture(e.pointerId); voice.pttStart(); });
["pointerup", "pointercancel"].forEach((ev) => micBtn.addEventListener(ev, () => voice.pttStop()));
let spaceHeld = false;
const typingIn = (el) => el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(el.tagName));
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !e.repeat && !typingIn(document.activeElement) && $(".view-chat").classList.contains("active") && !micBtn.disabled) {
    e.preventDefault(); spaceHeld = true; voice.pttStart();
  }
});
document.addEventListener("keyup", (e) => { if (e.code === "Space" && spaceHeld) { spaceHeld = false; voice.pttStop(); } });
handsBtn.addEventListener("click", () => voice.setHandsFree(!handsBtn.classList.contains("on")).then(() => setMicUi()));

// ---------------------------------------------------------------- chat
const input = $("#input");
const sendBtn = $("#send-btn");
const caption = $("#caption");
let conversationId = store.get("conversationId", null);
let controller = null;

function setBusy(busy) {
  sendBtn.classList.toggle("stop", busy);
  sendBtn.title = busy ? "Stop" : "Send (Enter)";
  sendBtn.innerHTML = busy
    ? '<svg viewBox="0 0 24 24" width="26" height="26"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>'
    : '<svg viewBox="0 0 24 24" width="30" height="30"><path d="M3 20l18-8L3 4l2 7 9 1-9 1z" fill="currentColor"/></svg>';
}

async function send(text, via = "text") {
  text = (text ?? input.value).trim();
  if (!text) return;
  // Barge-in: a new message interrupts speech and any reply still streaming.
  stopSpeaking();
  controller?.abort();
  voice.beginReply();
  let streamed = false;
  input.value = "";
  autoGrow();
  dog.touch();

  caption.innerHTML = `<p class="you">You: ${esc(text)}</p><p class="reply cursor"></p>`;
  const replyEl = $(".reply", caption);
  appendMessage("user", text);
  dog.set("thinking");
  setBusy(true);
  controller = new AbortController();
  const mine = controller;
  let full = "";
  try {
    await streamNdjson("/api/chat/stream", { message: text, conversation_id: conversationId, via }, (ev) => {
      if (ev.type === "conversation") {
        conversationId = ev.id; store.set("conversationId", ev.id);
      } else if (ev.type === "state") {
        dog.set("thinking", { label: ev.detail || `Thinking with ${ev.model}...` });
      } else if (ev.type === "token") {
        if (!full) dog.set("speaking", { label: "Talking..." });
        full += ev.text;
        streamed = true;
        voice.pushReply(ev.text);
        replyEl.innerHTML = md(full);
        caption.scrollTop = caption.scrollHeight;
      } else if (ev.type === "done") {
        full = ev.text;
        replyEl.innerHTML = md(full);
        replyEl.classList.remove("cursor");
        appendMessage("assistant", full);
        renderExtras(replyEl, ev);
        if (ev.source === "offline") toast("Ollama is offline: answered from local notes.", true);
        if (ev.action === "model_switch") refreshStatus();
        if (ev.action?.startsWith("task")) refreshStatus();
        if (/^(task|habit|reminder|note|plan)/.test(ev.action || "")) managerUi?.loadToday();
        const mood = ev.mood || "idle";
        voice.pendingMood = mood;
        // A list of file paths is for reading, not for listening to.
        const spoken = ev.action === "file_search" && ev.data?.results?.length
          ? `I found ${ev.data.results.length} matching file${ev.data.results.length === 1 ? "" : "s"}. They're on screen.`
          : full;
        if (!voice.endReply(spoken, streamed && spoken === full)) {
          if (voiceSelect.value.startsWith("browser:")) speakBrowser(spoken, mood);
          else { dog.set(mood, { holdMs: 6000 }); voice._afterReply(); }
        }
      } else if (ev.type === "error") {
        replyEl.classList.remove("cursor");
        replyEl.innerHTML = `<span class="err">${esc(ev.message)}</span>`;
        voice.stopSpeaking();
        dog.set("error", { holdMs: 6000 });
      }
    }, mine.signal);
  } catch (err) {
    replyEl.classList.remove("cursor");
    if (err.name === "AbortError") {
      if (full) appendMessage("assistant", full + " [interrupted]");
      replyEl.innerHTML = md(full) + ' <span class="muted">[interrupted]</span>';
      if (controller === mine) dog.set("idle");
    } else {
      replyEl.innerHTML = `<span class="err">I couldn't reach my own server: ${esc(err.message)}. Is app.py still running?</span>`;
      dog.set("error", { holdMs: 8000 });
    }
  } finally {
    if (controller === mine) { controller = null; setBusy(false); }
  }
}

// Sources the reply used, and an optional "remember this?" offer (never saved without a click).
function renderExtras(replyEl, ev) {
  if (ev.web_sources?.length || ev.warnings?.length) {
    replyEl.innerHTML = citeHtml(replyEl.innerHTML);
    const div = document.createElement("div");
    div.className = "research-extras";
    div.innerHTML = warningsHtml(ev.warnings, esc) +
      (ev.web_sources?.length ? `<ol class="web-sources">${sourcesHtml(ev.web_sources, esc)}</ol>` : "") +
      (ev.research_id ? '<button class="link-btn" data-goto-research>Saved in Research ›</button>' : "");
    div.querySelector("[data-goto-research]")?.addEventListener("click", () => show("research"));
    replyEl.after(div);
  }
  if (/^music/.test(ev.action || "")) setTimeout(() => integrations.loadMusic(), 800);
  if (ev.actions?.some((a) => a.tool === "media_control")) setTimeout(() => integrations.loadMusic(), 800);
  if (ev.actions?.length) {
    const div = document.createElement("div");
    div.className = "action-cards";
    div.innerHTML = ev.actions.map(actionsUi.card).join("");
    replyEl.after(div);
    actionsUi.refreshPending();
  }
  const srcs = ev.sources || [];
  if (ev.action === "file_search" && ev.data?.results?.length) {
    srcs.push(...ev.data.results.slice(0, 6).map((r) => ({ doc_id: r.id, name: r.name })));
  }
  if (srcs.length) {
    const div = document.createElement("div");
    div.className = "sources";
    div.innerHTML = `${ev.action === "file_search" ? "Open:" : "Sources:"} ` +
      srcs.map((s) => `<button data-doc="${s.doc_id}" title="${esc(s.path || s.name)}">📄 ${esc(s.name)}</button>`).join("");
    replyEl.after(div);
  }
  if (ev.suggest_memory) {
    const div = document.createElement("div");
    div.className = "suggest";
    div.innerHTML = `💾 Remember this? <i>${esc(ev.suggest_memory)}</i>
      <button class="yes">Remember</button><button class="no">No</button>`;
    div.querySelector(".yes").onclick = async () => {
      try { await api.post("/api/memories", { content: ev.suggest_memory, kind: "fact" }); div.innerHTML = "💾 Remembered. Edit it in Second Brain."; }
      catch (err) { toast(err.message, true); }
    };
    div.querySelector(".no").onclick = () => div.remove();
    (caption.querySelector(".sources") || replyEl).after(div);
  }
}
caption.addEventListener("click", (e) => {
  const b = e.target.closest("[data-doc]");
  if (b) files.openDocument(Number(b.dataset.doc));
});

sendBtn.addEventListener("click", () => {
  if (controller) { controller.abort(); stopSpeaking(); } else send();
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  if (e.key === "Escape") { controller?.abort(); stopSpeaking(); }
});
function autoGrow() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; }
input.addEventListener("input", () => {
  autoGrow(); dog.touch();
  if (synth?.speaking || voice.speaking) stopSpeaking(); // typing interrupts him
  if (dog.state === "idle" && input.value) dog.set("listening", { label: "Listening..." });
  if (!input.value && dog.state === "listening") dog.set("idle");
});

// transcript
const messagesEl = $("#messages");
function appendMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `<div class="who">${role === "user" ? "YOU" : "BABILIOUS"}</div><div class="text">${md(text)}</div>`;
  messagesEl.appendChild(div);
  messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
}
async function loadConversation(id) {
  messagesEl.innerHTML = "";
  if (!id) return;
  try {
    const data = await api.get(`/api/conversations/${id}`);
    data.messages.forEach((m) => appendMessage(m.role, m.content));
    const last = data.messages.filter((m) => m.role === "assistant").pop();
    if (last) caption.innerHTML = `<p>${md(last.content)}</p>`;
  } catch {
    conversationId = null; store.set("conversationId", null);
  }
}
async function loadConversationList() {
  const { conversations } = await api.get("/api/conversations");
  $("#conversation-list").innerHTML = conversations.map((c) => `
    <li><button class="link" data-conv="${c.id}">${esc(c.title)} <span class="muted small">· ${c.message_count} msgs · ${when(c.updated_at)}</span></button>
    <button class="icon-btn" data-del-conv="${c.id}" title="Delete conversation">🗑</button></li>`).join("") || '<li class="muted">None yet.</li>';
}
$("#conversation-list").addEventListener("click", async (e) => {
  const open = e.target.closest("[data-conv]");
  const del = e.target.closest("[data-del-conv]");
  if (open) {
    conversationId = Number(open.dataset.conv); store.set("conversationId", conversationId);
    await loadConversation(conversationId);
  } else if (del && confirm("Delete this conversation permanently? This cannot be undone.")) {
    await api.del(`/api/conversations/${del.dataset.delConv}`);
    if (Number(del.dataset.delConv) === conversationId) newChat();
    loadConversationList();
  }
});
function openTranscript(open) {
  $("#transcript").classList.toggle("open", open);
  $("#transcript").setAttribute("aria-hidden", String(!open));
  if (open) loadConversationList();
}
$("#open-transcript").addEventListener("click", () => openTranscript(!$("#transcript").classList.contains("open")));
$("#close-transcript").addEventListener("click", () => openTranscript(false));
function newChat() {
  controller?.abort(); stopSpeaking();
  conversationId = null; store.set("conversationId", null);
  messagesEl.innerHTML = "";
  caption.innerHTML = '<p class="caption-hint">Fresh start. Try not to waste it.</p>';
  dog.set("curious", { holdMs: 3000 });
  input.focus();
}
$("#new-chat").addEventListener("click", newChat);

// ---------------------------------------------------------------- status panel
async function refreshStatus() {
  try {
    const s = await api.get("/api/status");
    $("#st-model").textContent = s.active_model;
    const o = $("#st-ollama");
    o.textContent = s.ollama ? "Running" : "Offline";
    o.className = s.ollama ? "ok" : "bad";
    const loaded = s.resources.loaded_models;
    $("#st-loaded").textContent = loaded.length ? loaded.map((m) => `${m.name} (${m.ram_gb} GB)`).join(", ") : "None (saving RAM)";
    const r = s.resources;
    $("#st-cpu").textContent = Math.round(r.cpu_percent) + "%";
    $("#st-cpu-bar").style.width = r.cpu_percent + "%";
    $("#st-cpu-bar").classList.toggle("hot", r.cpu_percent > 80);
    $("#st-ram").textContent = Math.round(r.ram_percent) + "%";
    $("#st-ram-bar").style.width = r.ram_percent + "%";
    $("#st-ram-bar").classList.toggle("hot", r.ram_percent > 85);
    $("#st-app").textContent = `${r.app_ram_mb} MB · ${r.ram_used_gb}/${r.ram_total_gb} GB used`;
    $("#st-task").innerHTML = s.current_task
      ? `<b>${esc(s.current_task.title)}</b><div class="small muted">${s.open_tasks} open task${s.open_tasks === 1 ? "" : "s"}</div>`
      : "Nothing open. Suspicious.";
    $("#st-activity").innerHTML = s.recent_activity.map((a) =>
      `<li><span class="tag ${a.status}">${a.status}</span>${esc(a.summary)} <span class="small">· ${when(a.ts)}</span></li>`).join("") || "<li>Nothing yet.</li>";
    if (!s.ollama && dog.state === "idle") dog.set("idle", { label: "Idle · Ollama offline (local notes only)" });
  } catch {
    $("#st-ollama").textContent = "App unreachable";
  }
}
// Poll gently and only while the page is visible.
setInterval(() => { if (!document.hidden && $(".view-chat").classList.contains("active")) refreshStatus(); }, 10000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshStatus(); });

// ---------------------------------------------------------------- tasks
let taskFilter = "open";
async function loadTasks() {
  const url = taskFilter === "open" ? "/api/tasks" : `/api/tasks?status=${taskFilter}`;
  let { tasks } = await api.get(taskFilter === "" ? "/api/tasks" : url);
  if (taskFilter === "open") tasks = tasks.filter((t) => t.status !== "done");
  const pr = { 1: "High", 2: "", 3: "Low" };
  $("#task-list").innerHTML = tasks.map((t) => `
    <li class="${t.status === "done" ? "done" : ""} ${t.due_date && t.status !== "done" && t.due_date < todayIso() ? "overdue" : ""}" data-id="${t.id}">
      <input type="checkbox" class="check" data-act="toggle" ${t.status === "done" ? "checked" : ""} title="Done">
      <div class="grow"><div class="title-text">${esc(t.title)}</div>
        <div class="sub">${[t.project, t.due_date ? "due " + t.due_date : "", t.notes].filter(Boolean).map(esc).join(" · ")}</div></div>
      ${pr[t.priority] ? `<span class="pill p${t.priority}">${pr[t.priority]}</span>` : ""}
      ${t.status === "in_progress" ? '<span class="pill active">In progress</span>' : ""}
      ${t.status === "open" ? '<button class="icon-btn" data-act="start" title="Mark in progress">▶</button>' : ""}
      <button class="icon-btn" data-act="edit" title="Rename">✎</button>
      <button class="icon-btn" data-act="delete" title="Delete">🗑</button>
    </li>`).join("") || '<li class="muted">No tasks here.</li>';
}
loaders.tasks = () => { loadTasks(); managerUi.loadTaskExtras(); };
const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
$("#task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api.post("/api/tasks", {
      title: $("#task-title").value, priority: Number($("#task-priority").value),
      due_date: $("#task-due").value || null, project: $("#task-project").value,
    });
    $("#task-title").value = ""; $("#task-due").value = "";
    loadTasks(); toast("Task added.");
  } catch (err) { toast(err.message, true); }
});
$("#task-filter").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  taskFilter = b.dataset.filter;
  $$("#task-filter button").forEach((x) => x.classList.toggle("active", x === b));
  loadTasks();
});
$("#task-list").addEventListener("click", async (e) => {
  const act = e.target.closest("[data-act]")?.dataset.act;
  const li = e.target.closest("li[data-id]");
  if (!act || !li) return;
  const id = li.dataset.id;
  try {
    if (act === "toggle") await api.patch(`/api/tasks/${id}`, { status: e.target.checked ? "done" : "open" });
    if (act === "start") await api.patch(`/api/tasks/${id}`, { status: "in_progress" });
    if (act === "edit") {
      const title = prompt("Rename task:", $(".title-text", li).textContent);
      if (title?.trim()) await api.patch(`/api/tasks/${id}`, { title });
    }
    if (act === "delete") {
      if (!confirm("Delete this task permanently?")) return;
      await api.del(`/api/tasks/${id}`);
    }
    loadTasks();
  } catch (err) { toast(err.message, true); }
});

// ---------------------------------------------------------------- calendar
let calMonth = new Date(); calMonth.setDate(1);
async function loadCalendar() {
  const y = calMonth.getFullYear(), m = calMonth.getMonth();
  $("#cal-title").textContent = calMonth.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const start = new Date(y, m, 1 - ((new Date(y, m, 1).getDay() + 6) % 7)); // Monday first
  const end = new Date(start); end.setDate(start.getDate() + 42);
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const { items: feed, calendar } = await api.get(`/api/calendar?from=${iso(start)}&to=${iso(end)}`);
  $("#cal-status").textContent = calendar?.configured ? (calendar.error ? `Calendar link problem: ${calendar.error}` : "") : "";
  const byDate = {};
  feed.forEach((it) => (byDate[it.date] ||= []).push(it));
  Object.values(byDate).forEach((list) => list.sort((a, b) => (a.time || "99") .localeCompare(b.time || "99")));
  const today = new Date().toDateString();
  let html = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => `<div class="dow">${d}</div>`).join("");
  for (let i = 0; i < 42; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const items = (byDate[key] || []).map((t) => t.kind === "reminder"
      ? `<div class="t rem" title="Reminder${t.recurring ? " (repeats)" : ""}: ${esc(t.title)}">${t.recurring ? "🔁" : "⏰"} ${esc(t.time)} ${esc(t.title)}</div>`
      : t.kind === "event"
      ? `<div class="t ev" title="Calendar: ${esc(t.title)}${t.time ? ` ${esc(t.time)}${t.end ? "–" + esc(t.end) : ""}` : " (all day)"}${t.location ? " · " + esc(t.location) : ""}">📅 ${esc(t.time)} ${esc(t.title)}</div>`
      : `<div class="t ${t.done ? "done" : ""}" title="${esc(t.title)}">${esc(t.title)}</div>`).join("");
    html += `<div class="day ${d.getMonth() !== m ? "other" : ""} ${d.toDateString() === today ? "today" : ""}" data-date="${key}"><div class="n">${d.getDate()}</div>${items}</div>`;
  }
  $("#calendar").innerHTML = html;
}
loaders.calendar = loadCalendar;
$("#cal-prev").addEventListener("click", () => { calMonth.setMonth(calMonth.getMonth() - 1); loadCalendar(); });
$("#cal-next").addEventListener("click", () => { calMonth.setMonth(calMonth.getMonth() + 1); loadCalendar(); });
$("#calendar").addEventListener("click", async (e) => {
  const day = e.target.closest(".day"); if (!day) return;
  const title = prompt(`New task due ${day.dataset.date}:`);
  if (!title?.trim()) return;
  try { await api.post("/api/tasks", { title, due_date: day.dataset.date }); loadCalendar(); toast("Task added."); }
  catch (err) { toast(err.message, true); }
});

// ---------------------------------------------------------------- second brain
async function loadMemories() {
  const q = $("#memory-search").value.trim();
  const { memories, kinds } = await api.get(`/api/memories${q ? "?q=" + encodeURIComponent(q) : ""}`);
  const sel = $("#memory-kind");
  if (!sel.options.length) sel.innerHTML = kinds.map((k) => `<option>${k}</option>`).join("");
  $("#memory-list").innerHTML = memories.map((m) => `
    <li data-id="${m.id}">
      <span class="pill">${esc(m.kind)}</span>
      <div class="grow"><div class="title-text">${esc(m.content)}</div>
        <div class="sub">from ${esc(m.source)} · ${when(m.updated_at)}</div></div>
      <button class="icon-btn" data-act="pin" title="${m.pinned ? "Unpin" : "Pin (always included)"}">${m.pinned ? "📌" : "📍"}</button>
      <button class="icon-btn" data-act="edit" title="Edit">✎</button>
      <button class="icon-btn" data-act="delete" title="Delete">🗑</button>
    </li>`).join("") || '<li class="muted">No memories yet. Say "remember that ..." in chat.</li>';
  const { files: kfiles } = await api.get("/api/knowledge");
  $("#knowledge-list").innerHTML = kfiles.map((f) =>
    `<li><span class="grow">${esc(f.name)}</span><span class="muted small">${f.empty ? "empty" : f.chars + " chars"}</span></li>`).join("");
}
async function loadBrainSettings() {
  const s = await api.get("/api/settings");
  const f = $("#brain-settings");
  f.elements.use_documents_in_chat.checked = !!s.use_documents_in_chat;
  f.elements.semantic_search.checked = !!s.semantic_search;
}
$("#brain-settings").addEventListener("change", async (e) => {
  const el = e.target;
  try {
    await api.patch("/api/settings", { [el.name]: el.checked });
    toast(el.name === "semantic_search" && el.checked ? "Semantic search on. Embedding your files in the background." : "Saved.");
  } catch (err) { el.checked = !el.checked; toast(err.message, true); }
});
loaders.brain = () => { loadMemories(); loadBrainSettings(); managerUi.loadNotes(); graph.load(); };
loaders.reminders = () => managerUi.loadReminders();
$("#memory-search").addEventListener("input", () => { clearTimeout(loadMemories.t); loadMemories.t = setTimeout(loadMemories, 250); });
$("#memory-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api.post("/api/memories", { content: $("#memory-content").value, kind: $("#memory-kind").value });
    $("#memory-content").value = ""; loadMemories(); toast("Remembered.");
  } catch (err) { toast(err.message, true); }
});
$("#memory-list").addEventListener("click", async (e) => {
  const act = e.target.closest("[data-act]")?.dataset.act;
  const li = e.target.closest("li[data-id]");
  if (!act || !li) return;
  const id = li.dataset.id;
  try {
    if (act === "pin") await api.patch(`/api/memories/${id}`, { pinned: e.target.textContent !== "📌" });
    if (act === "edit") {
      const content = prompt("Edit memory:", $(".title-text", li).textContent);
      if (content?.trim()) await api.patch(`/api/memories/${id}`, { content });
    }
    if (act === "delete") {
      if (!confirm("Delete this memory permanently?")) return;
      await api.del(`/api/memories/${id}`);
    }
    loadMemories();
  } catch (err) { toast(err.message, true); }
});

// ---------------------------------------------------------------- automation / activity
async function loadActivity() {
  const { activity } = await api.get("/api/activity?limit=100");
  $("#activity-list").innerHTML = activity.map((a) =>
    `<li><span class="tag ${a.status}">${a.status}</span><span class="grow">${esc(a.summary)}</span><span class="muted small">${esc(a.kind)} · ${when(a.ts)}</span></li>`).join("")
    || '<li class="muted">Nothing logged yet.</li>';
}
loaders.automation = () => { loadActivity(); actionsUi.load(); };

// ---------------------------------------------------------------- settings
$("#settings-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  $$("#settings-tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab-pane").forEach((p) => p.classList.toggle("active", p.dataset.tab === b.dataset.tab));
  if (b.dataset.tab === "health") loadHealth();
  if (b.dataset.tab === "prefs") loadPrefs();
  if (b.dataset.tab === "voice") voiceSettings.load();
  if (b.dataset.tab === "integrations") integrations.loadIntegrations();
});
loaders.settings = () => loadModels();

async function loadModels() {
  let data;
  try { data = await api.get("/api/models"); }
  catch (err) { $("#model-list").innerHTML = `<li class="muted">${esc(err.message)}</li>`; return; }
  if (!data.ollama) {
    $("#model-list").innerHTML = '<li class="muted">Ollama is not running. Start it (or run <code>ollama serve</code>) and reopen this page.</li>';
    $("#model-suggestions").innerHTML = "";
    return;
  }
  $("#model-list").innerHTML = data.installed.map((m) => {
    const b = m.benchmark;
    const bench = b ? `${b.tokens_per_sec ?? "?"} tok/s · first token ${b.first_token_ms ?? "?"} ms` : "not benchmarked";
    return `<li data-name="${esc(m.name)}">
      <div class="grow"><b>${esc(m.name)}</b> ${m.active ? '<span class="pill active">active</span>' : ""} ${m.loaded ? '<span class="pill loaded">in RAM</span>' : ""}
        <div class="sub">${esc(m.params)} · ${esc(m.quantization)} · ${gb(m.size)} on disk · ~${m.ram_gb} GB RAM · ${bench}</div>
        ${m.notes ? `<div class="sub">${esc(m.notes)}</div>` : ""}</div>
      ${m.embedding ? '<span class="pill">embeddings</span>' : m.active ? "" : '<button class="ghost-btn" data-act="use">Use</button>'}
      <button class="ghost-btn" data-act="bench">Benchmark</button>
      ${m.active ? "" : '<button class="ghost-btn danger" data-act="remove">Remove</button>'}
    </li>`;
  }).join("") || '<li class="muted">No models installed.</li>';
  $("#model-suggestions").innerHTML = data.suggestions.map((m) => `
    <li data-name="${esc(m.name)}"><div class="grow"><b>${esc(m.name)}</b> <span class="pill">${esc(m.label)}</span>
      <div class="sub">${esc(m.params)} · ${m.download_gb} GB download · ~${m.ram_gb} GB RAM · ${esc(m.notes)}</div>
      <div class="progress" hidden><i></i></div></div>
      <button class="ghost-btn" data-act="pull">Download</button></li>`).join("");
}
async function pullModel(name, li) {
  if (!confirm(`Download ${name} from the Ollama library? This uses disk space and bandwidth.`)) return;
  const bar = li ? $(".progress", li) : null;
  const btn = li ? $("[data-act=pull]", li) : $("#custom-pull");
  btn.disabled = true; btn.textContent = "Downloading...";
  if (bar) bar.hidden = false;
  try {
    await streamNdjson("/api/models/pull", { name }, (ev) => {
      if (ev.type === "progress" && ev.total && bar) $("i", bar).style.width = (100 * (ev.completed || 0) / ev.total) + "%";
      if (ev.type === "progress") btn.textContent = ev.status.slice(0, 22);
      if (ev.type === "error") throw new Error(ev.message);
    });
    toast(`${name} downloaded.`); loadModels();
  } catch (err) { toast(err.message, true); btn.disabled = false; btn.textContent = "Download"; }
}
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("#model-list [data-act], #model-suggestions [data-act]");
  if (!btn) return;
  const li = btn.closest("li"); const name = li.dataset.name;
  try {
    if (btn.dataset.act === "use") {
      await api.post("/api/models/active", { name }); toast(`Now running on ${name}.`); refreshStatus(); loadModels();
    } else if (btn.dataset.act === "bench") {
      btn.disabled = true; btn.textContent = "Running...";
      const r = await api.post("/api/models/benchmark", { name });
      toast(`${name}: ${r.tokens_per_sec} tokens/s, first token after ${r.first_token_ms} ms`); loadModels();
    } else if (btn.dataset.act === "remove") {
      if (!confirm(`Remove ${name} from disk? You can download it again later.`)) return;
      await api.del(`/api/models/${encodeURIComponent(name)}`); toast(`${name} removed.`); loadModels();
    } else if (btn.dataset.act === "pull") {
      await pullModel(name, li);
    }
  } catch (err) { toast(err.message, true); loadModels(); }
});
$("#custom-pull").addEventListener("click", () => {
  const name = $("#custom-model").value.trim(); if (name) pullModel(name, null);
});

async function loadPrefs() {
  const s = await api.get("/api/settings");
  const f = $("#prefs-form");
  for (const [k, v] of Object.entries(s)) if (f.elements[k]) f.elements[k].value = v;
}
$("#prefs-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {};
  for (const k of ["user_name", "temperature", "num_ctx", "max_reply_tokens", "history_messages", "keep_alive"]) body[k] = f.elements[k].value;
  try { await api.patch("/api/settings", body); $("#prefs-saved").textContent = "Saved."; setTimeout(() => ($("#prefs-saved").textContent = ""), 2000); }
  catch (err) { toast(err.message, true); }
});

async function loadHealth() {
  $("#health-list").innerHTML = '<li class="muted">Checking...</li>';
  const { checks } = await api.get("/api/health");
  $("#health-list").innerHTML = checks.map((c) => `
    <li><span class="health-dot ${c.status}"></span><div class="grow"><b>${esc(c.name)}</b>
      <div class="sub">${esc(c.detail)}${c.milestone && c.status === "pending" ? ` · Milestone ${c.milestone}` : ""}</div></div></li>`).join("");
}
$("#run-health").addEventListener("click", loadHealth);

// ---------------------------------------------------------------- second brain: files + graph
const actionsUi = initActions({ esc, toast, when, dog, show, send: (t) => send(t) });
const managerUi = initManager({ esc, toast, when, dog, voice, show, md, openConversation: async (id) => {
  conversationId = id; store.set("conversationId", id); await loadConversation(id); } });
const files = initFiles({ esc, toast, when, send: (t) => send(t), show });
const graph = initGraph({ onOpen: (id) => files.openDocument(id) });
loaders.files = files.load;
const integrations = initIntegrations({ esc, md, toast, when, dog });
loaders.research = integrations.loadResearch;
integrations.startMusicPolling();

// ---------------------------------------------------------------- voice settings
const voiceSettings = initVoiceSettings({ voice, toast, esc, onChange: loadVoiceOptions });

// ---------------------------------------------------------------- boot
refreshStatus();
loadVoiceOptions();
actionsUi.refreshPending();
loadConversation(conversationId);
const startView = store.get("view", "chat");
show($(`.rail-btn[data-view="${startView}"]`) ? startView : "chat");
input.focus();
