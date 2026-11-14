// Personal manager UI: reminders, notifications, habits, projects, notes, history, today card.
import { api } from "./api.js";

const $ = (s, r = document) => r.querySelector(s);
const MARK = /\u0002([\s\S]*?)\u0003/g;

export function initManager({ esc, toast, when, dog, voice, show, md, openConversation }) {
  const todayISO = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };

  // ------------------------------------------------------------ reminders view
  async function loadReminders() {
    const { reminders } = await api.get("/api/reminders");
    const fired = reminders.filter((r) => r.status === "fired");
    const upcoming = reminders.filter((r) => r.status === "scheduled");
    $("#reminders-fired").innerHTML = fired.map((r) => reminderRow(r, true)).join("") ||
      '<li class="muted">Nothing waiting.</li>';
    $("#reminders-upcoming").innerHTML = upcoming.map((r) => reminderRow(r, false)).join("") ||
      '<li class="muted">No reminders. Try "remind me tomorrow at 9 to continue the radar project".</li>';
    const { notifications } = await api.get("/api/notifications?all=1");
    $("#notification-list").innerHTML = notifications.slice(0, 15).map((n) => `
      <li class="${n.read ? "" : "unread"}"><span class="grow"><b>${esc(n.title)}</b><div class="sub">${esc(n.body)}</div></span>
        <span class="muted small">${when(n.created_at)}</span></li>`).join("") || '<li class="muted">No notifications yet.</li>';
    const s = await api.get("/api/settings");
    const f = $("#manager-settings");
    f.elements.desktop_notifications.checked = s.desktop_notifications !== false;
    f.elements.briefing_time.value = s.briefing_time || "";
  }
  function reminderRow(r, fired) {
    return `<li data-rid="${r.id}">
      <span class="ico">${r.recurrence ? "🔁" : "⏰"}</span>
      <div class="grow"><b>${esc(r.text)}</b><div class="sub">${esc(r.when)}${fired ? " · waiting for you" : ""}</div></div>
      ${fired ? `<button class="ghost-btn" data-rop="done">Done</button>
                 <button class="ghost-btn" data-rop="snooze" data-min="10">+10 min</button>
                 <button class="ghost-btn" data-rop="snooze" data-min="60">+1 h</button>` : ""}
      <button class="icon-btn" data-rop="cancel" title="Cancel reminder">🗑</button></li>`;
  }
  async function reminderOp(rid, op, minutes) {
    try {
      await api.post(`/api/reminders/${rid}/${op}`, op === "snooze" ? { minutes } : {});
      toast(op === "done" ? "Done." : op === "snooze" ? `Snoozed ${minutes >= 60 ? minutes / 60 + " h" : minutes + " min"}.` : "Reminder cancelled.");
      document.querySelectorAll(`.banner[data-rid="${rid}"]`).forEach((b) => b.remove());
      loadReminders().catch(() => {});
      loadToday();
    } catch (err) { toast(err.message, true); }
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-rop]");
    if (!b) return;
    const rid = b.closest("[data-rid]").dataset.rid;
    if (b.dataset.rop === "cancel" && !confirm("Cancel this reminder?")) return;
    reminderOp(rid, b.dataset.rop, Number(b.dataset.min || 10));
  });

  let previewTimer = null;
  $("#reminder-when").addEventListener("input", (e) => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      const v = e.target.value.trim();
      if (!v) { $("#reminder-preview").innerHTML = "&nbsp;"; return; }
      const r = await api.post("/api/parse-time", { text: v });
      $("#reminder-preview").textContent = r.ok ? `→ ${r.when}` : "→ I can't read that time yet (or it has already passed).";
      $("#reminder-preview").classList.toggle("bad-text", !r.ok);
    }, 250);
  });
  $("#reminder-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api.post("/api/reminders", { text: $("#reminder-text").value, when: $("#reminder-when").value });
      toast(`I'll remind you ${r.when}.`);
      e.target.reset(); $("#reminder-preview").innerHTML = "&nbsp;";
      loadReminders(); loadToday();
    } catch (err) { toast(err.message, true); }
  });
  $("#manager-settings").addEventListener("change", async (e) => {
    const el = e.target;
    const patch = { [el.name]: el.type === "checkbox" ? el.checked : el.value };
    try { await api.patch("/api/settings", patch); toast(el.name === "briefing_time" && el.value ? `Morning briefing at ${el.value}.` : "Saved."); }
    catch (err) { toast(err.message, true); }
  });

  // ------------------------------------------------------------ notifications (poll every 15 s)
  let lastId = 0;
  const shown = new Set();
  async function poll() {
    let list;
    try { ({ notifications: list } = await api.get(`/api/notifications?after=${lastId}`)); } catch { return; }
    if (!list.length) return;
    lastId = Math.max(lastId, ...list.map((n) => n.id));
    const fresh = list.filter((n) => !shown.has(n.id)).reverse().slice(-5);
    for (const n of fresh) {
      shown.add(n.id);
      banner(n);
    }
    if (fresh.length) {
      dog.set("excited", { holdMs: 5000, label: fresh[fresh.length - 1].title.replace(/^\W+/, "") });
      const spoken = fresh.filter((n) => n.kind === "reminder").map((n) => "Reminder: " + n.title.replace(/^\W+/, "")).join(". ");
      if (spoken && voice.output === "piper" && voice.piperReady) voice.speaker.enqueue(spoken);
      else voice.speaker.beep(660, 160);
      loadToday();
    }
  }
  function banner(n) {
    const div = document.createElement("div");
    div.className = `banner kind-${n.kind}`;
    if (n.reminder_id) div.dataset.rid = n.reminder_id;
    div.innerHTML = `<div class="grow"><b>${esc(n.title)}</b><div class="sub">${md(n.body)}</div></div>
      ${n.kind === "reminder" && n.reminder_id ? `<button class="ghost-btn" data-rop="done">Done</button>
        <button class="ghost-btn" data-rop="snooze" data-min="10">+10 min</button>` : ""}
      <button class="icon-btn" data-dismiss title="Dismiss">✕</button>`;
    div.querySelector("[data-dismiss]").onclick = () => { div.remove(); api.post("/api/notifications/read", { id: n.id }); };
    $("#banners").appendChild(div);
    if (n.kind !== "reminder") setTimeout(() => div.remove(), 30000);
  }
  setInterval(() => { poll(); }, 15000);

  // ------------------------------------------------------------ today card
  async function loadToday() {
    try {
      const a = await api.get("/api/agenda");
      const bits = [];
      if (a.overdue.length) bits.push(`<span class="bad-text">${a.overdue.length} overdue</span>`);
      if (a.due_today.length) bits.push(`${a.due_today.length} due today`);
      const next = a.reminders_today[0];
      if (next) bits.push(`next: ${esc(next.text)} at ${next.due_local.slice(11, 16)}`);
      if (a.unacknowledged.length) bits.push(`${a.unacknowledged.length} reminder(s) waiting`);
      if (a.habits_due.length) bits.push(`habits left: ${a.habits_due.map((h) => esc(h.name)).join(", ")}`);
      $("#st-today").innerHTML = bits.join(" · ") || "Nothing scheduled today.";
    } catch { /* offline */ }
  }

  // ------------------------------------------------------------ projects & habits (Tasks view)
  async function loadTaskExtras() {
    const [{ projects }, { habits }] = await Promise.all([api.get("/api/projects-summary"), api.get("/api/habits")]);
    $("#project-progress").innerHTML = projects.filter((p) => p.total).map((p) => {
      const pct = Math.round((100 * p.done) / p.total);
      return `<li><div class="grow"><b>${esc(p.project)}</b> <span class="muted small">${p.done}/${p.total} done</span>
        <div class="bar small-bar"><i style="width:${pct}%"></i></div>
        <div class="sub">${p.next_task ? "Next: " + esc(p.next_task) : "All done"}${p.next_due ? " · due " + esc(p.next_due) : ""}
          ${p.overdue ? ` · <span class="bad-text">${p.overdue} overdue</span>` : ""}</div></div></li>`;
    }).join("") || '<li class="muted">No tasks yet.</li>';
    $("#habit-list").innerHTML = habits.map((h) => `
      <li data-hid="${h.id}">
        <button class="habit-check ${h.done_today ? "done" : ""}" data-habit="toggle" title="${h.done_today ? "Undo today" : "Done today"}">${h.done_today ? "✓" : ""}</button>
        <div class="grow"><b>${esc(h.name)}</b>
          <div class="sub">🔥 ${h.streak} ${h.streak_unit} · ${esc(h.progress)}</div>
          <div class="dots">${h.last14.map((d) => `<i class="${d ? "on" : ""}"></i>`).join("")}</div></div>
        <button class="icon-btn" data-habit="archive" title="Stop tracking">🗑</button></li>`).join("") ||
      '<li class="muted">No habits yet.</li>';
  }
  $("#habit-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-habit]");
    if (!b) return;
    const li = b.closest("[data-hid]");
    try {
      if (b.dataset.habit === "toggle") {
        const done = !b.classList.contains("done");
        const h = await api.post(`/api/habits/${li.dataset.hid}/log`, { done });
        if (done) { toast(`🔥 ${h.name}: ${h.streak} ${h.streak_unit}`); dog.set(h.streak >= 3 ? "excited" : "happy", { holdMs: 3000 }); }
      } else if (confirm("Stop tracking this habit? Its history is kept.")) {
        await api.del(`/api/habits/${li.dataset.hid}`);
      }
      loadTaskExtras(); loadToday();
    } catch (err) { toast(err.message, true); }
  });
  $("#habit-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.post("/api/habits", { name: $("#habit-name").value, schedule: $("#habit-schedule").value });
      e.target.reset(); loadTaskExtras(); toast("Habit added.");
    } catch (err) { toast(err.message, true); }
  });

  // ------------------------------------------------------------ notes (Second Brain)
  async function loadNotes() {
    const q = $("#note-search").value.trim();
    const { notes } = await api.get(`/api/notes${q ? "?q=" + encodeURIComponent(q) : ""}`);
    $("#note-list").innerHTML = notes.map((n) => `
      <li data-nid="${n.id}" class="note-item">
        <div class="grow"><b>${esc(n.title)}</b>
          <div class="note-body">${n.snippet ? esc(n.snippet).replace(MARK, "<mark>$1</mark>") : esc(n.body.slice(0, 400))}</div>
          <div class="sub">${n.tags ? n.tags.split(" ").map((t) => `<span class="kw">#${esc(t)}</span>`).join("") : ""} ${when(n.updated_at)}</div></div>
        <button class="icon-btn" data-note="edit" title="Edit">✎</button>
        <button class="icon-btn" data-note="delete" title="Delete">🗑</button></li>`).join("") ||
      `<li class="muted">${q ? "No matching notes." : "No notes yet."}</li>`;
  }
  $("#note-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api.post("/api/notes", { body: $("#note-body").value }); e.target.reset(); loadNotes(); toast("Noted."); }
    catch (err) { toast(err.message, true); }
  });
  let noteTimer = null;
  $("#note-search").addEventListener("input", () => { clearTimeout(noteTimer); noteTimer = setTimeout(loadNotes, 250); });
  $("#note-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-note]");
    if (!b) return;
    const id = b.closest("[data-nid]").dataset.nid;
    try {
      if (b.dataset.note === "delete") {
        if (!confirm("Delete this note permanently?")) return;
        await api.del(`/api/notes/${id}`);
      } else {
        const full = await api.get(`/api/notes?q=`).then((r) => r.notes.find((n) => String(n.id) === id));
        const body = prompt("Edit note:", full ? full.body : "");
        if (body === null || !body.trim()) return;
        await api.patch(`/api/notes/${id}`, { body });
      }
      loadNotes();
    } catch (err) { toast(err.message, true); }
  });

  // ------------------------------------------------------------ history search (conversation drawer)
  let histTimer = null;
  $("#history-q").addEventListener("input", (e) => {
    clearTimeout(histTimer);
    histTimer = setTimeout(async () => {
      const q = e.target.value.trim();
      if (q.length < 2) { $("#history-results").innerHTML = ""; return; }
      const { results } = await api.get(`/api/history?q=${encodeURIComponent(q)}`);
      $("#history-results").innerHTML = results.map((r) => `
        <li data-kind="${r.kind}" data-id="${r.id}" class="${r.kind === "chat" ? "clickable" : ""}">
          <span class="pill">${esc(r.kind)}</span><div class="grow"><div class="sub">${esc(r.title)} · ${when(r.when)}</div>
          <div>${esc(r.text)}</div></div></li>`).join("") || '<li class="muted">Nothing found.</li>';
    }, 300);
  });
  $("#history-results").addEventListener("click", (e) => {
    const li = e.target.closest("[data-kind]");
    if (!li) return;
    if (li.dataset.kind === "chat") openConversation(Number(li.dataset.id));
    else if (li.dataset.kind === "note") { show("brain"); }
    else if (li.dataset.kind === "task") { show("tasks"); }
    else if (li.dataset.kind === "reminder") { show("reminders"); }
    else if (li.dataset.kind === "research") { show("research"); }
  });

  // boot
  poll(); loadToday();
  return { loadReminders, loadTaskExtras, loadNotes, loadToday, poll };
}
