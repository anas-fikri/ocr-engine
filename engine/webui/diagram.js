/* Renderer diagram SVG sederhana (tanpa library, jalan offline): flowchart grid dan sequence diagram.
 * Dipakai guide.html. Teks node memakai "\n" untuk ganti baris. */
"use strict";

const PQEDiagram = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const LINE = 15;

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  }

  function label(parent, x, y, str, cls, anchor = "middle") {
    const lines = String(str).split("\n");
    const t = el("text", { x, y: y - ((lines.length - 1) * LINE) / 2, class: cls, "text-anchor": anchor, "dominant-baseline": "middle" }, parent);
    lines.forEach((ln, i) => { const s = el("tspan", { x, dy: i ? LINE : 0 }, t); s.textContent = ln; });
    return t;
  }

  function tag(parent, x, y, str, anchor = "start") { // label kecil di garis, dengan latar agar terbaca
    const g = el("g", {}, parent);
    const t = label(g, x, y, str, "dg-edge-label", anchor);
    requestAnimationFrame(() => {
      try {
        const b = t.getBBox();
        const r = el("rect", { x: b.x - 3, y: b.y - 1, width: b.width + 6, height: b.height + 2, rx: 3, class: "dg-edge-bg" });
        g.insertBefore(r, t);
      } catch { /* belum ter-render */ }
    });
  }

  function svgRoot(container, w, h, title) {
    const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": title, class: "dg" });
    svg.style.width = "100%";
    svg.style.minWidth = Math.min(w, 760) + "px";
    const defs = el("defs", {}, svg);
    const m = el("marker", { id: `arrow-${container.id}`, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
    el("path", { d: "M0,0 L10,5 L0,10 z", class: "dg-arrow" }, m);
    container.innerHTML = "";
    container.appendChild(svg);
    return svg;
  }

  /* ---------- flowchart pada grid kolom/baris ---------- */
  function flow(container, spec) {
    const colW = spec.colW || 210, rowH = spec.rowH || 86, bw = spec.boxW || 180, bh = spec.boxH || 54, lane = 30;
    const rows = Math.max(...Object.values(spec.nodes).map((n) => n.r)) + 1;
    const W = spec.cols * colW + lane * 2, H = rows * rowH + 10;
    const svg = svgRoot(container, W, H, spec.title || "diagram");
    const marker = `url(#arrow-${container.id})`;
    const N = {};
    for (const [id, n] of Object.entries(spec.nodes)) {
      const w = n.w || bw, h = n.h || bh;
      N[id] = { ...n, w, h, x: lane + n.c * colW + colW / 2, y: 8 + n.r * rowH + h / 2 };
    }
    const gEdges = el("g", {}, svg), gNodes = el("g", {}, svg), gLabels = el("g", {}, svg);
    const side = (n, dir) => ({ l: [n.x - n.w / 2, n.y], r: [n.x + n.w / 2, n.y], t: [n.x, n.y - n.h / 2], b: [n.x, n.y + n.h / 2] }[dir]);

    for (const e of spec.edges) {
      const a = N[e.f], b = N[e.t];
      let pts, lp, anchor = "start";
      const route = e.route || (a.c === b.c ? (b.r > a.r ? "v" : "right") : a.r === b.r ? "h" : a.k === "dec" ? "hv" : "vh");
      if (route === "v") {
        pts = [side(a, "b"), side(b, "t")]; lp = [a.x + 6, a.y + a.h / 2 + 12];
      } else if (route === "h") {
        const ltr = b.x > a.x, p0 = side(a, ltr ? "r" : "l"), p1 = side(b, ltr ? "l" : "r");
        pts = [p0, [p1[0], Math.abs(p0[1] - p1[1]) < b.h / 2 - 4 ? p0[1] : p1[1]]]; // tetap lurus walau tinggi node beda
        lp = [(pts[0][0] + pts[1][0]) / 2, a.y - 10]; anchor = "middle";
      } else if (route === "hv") {
        if (Math.abs(b.x - a.x) < a.w / 2 - 14) { // target tepat di bawah node lebar: turun lurus
          pts = [[b.x, a.y + a.h / 2], side(b, "t")]; lp = [b.x + 6, a.y + a.h / 2 + 12];
        } else {
          const p0 = side(a, b.x > a.x ? "r" : "l");
          pts = [p0, [b.x, p0[1]], side(b, "t")]; lp = [b.x + 6, (p0[1] + b.y - b.h / 2) / 2];
        }
      } else if (route === "vh") {
        const p0 = side(a, "b");
        if (Math.abs(p0[0] - b.x) < b.w / 2 - 10) {
          pts = [p0, [p0[0], b.y - b.h / 2]];
        } else {
          const p2 = side(b, b.x > a.x ? "l" : "r");
          pts = [p0, [p0[0], p2[1]], p2];
        }
        lp = [p0[0] + 6, p0[1] + 12];
      } else { // "left" / "right": lewat jalur pinggir, untuk panah kembali ke atas
        const x = route === "left" ? lane / 2 : W - lane / 2, d = route === "left" ? "l" : "r";
        const p0 = side(a, d), p3 = side(b, d);
        pts = [p0, [x, p0[1]], [x, p3[1]], p3]; lp = [p3[0] + (route === "left" ? -8 : 8), p3[1] - 10];
        anchor = route === "left" ? "end" : "start";
      }
      el("polyline", { points: pts.map((p) => p.join(",")).join(" "), class: "dg-edge" + (e.dash ? " dash" : ""), "marker-end": marker }, gEdges);
      if (e.label) tag(gLabels, lp[0], lp[1], e.label, anchor);
    }

    for (const n of Object.values(N)) {
      const g = el("g", { class: `dg-node ${n.k || "step"} ${n.tone || ""}` }, gNodes);
      const x0 = n.x - n.w / 2, y0 = n.y - n.h / 2;
      if (n.k === "dec") {
        const k = 14;
        el("polygon", { points: [[x0 + k, y0], [x0 + n.w - k, y0], [x0 + n.w, n.y], [x0 + n.w - k, y0 + n.h], [x0 + k, y0 + n.h], [x0, n.y]].map((p) => p.join(",")).join(" ") }, g);
      } else {
        el("rect", { x: x0, y: y0, width: n.w, height: n.h, rx: n.k === "start" || n.k === "end" ? n.h / 2 : 8 }, g);
      }
      label(g, n.x, n.y, n.t, "dg-text");
    }
  }

  /* ---------- sequence diagram ---------- */
  function seq(container, spec) {
    const colW = spec.colW || 190, head = 56, step = 38, padX = 20;
    const W = spec.actors.length * colW + padX * 2;
    const H = head + spec.steps.reduce((s, x) => s + (x.note ? 44 : x.self != null ? 46 : step), 0) + 40;
    const svg = svgRoot(container, W, H, spec.title || "diagram");
    const marker = `url(#arrow-${container.id})`;
    const X = (i) => padX + i * colW + colW / 2;
    spec.actors.forEach((a, i) => {
      el("line", { x1: X(i), y1: head, x2: X(i), y2: H - 8, class: "dg-life" }, svg);
      const g = el("g", { class: "dg-node actor" }, svg);
      el("rect", { x: X(i) - colW / 2 + 10, y: 4, width: colW - 20, height: head - 12, rx: 8 }, g);
      label(g, X(i), 4 + (head - 12) / 2, a, "dg-text");
    });
    let y = head + 22;
    for (const s of spec.steps) {
      if (s.note) {
        const [a, b] = s.span, x0 = X(Math.min(a, b)) - colW / 2 + 16, x1 = X(Math.max(a, b)) + colW / 2 - 16;
        const g = el("g", { class: `dg-node note ${s.tone || ""}` }, svg);
        el("rect", { x: x0, y: y - 14, width: x1 - x0, height: 32, rx: 6 }, g);
        label(g, (x0 + x1) / 2, y + 2, s.note, "dg-text");
        y += 44;
      } else if (s.self != null) {
        const x = X(s.self);
        el("polyline", { points: `${x},${y - 6} ${x + 34},${y - 6} ${x + 34},${y + 14} ${x + 3},${y + 14}`, class: "dg-edge", "marker-end": marker }, svg);
        label(svg, x + 42, y + 4, s.text, "dg-seq-label", "start");
        y += 46;
      } else {
        const x1 = X(s.f), x2 = X(s.t);
        el("line", { x1, y1: y, x2: x2 + (x2 > x1 ? -3 : 3), y2: y, class: "dg-edge" + (s.dash ? " dash" : ""), "marker-end": marker }, svg);
        label(svg, (x1 + x2) / 2, y - 10, s.text, "dg-seq-label");
        y += step;
      }
    }
  }

  return { flow, seq };
})();
