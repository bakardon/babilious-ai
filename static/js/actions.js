// Action cards (approve / cancel / results) and the Automation view.
import { api } from "./api.js";

const $ = (s, r = document) => r.querySelector(s);

const STATUS = {
  pending: "Waiting for your OK", completed: "Done", failed: "Failed", rejected: "Cancelled",
  expired: "Expired", running: "Running...", blocked: "Blocked",
};

export function initActions({ esc, toast, when, dog, show, send }) {
  function card(a) {
    const shot = a.data?.screenshot ? `<a href="/api/screenshots/${esc(a.data.screenshot)}" target="_blank" rel="noopener">
        <img class="ac-shot" src="/api/screenshots/${esc(a.data.screenshot)}" alt="Screenshot"></a>` : "";
    const out = a.output && a.status !== "pending"
      ? `<details class="ac-out" ${a.status === "failed" ? "open" : ""}><summary>Output</summary><pre>${esc(a.output)}</pre></details>` : "";
    const buttons = a.status === "pending"
      ? `<div class="ac-buttons"><button class="primary ${a.risk === "danger" ? "danger-btn" : ""}" data-approve>Approve</button>
         <button class="ghost-btn" data-reject>Cancel</button></div>` : "";
    return `<div class="action-card risk-${esc(a.risk)} st-${esc(a.status)}" data-action="${a.id}">
      <div class="ac-head"><span class="ac-badge">${STATUS[a.status] || esc(a.status)}</span>
        ${a.origin === "model" ? '<span class="ac-origin" title="The AI model suggested this">AI suggestion</span>' : ""}
        <span class="ac-preview">${esc(a.preview)}</span></div>
      ${a.status === "pending" && a.reason ? `<div class="ac-reason">${a.risk === "danger" ? "⚠️ " : ""}${esc(a.reason)}</div>` : ""}
      ${a.summary && a.status !== "pending" ? `<div class="ac-summary">${esc(a.summary)}</div>` : ""}
      ${shot}${out}${buttons}</div>`;
  }

  async function decide(el, approve) {
    const id = el.dataset.action;
    el.querySelectorAll("button").forEach((b) => (b.disabled = true));
    if (approve) { el.querySelector(".ac-badge").textContent = "Running..."; dog.set("thinking", { label: "Working on it..." }); }
    try {
      const a = await api.post(`/api/actions/${id}/${approve ? "approve" : "reject"}`);
      el.outerHTML = card(a);
      if (approve) dog.set(a.status === "completed" ? "happy" : "error", { holdMs: 4000, label: a.summary.slice(0, 80) });
      refreshPending();
    } catch (err) {
      toast(err.message, true);
      el.querySelectorAll("button").forEach((b) => (b.disabled = false));
      dog.set("idle");
    }
  }

  document.addEventListener("click", (e) => {
    const el = e.target.closest(".action-card");
    if (!el) return;
    if (e.target.closest("[data-approve]")) decide(el, true);
    if (e.target.closest("[data-reject]")) decide(el, false);
  });

  // ------------------------------------------------------------ automation view
  async function refreshPending() {
    const { actions } = await api.get("/api/actions?status=pending");
    const badge = document.querySelector('.rail-btn[data-view="automation"]');
    badge?.classList.toggle("has-badge", actions.length > 0);
    badge?.setAttribute("data-badge", String(actions.length));
    const list = $("#pending-actions");
    if (list) list.innerHTML = actions.map(card).join("") || '<p class="muted">Nothing waiting for approval.</p>';
  }

  async function load() {
    await refreshPending();
    const [{ actions }, info] = await Promise.all([api.get("/api/actions?limit=40"), api.get("/api/automation")]);
    $("#action-history").innerHTML = actions.filter((a) => a.status !== "pending").map((a) => `
      <li><span class="tag ${a.status === "completed" ? "completed" : a.status === "failed" ? "failed" : "info"}">${esc(a.status)}</span>
        <div class="grow"><div>${esc(a.preview)}</div><div class="sub">${esc(a.summary || "")}</div>
        ${a.output ? `<details><summary class="sub">Output</summary><pre class="mini-pre">${esc(a.output)}</pre></details>` : ""}</div>
        <span class="muted small">${a.origin === "model" ? "AI · " : ""}${when(a.created_at)}</span></li>`).join("")
      || '<li class="muted">No actions yet. Try "open VS Code" or "what\'s in my Downloads folder?".</li>';
    $("#app-list").innerHTML = info.apps.map((a) => `
      <li><span class="${a.installed ? "dot-ok" : "dot-off"}"></span><div class="grow"><b>${esc(a.name)}</b>
        <span class="muted small">say "open ${esc(a.say)}"</span>${a.path ? `<div class="sub">${esc(a.path)}</div>` : ""}</div>
        ${a.custom ? `<button class="icon-btn" data-del-app="${esc(a.say)}" title="Remove">🗑</button>` : ""}</li>`).join("");
    $("#project-list").innerHTML = Object.entries(info.projects).map(([n, p]) => `
      <li><div class="grow"><b>${esc(n)}</b><div class="sub">${esc(p)}</div></div>
        <button class="icon-btn" data-del-project="${esc(n)}" title="Remove shortcut">🗑</button></li>`).join("");
    $("#default-folder").value = info.custom_default_folder || "";
    $("#default-folder").placeholder = info.default_folder;
    $("#model-routing").checked = !!info.model_tool_routing;
    window.__automation = info;
  }

  async function saveMap(key, map, msg) {
    try { await api.patch("/api/settings", { [key]: map }); toast(msg); load(); }
    catch (err) { toast(err.message, true); }
  }
  $("#app-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const name = $("#app-name").value.trim().toLowerCase(), path = $("#app-path").value.trim().replace(/^"|"$/g, "");
    const current = Object.fromEntries((window.__automation?.apps || []).filter((a) => a.custom).map((a) => [a.say, a.path]));
    saveMap("custom_apps", { ...current, [name]: path }, `Say "open ${name}" to launch it.`);
    e.target.reset();
  });
  $("#project-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const saved = {};  // only user-saved ones are stored; auto-detected ones aren't
    for (const [n, p] of Object.entries(window.__automation?.projects || {})) if (n !== "babilious") saved[n] = p;
    saveMap("projects", { ...saved, [$("#project-name").value.trim().toLowerCase()]: $("#project-path").value.trim().replace(/^"|"$/g, "") },
      "Project saved.");
    e.target.reset();
  });
  document.addEventListener("click", (e) => {
    const app = e.target.closest("[data-del-app]");
    if (app) {
      const current = Object.fromEntries((window.__automation?.apps || []).filter((a) => a.custom && a.say !== app.dataset.delApp).map((a) => [a.say, a.path]));
      saveMap("custom_apps", current, "App removed.");
    }
    const proj = e.target.closest("[data-del-project]");
    if (proj) {
      const saved = {};
      for (const [n, p] of Object.entries(window.__automation?.projects || {})) if (n !== "babilious" && n !== proj.dataset.delProject) saved[n] = p;
      saveMap("projects", saved, "Shortcut removed (the folder itself is untouched).");
    }
    const q = e.target.closest("[data-quick]");
    if (q) { show("chat"); send(q.dataset.quick); }
  });
  $("#default-folder").addEventListener("change", (e) =>
    api.patch("/api/settings", { default_folder: e.target.value.trim() }).then(() => toast("Saved.")).catch((err) => toast(err.message, true)));
  $("#model-routing").addEventListener("change", (e) =>
    api.patch("/api/settings", { model_tool_routing: e.target.checked }).then(() => toast("Saved.")).catch((err) => toast(err.message, true)));

  return { card, load, refreshPending };
}
