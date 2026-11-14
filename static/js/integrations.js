// Milestone 6 UI: the Research view, the music card, and Settings > Integrations.
import { api, streamNdjson } from "./api.js";

const $ = (s, r = document) => r.querySelector(s);

export function sourcesHtml(list, esc) {
  return (list || []).map((s) => `<li value="${s.n}"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>
    <span class="muted small">${esc(s.site)}${s.kind && s.kind !== "web" ? " · " + esc(s.kind) : ""}</span></li>`).join("");
}
export function warningsHtml(list, esc) {
  return (list || []).map((w) => `<div class="warn">⚠️ ${esc(w)}</div>`).join("");
}
// [1] [2] citations become small superscript badges.
export function citeHtml(html) {
  return html.replace(/\[(\d{1,2})\]/g, '<sup class="cite">$1</sup>');
}

export function initIntegrations({ esc, md, toast, when, dog }) {
  // ------------------------------------------------------------ research view
  let running = null;
  let liveId = null;
  const form = $("#research-form");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (running) { running.abort(); return; }
    const topic = form.elements.topic.value.trim();
    if (topic.length < 3) return;
    const live = $("#research-live");
    live.hidden = false;
    $("#research-progress").innerHTML = "";
    $("#research-summary").innerHTML = "";
    $("#research-warnings").innerHTML = "";
    $("#research-sources").innerHTML = "";
    $("#research-live-actions").hidden = true;
    const btn = $("#research-go");
    btn.textContent = "Stop";
    running = new AbortController();
    dog?.set("thinking", { label: "Researching..." });
    let text = "";
    const step = (html, cls = "") => {
      const li = document.createElement("li");
      li.className = cls; li.innerHTML = html;
      $("#research-progress").append(li);
    };
    try {
      await streamNdjson("/api/research", { topic, depth: form.elements.depth.value, papers: form.elements.papers.checked }, (ev) => {
        if (ev.type === "state" && ev.detail) step(esc(ev.detail), "muted");
        else if (ev.type === "progress") step(esc(ev.detail), "muted");
        else if (ev.type === "source") {
          step(ev.ok ? `✓ Read <b>${esc(ev.site)}</b> <span class="muted">(${(ev.chars || 0).toLocaleString()} chars${ev.note ? ", " + esc(ev.note) : ""})</span>`
                     : `✗ Skipped <b>${esc(ev.site)}</b> <span class="muted">(${esc(ev.note || "unreadable")})</span>`, ev.ok ? "ok" : "bad");
        } else if (ev.type === "token") {
          text += ev.text;
          $("#research-summary").innerHTML = citeHtml(md(text));
        } else if (ev.type === "done") {
          $("#research-summary").innerHTML = citeHtml(md(ev.text));
          $("#research-warnings").innerHTML = warningsHtml(ev.warnings, esc);
          $("#research-sources").innerHTML = sourcesHtml(ev.web_sources, esc);
          liveId = ev.research_id || null;
          $("#research-live-actions").hidden = !liveId;
          dog?.set(ev.mood || "happy", { holdMs: 4000 });
        }
      }, running.signal);
    } catch (err) {
      if (err.name !== "AbortError") { toast(err.message, true); dog?.set("error", { holdMs: 4000 }); }
      else step("Stopped.", "muted");
    } finally {
      running = null;
      btn.textContent = "Research";
      loadResearch();
    }
  });
  $("#research-live-actions").addEventListener("click", async (e) => {
    if (e.target.dataset.rs === "note" && liveId) {
      try { await api.post(`/api/research/${liveId}/note`); toast("Saved to your notes (#research)."); }
      catch (err) { toast(err.message, true); }
    }
  });

  let searchTimer;
  $("#research-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadResearch, 250); });

  async function loadResearch() {
    const q = $("#research-search").value.trim();
    const { research } = await api.get(`/api/research${q ? "?q=" + encodeURIComponent(q) : ""}`);
    $("#research-list").innerHTML = research.map((r) => `
      <li class="research-item" data-id="${r.id}">
        <div class="grow">
          <button class="link-btn research-open"><b>${esc(r.topic)}</b></button>
          <div class="sub">${when(r.created_at)} · ${r.sources.length} source${r.sources.length === 1 ? "" : "s"} · ${esc(r.depth)}${r.model ? " · " + esc(r.model) : " · not summarised"}${r.warnings.length ? " · ⚠️" : ""}</div>
          <div class="research-body" hidden>
            <div class="research-summary">${citeHtml(md(r.summary))}</div>
            ${warningsHtml(r.warnings, esc)}
            <ol class="web-sources">${sourcesHtml(r.sources, esc)}</ol>
          </div>
        </div>
        <button class="ghost-btn" data-op="note" title="Save as a note">📝</button>
        <button class="icon-btn" data-op="delete" title="Delete">🗑</button>
      </li>`).join("") || '<li class="muted">Nothing yet. Ask above, or in chat: "research ..."</li>';
  }
  $("#research-list").addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-id]"); if (!li) return;
    const id = li.dataset.id;
    if (e.target.closest(".research-open")) { const b = $(".research-body", li); b.hidden = !b.hidden; return; }
    const op = e.target.closest("[data-op]")?.dataset.op;
    try {
      if (op === "note") { await api.post(`/api/research/${id}/note`); toast("Saved to your notes (#research)."); }
      if (op === "delete" && confirm("Delete this saved research? (Your notes aren't affected.)")) {
        await api.del(`/api/research/${id}`); loadResearch();
      }
    } catch (err) { toast(err.message, true); }
  });

  // ------------------------------------------------------------ music card
  let musicTimer = null;
  async function loadMusic() {
    let s;
    try { s = await api.get("/api/music"); } catch { $("#music-sub").textContent = "Unavailable"; return; }
    const np = s.now_playing;
    const art = $("#music-art");
    if (np?.art) { art.src = np.art; art.hidden = false; } else art.hidden = true;
    $("#music-title").textContent = np?.title || "Nothing playing";
    $("#music-sub").textContent = np
      ? `${np.artist || ""}${np.artist && np.app ? " · " : ""}${np.app || ""}${np.playing ? "" : " · paused"}`
      : (s.spotify_connected ? "Spotify connected" : "Spotify, YouTube, any player");
    $("#music-card").classList.toggle("playing", !!np?.playing);
    if (s.error) $("#music-sub").title = s.error;
  }
  $("#music-card").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-music]"); if (!b) return;
    try { await api.post(`/api/music/${b.dataset.music}`); } catch (err) { toast(err.message, true); }
    setTimeout(loadMusic, 300);
  });
  function startMusicPolling() {
    clearInterval(musicTimer);
    loadMusic();
    // Asks Windows what's playing; only while the page is visible.
    musicTimer = setInterval(() => { if (!document.hidden) loadMusic(); }, 20000);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadMusic(); });

  // ------------------------------------------------------------ settings > integrations
  async function loadIntegrations() {
    const [i, s] = await Promise.all([api.get("/api/integrations"), api.get("/api/settings")]);
    $("#sp-redirect").textContent = i.spotify.redirect_uri;
    $("#sp-client").value = i.spotify.client_id || "";
    $("#sp-status").innerHTML = i.spotify.connected ? '<b class="ok">Connected.</b> "Play Bohemian Rhapsody" starts it directly (Premium).'
      : i.spotify.client_id ? "Client ID saved. Press Connect and log in to Spotify." : "Not connected.";
    $("#sp-connect").hidden = i.spotify.connected;
    $("#sp-disconnect").hidden = !i.spotify.connected;
    $("#music-pause-voice").checked = s.music_pause_for_voice !== false;
    $("#ics-url").value = s.calendar_ics_url || "";
    $("#ics-status").innerHTML = !i.calendar.url_set ? '<span class="muted">No calendar linked.</span>'
      : i.calendar.error ? `<span class="err">Couldn't read it: ${esc(i.calendar.error)}</span>`
      : i.calendar.fetched ? `<span class="ok">Linked · ${i.calendar.events} events</span>` : '<span class="muted">Linked · press Refresh to check it.</span>';
    const g = i.github;
    $("#gh-status").innerHTML = [
      [g.git, "Git installed", "Git isn't installed (git-scm.com)"],
      [g.gh, "GitHub CLI installed", "GitHub CLI not installed (optional: cli.github.com)"],
      [g.gh_signed_in, "Signed in to GitHub CLI", "Not signed in to gh (public repos still work)"],
      [!!g.babilious_remote, `Babilious remote: ${g.babilious_remote || ""}`, "Babilious has no GitHub remote"],
    ].map(([ok, yes, no]) => `<li><span>${ok ? "✅" : "▫️"}</span><span class="grow">${esc(ok ? yes : no)}</span></li>`).join("");
    const sel = $("#vision-model");
    sel.innerHTML = '<option value="">Off (read text with OCR only)</option>' +
      i.vision.installed.map((n) => `<option>${esc(n)}</option>`).join("");
    if (i.vision.model && !i.vision.installed.includes(i.vision.model)) sel.innerHTML += `<option>${esc(i.vision.model)}</option>`;
    sel.value = i.vision.model || "";
    $("#vision-note").textContent = i.vision.installed.length
      ? "Uses the vision model only when you ask him to describe something. It loads into RAM for about a minute."
      : "No vision model installed. Download moondream (1.7 GB, light) or qwen2.5vl:3b (heavier, better) in Settings > Models.";
    $("#research-depth").value = s.research_depth || "normal";
  }
  const save = async (patch, msg = "Saved.") => {
    try { await api.patch("/api/settings", patch); toast(msg); loadIntegrations(); }
    catch (err) { toast(err.message, true); }
  };
  $("#sp-save").addEventListener("click", () => save({ spotify_client_id: $("#sp-client").value.trim() }));
  $("#sp-copy").addEventListener("click", () => navigator.clipboard?.writeText($("#sp-redirect").textContent).then(() => toast("Copied.")));
  $("#sp-connect").addEventListener("click", async () => {
    const id = $("#sp-client").value.trim();
    if (!id) { toast("Paste your Spotify app's Client ID first.", true); return; }
    try { await api.patch("/api/settings", { spotify_client_id: id }); } catch (err) { toast(err.message, true); return; }
    window.open("/api/spotify/login", "spotify-login", "width=520,height=760");
    const t = setInterval(async () => {
      const i = await api.get("/api/integrations").catch(() => null);
      if (i?.spotify.connected) { clearInterval(t); toast("Spotify connected."); loadIntegrations(); loadMusic(); }
    }, 2500);
    setTimeout(() => clearInterval(t), 180000);
  });
  $("#sp-disconnect").addEventListener("click", async () => {
    await api.post("/api/spotify/disconnect"); toast("Spotify disconnected (token deleted)."); loadIntegrations();
  });
  $("#music-pause-voice").addEventListener("change", (e) => save({ music_pause_for_voice: e.target.checked }));
  $("#ics-save").addEventListener("click", () => save({ calendar_ics_url: $("#ics-url").value.trim() }, "Calendar link saved."));
  $("#ics-refresh").addEventListener("click", async () => {
    try {
      const r = await api.post("/api/calendar/refresh");
      toast(r.error ? `Calendar: ${r.error}` : `Calendar read: ${r.events} events.`, !!r.error);
      loadIntegrations();
    } catch (err) { toast(err.message, true); }
  });
  $("#vision-model").addEventListener("change", (e) => save({ vision_model: e.target.value },
    e.target.value ? `Vision model: ${e.target.value}` : "Vision model off."));
  $("#research-depth").addEventListener("change", (e) => save({ research_depth: e.target.value }));

  return { loadResearch, loadIntegrations, loadMusic, startMusicPolling };
}
