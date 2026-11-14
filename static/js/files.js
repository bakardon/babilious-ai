// Files view: folders, indexing progress, search and document details.
import { api } from "./api.js";

const $ = (sel, root = document) => root.querySelector(sel);

export function initFiles({ esc, toast, when, send, show }) {
  const MARK = /\u0002([\s\S]*?)\u0003/g;
  const snippet = (s) => esc(s || "").replace(MARK, "<mark>$1</mark>");
  const size = (b) => (b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1e3)) + " KB");
  let pollTimer = null;
  let searchTimer = null;
  let selectedId = null;

  // ------------------------------------------------------------ folders & indexing
  async function loadFolders() {
    const { folders, job, stats } = await api.get("/api/folders");
    $("#folder-list").innerHTML = folders.map((f) => `
      <li data-id="${f.id}">
        <span class="ico">📁</span>
        <div class="grow"><b>${esc(f.path)}</b>
          <div class="sub">${f.file_count} files · ${f.content_count} readable ·
            ${f.last_indexed_at ? "indexed " + when(f.last_indexed_at) : "not indexed yet"}
            ${f.note ? " · ⚠ " + esc(f.note) : ""}</div></div>
        <button class="ghost-btn" data-act="reindex">↻</button>
        <button class="ghost-btn danger" data-act="remove" title="Forget this folder (files stay on disk)">Remove</button>
      </li>`).join("") || '<li class="muted">No folders yet. Add one, like your notes or projects folder.</li>';
    $("#index-stats").textContent = stats.files
      ? `${stats.files} files indexed, ${stats.with_text} with readable text (${stats.chunks} passages)` +
        (stats.unreadable ? `, ${stats.unreadable} unreadable (scanned PDFs, locked files...)` : "") +
        (stats.embedded_chunks ? `, ${stats.embedded_chunks} with semantic vectors` : "")
      : "";
    renderJob(job);
  }

  function renderJob(job) {
    const box = $("#index-status");
    if (job.running) {
      box.hidden = false;
      $("#index-status-text").textContent =
        `${job.message || "Indexing..."} ${job.scanned || 0} scanned, ${job.indexed || 0} read` +
        (job.current ? ` · ${job.current}` : "");
      clearTimeout(pollTimer);
      pollTimer = setTimeout(pollJob, 1000);
    } else {
      box.hidden = true;
      if (pollTimer) { // just finished
        pollTimer = null;
        if (job.message) toast(job.message);
        loadFolders();
        runSearch();
      }
    }
  }
  async function pollJob() {
    try { renderJob(await api.get("/api/index/status")); } catch { /* server restarting */ }
  }

  async function addFolder(path) {
    try {
      const f = await api.post("/api/folders", { path });
      toast(`Added ${f.path}. Indexing...`);
      $("#folder-path").value = "";
      pollTimer = 1; // so the finish is noticed
      loadFolders();
    } catch (err) { toast(err.message, true); }
  }

  $("#pick-folder").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = "Choose in the dialog...";
    try {
      const { path } = await api.post("/api/folders/pick");
      if (path) await addFolder(path);
    } catch (err) { toast(err.message, true); }
    finally { btn.disabled = false; btn.textContent = "＋ Add folder"; }
  });
  $("#folder-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const p = $("#folder-path").value.trim();
    if (p) addFolder(p);
  });
  $("#reindex-all").addEventListener("click", async () => {
    const r = await api.post("/api/index", {});
    if (!r.started) toast("Already indexing.");
    pollTimer = 1; renderJob(r.job);
  });
  $("#cancel-index").addEventListener("click", () => api.post("/api/index/cancel"));
  $("#folder-list").addEventListener("click", async (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    const li = e.target.closest("li[data-id]");
    if (!act || !li) return;
    try {
      if (act === "reindex") {
        const r = await api.post("/api/index", { folder_id: Number(li.dataset.id) });
        pollTimer = 1; renderJob(r.job);
      } else if (act === "remove") {
        if (!confirm("Remove this folder from Babilious' index? Your files are NOT deleted; only the index is.")) return;
        await api.del(`/api/folders/${li.dataset.id}`);
        toast("Folder removed from the index."); loadFolders(); runSearch();
      }
    } catch (err) { toast(err.message, true); }
  });

  // ------------------------------------------------------------ search
  async function runSearch() {
    const q = $("#file-q").value.trim();
    const params = new URLSearchParams({ q, ext: $("#file-ext").value, sort: q ? $("#file-sort").value : "modified", limit: "60" });
    const { results } = await api.get(`/api/files/search?${params}`);
    $("#file-results").innerHTML = results.map((r) => `
      <li data-id="${r.id}" class="${r.id === selectedId ? "selected" : ""}">
        <span class="ext">${esc((r.ext || "?").replace(".", "")) || "?"}</span>
        <div class="grow"><b>${esc(r.name)}</b>
          <div class="path">${esc(r.path)}</div>
          <div class="sub">${when(r.modified)} · ${size(r.size)}${r.error ? " · ⚠ " + esc(r.error) : ""}${r.match ? " · " + r.match + " match" : ""}</div>
          ${r.snippet ? `<div class="snippet">${snippet(r.snippet)}</div>` : ""}</div>
      </li>`).join("") || `<li class="muted">${q ? "No matches." : "Nothing indexed yet."}</li>`;
  }
  ["input", "change"].forEach((ev) => ["#file-q", "#file-ext", "#file-sort"].forEach((s) =>
    $(s).addEventListener(ev, () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 200); })));
  $("#file-results").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (li) openDocument(Number(li.dataset.id));
  });

  // ------------------------------------------------------------ document detail
  async function openDocument(id) {
    selectedId = id;
    const panel = $("#doc-detail");
    let d;
    try { d = await api.get(`/api/documents/${id}`); }
    catch (err) { toast(err.message, true); return; }
    document.querySelectorAll("#file-results li").forEach((li) => li.classList.toggle("selected", Number(li.dataset.id) === id));
    panel.hidden = false;
    $(".files-layout").classList.add("with-detail");
    panel.innerHTML = `
      <div class="block-head"><h4>${esc(d.name)}</h4><button class="icon-btn" data-close>✕</button></div>
      <div class="path">${esc(d.path)}</div>
      <div class="sub muted small">${when(d.modified)} · ${size(d.size)} · ${d.chunks} passages</div>
      <div class="actions">
        ${d.has_text ? '<button class="primary" data-summarise>Summarise in chat</button>' : ""}
        <button class="ghost-btn" data-copy>Copy path</button>
      </div>
      ${d.error ? `<p class="muted">⚠ Couldn't read the contents: ${esc(d.error)}</p>` : ""}
      ${d.keywords.length ? `<div>${d.keywords.map((k) => `<span class="kw">${esc(k)}</span>`).join("")}</div>` : ""}
      ${d.preview ? `<h4 style="margin-top:12px">Preview</h4><pre>${esc(d.preview)}</pre>` : ""}
      <h4 style="margin-top:12px">Related</h4>
      ${d.related.length ? d.related.map((r) => `<button class="rel" data-rel="${r.id}">🔗 ${esc(r.name)}
        <span class="muted small">${r.kind === "link" ? "linked" : "shares " + r.shared.map(esc).join(", ")}</span></button>`).join("")
        : '<p class="muted small">No related documents found.</p>'}`;
    panel.onclick = async (e) => {
      if (e.target.closest("[data-close]")) { panel.hidden = true; $(".files-layout").classList.remove("with-detail"); selectedId = null; }
      if (e.target.closest("[data-copy]")) {
        try { await navigator.clipboard.writeText(d.path); toast("Path copied."); } catch { toast("Couldn't copy.", true); }
      }
      if (e.target.closest("[data-summarise]")) { show("chat"); send(`Summarise ${d.name}`); }
      const rel = e.target.closest("[data-rel]");
      if (rel) openDocument(Number(rel.dataset.rel));
    };
  }

  return {
    load: () => { loadFolders(); runSearch(); },
    openDocument: async (id) => { show("files"); await openDocument(id); },
  };
}
