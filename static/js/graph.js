// Document map: a small force-directed layout on a <canvas>.
// The simulation runs once per load (not continuously), so it costs no idle CPU.
import { api } from "./api.js";

const COLORS = { ".md": "#a78bfa", ".txt": "#a78bfa", ".pdf": "#f87171", ".docx": "#60a5fa", ".py": "#34d399",
  ".ipynb": "#34d399", ".js": "#fbbf24", ".ts": "#fbbf24", ".m": "#fb923c" };
const color = (ext) => COLORS[ext] || "#9a9aa5";

export function initGraph({ onOpen }) {
  const canvas = document.getElementById("graph");
  const tip = document.getElementById("graph-tip");
  const empty = document.getElementById("graph-empty");
  const ctx = canvas.getContext("2d");
  let nodes = [], edges = [], hover = null, drag = null, view = { x: 0, y: 0 };

  function layout() {
    const W = canvas.clientWidth, H = canvas.clientHeight;
    const byId = new Map(nodes.map((n) => [n.id, n]));
    nodes.forEach((n, i) => {
      const a = (i / nodes.length) * Math.PI * 2;
      n.x = W / 2 + Math.cos(a) * W * 0.3 + (Math.random() - 0.5) * 20;
      n.y = H / 2 + Math.sin(a) * H * 0.3 + (Math.random() - 0.5) * 20;
      n.deg = 0;
    });
    const links = edges.map((e) => ({ s: byId.get(e.source), t: byId.get(e.target), w: e.weight })).filter((l) => l.s && l.t);
    links.forEach((l) => { l.s.deg++; l.t.deg++; });
    const k = Math.sqrt((W * H) / Math.max(nodes.length, 1)) * 0.38;
    for (let step = 0, temp = W / 10; step < 260; step++, temp *= 0.97) {
      for (const n of nodes) { n.dx = 0; n.dy = 0; }
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          const d2 = Math.max(dx * dx + dy * dy, 0.01), f = (k * k) / d2;
          a.dx += dx * f; a.dy += dy * f; b.dx -= dx * f; b.dy -= dy * f;
        }
      }
      for (const l of links) {
        const dx = l.s.x - l.t.x, dy = l.s.y - l.t.y, d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const f = (d / k) * Math.min(1, 0.3 + l.w / 10);
        l.s.dx -= dx * f; l.s.dy -= dy * f; l.t.dx += dx * f; l.t.dy += dy * f;
      }
      for (const n of nodes) {
        // Gravity toward the centre (stronger for unlinked nodes so they don't drift to the edges).
        const g = n.deg ? 0.6 : 1.2;
        n.dx += (W / 2 - n.x) * g; n.dy += (H / 2 - n.y) * g * (W / H);
        const d = Math.sqrt(n.dx * n.dx + n.dy * n.dy) || 1;
        n.x = Math.min(W - 12, Math.max(12, n.x + (n.dx / d) * Math.min(d, temp)));
        n.y = Math.min(H - 12, Math.max(12, n.y + (n.dy / d) * Math.min(d, temp)));
      }
    }
    return links;
  }

  let links = [];
  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
    ctx.setTransform(dpr, 0, 0, dpr, view.x * dpr, view.y * dpr);
    ctx.clearRect(-view.x, -view.y, W, H);
    for (const l of links) {
      const hot = hover && (l.s === hover || l.t === hover);
      ctx.strokeStyle = hot ? "#a78bfa" : "#3a3a44";
      ctx.lineWidth = hot ? 2 : 1;
      ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke();
    }
    for (const n of nodes) {
      const r = 4 + Math.min(n.deg, 10) * 0.8;
      ctx.fillStyle = color(n.ext);
      ctx.globalAlpha = hover && hover !== n && !links.some((l) => (l.s === hover && l.t === n) || (l.t === hover && l.s === n)) ? 0.35 : 1;
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill();
      if (n.deg >= 3 || n === hover) {
        ctx.fillStyle = "#cfcfd8"; ctx.font = "11px Segoe UI, sans-serif";
        ctx.fillText(n.name.length > 26 ? n.name.slice(0, 24) + "…" : n.name, n.x + r + 3, n.y + 4);
      }
      ctx.globalAlpha = 1;
    }
  }

  function nodeAt(ev) {
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left - view.x, y = ev.clientY - rect.top - view.y;
    let best = null, bd = 14 * 14;
    for (const n of nodes) {
      const d = (n.x - x) ** 2 + (n.y - y) ** 2;
      if (d < bd) { bd = d; best = n; }
    }
    return best;
  }
  canvas.addEventListener("mousemove", (ev) => {
    if (drag) {
      view.x = drag.vx + ev.clientX - drag.x; view.y = drag.vy + ev.clientY - drag.y;
      draw(); return;
    }
    const n = nodeAt(ev);
    if (n !== hover) { hover = n; draw(); }
    if (n) {
      const rect = canvas.getBoundingClientRect();
      tip.hidden = false;
      tip.style.left = Math.min(ev.clientX - rect.left + 12, rect.width - 290) + "px";
      tip.style.top = ev.clientY - rect.top + 12 + "px";
      tip.textContent = `${n.name} · ${n.deg} connection${n.deg === 1 ? "" : "s"}${n.keywords.length ? " · " + n.keywords.join(", ") : ""}`;
      canvas.style.cursor = "pointer";
    } else { tip.hidden = true; canvas.style.cursor = "grab"; }
  });
  canvas.addEventListener("mouseleave", () => { hover = null; tip.hidden = true; drag = null; draw(); });
  canvas.addEventListener("mousedown", (ev) => { if (!nodeAt(ev)) drag = { x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y }; });
  window.addEventListener("mouseup", () => { drag = null; });
  canvas.addEventListener("click", (ev) => { const n = nodeAt(ev); if (n) onOpen(n.id); });

  return {
    async load() {
      const g = await api.get("/api/graph?limit=150");
      nodes = g.nodes; edges = g.edges; view = { x: 0, y: 0 };
      empty.hidden = nodes.length > 0;
      if (!nodes.length) { ctx.clearRect(0, 0, canvas.width, canvas.height); return; }
      requestAnimationFrame(() => { links = layout(); draw(); });
    },
  };
}
