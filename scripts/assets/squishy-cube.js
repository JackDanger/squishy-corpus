/* squishy-cube.js — a dependency-free 3D scatter of the Squishy corpus.
 *
 * Each dataset is a point positioned by three INTRINSIC, codec-free byte properties:
 *   x = entropy (bits/byte, 0..8 — how random the bytes look)
 *   y = repeat coverage (0..1 — how much of the file exactly recurs)
 *   z = match distance (bytes, log — how far back the repeats sit)
 * coloured by category, sized by file size. A translucent plane shows the
 * compressibility threshold K = coverage + (8−entropy)/8 that gates the Squishy
 * Score: points above it are scored, points below (entropy-coded media) are kept as
 * diagnostics and drawn hollow. Data is generated into cube-data.json by
 * scripts/build-provenance.py — this file only renders it.
 *
 *   SquishyCube.mount(canvasEl, data, {legendEl, tooltipEl})
 */
(function (global) {
  "use strict";

  function normalize(v, ax) {
    const t = ax.log ? Math.log10(v) : v;
    const lo = ax.log ? Math.log10(ax.min) : ax.min;
    const hi = ax.log ? Math.log10(ax.max) : ax.max;
    return ((t - lo) / (hi - lo)) * 2 - 1; // → [-1, 1]
  }

  // 3x3 rotation for yaw (Y) then pitch (X), applied to a [-1,1] cube point.
  function rotate(p, yaw, pitch) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    const cx = Math.cos(pitch), sx = Math.sin(pitch);
    let x = p[0] * cy + p[2] * sy;
    let z = -p[0] * sy + p[2] * cy;
    let y = p[1] * cx - z * sx;
    z = p[1] * sx + z * cx;
    return [x, y, z];
  }

  function mount(canvas, data, opts) {
    opts = opts || {};
    const ctx = canvas.getContext("2d");
    const cats = data.categories;        // {name: "#rrggbb"}
    const ax = data.axes;                // {x:{label,min,max,log}, y:..., z:...}
    // pre-normalize every point into the unit cube once
    const pts = data.points.map(function (d) {
      return {
        d: d,
        c: [normalize(d.x, ax.x), -normalize(d.y, ax.y), normalize(d.z, ax.z)],
        color: cats[d.cat] || "#9aa",
      };
    });

    let yaw = -0.6, pitch = -0.35, dist = 3.4, auto = true;
    let W = 0, H = 0, cx = 0, cy = 0, scale = 1, hover = null;

    function resize() {
      const dpr = global.devicePixelRatio || 1;
      const r = canvas.getBoundingClientRect();
      W = r.width; H = r.height;
      canvas.width = W * dpr; canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cx = W / 2; cy = H / 2; scale = Math.min(W, H) * 0.32;
    }

    function project(c3) {
      const r = rotate(c3, yaw, pitch);
      const f = dist / (dist - r[2]);          // simple perspective
      return { x: cx + r[0] * scale * f, y: cy + r[1] * scale * f, z: r[2], f: f };
    }

    function axisLine(a, b, style, width) {
      const pa = project(a), pb = project(b);
      ctx.strokeStyle = style; ctx.lineWidth = width || 1;
      ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
    }

    function drawPlane() {
      // the compressibility gate K = slope_x·entropy + intercept (= coverage boundary),
      // a flat curtain spanning the whole match-distance (z) axis. Everything on the
      // low-coverage / high-entropy side of it is measured but NOT scored.
      const pl = data.plane; if (!pl) return;
      const xMax = ax.x.max, x0 = (0 - pl.intercept) / pl.slope_x;   // entropy where coverage-boundary = 0
      if (x0 >= xMax) return;
      const yMax = pl.slope_x * xMax + pl.intercept;
      const corners = [[x0, 0, ax.z.min], [xMax, yMax, ax.z.min], [xMax, yMax, ax.z.max], [x0, 0, ax.z.max]]
        .map(function (p) { return project([normalize(p[0], ax.x), -normalize(p[1], ax.y), normalize(p[2], ax.z)]); });
      ctx.beginPath(); ctx.moveTo(corners[0].x, corners[0].y);
      for (var i = 1; i < 4; i++) ctx.lineTo(corners[i].x, corners[i].y);
      ctx.closePath();
      ctx.fillStyle = "rgba(204,121,167,0.10)"; ctx.fill();
      ctx.strokeStyle = "rgba(204,121,167,0.55)"; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = "rgba(204,121,167,0.8)"; ctx.font = "11px ui-monospace,Menlo,monospace";
      ctx.fillText("K = " + pl.kmin + " (score ↔ diagnostic)", corners[1].x + 5, corners[1].y);
    }

    function drawCube() {
      // 12 edges of the [-1,1] cube, faint
      const k = 1, V = [[-k,-k,-k],[k,-k,-k],[k,k,-k],[-k,k,-k],[-k,-k,k],[k,-k,k],[k,k,k],[-k,k,k]];
      const E = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
      E.forEach(function (e) { axisLine(V[e[0]], V[e[1]], "rgba(255,255,255,0.06)", 1); });
      // three labelled axes from the back-bottom corner
      ctx.font = "12px -apple-system,Segoe UI,sans-serif";
      const o = [-k,-k,-k];
      [[[k,-k,-k], ax.x.label, "#e06c9a"], [[-k,k,-k], ax.y.label, "#5fbf8f"],
       [[-k,-k,k], ax.z.label, "#5b9bd5"]].forEach(function (t) {
        axisLine(o, t[0], t[2], 1.5);
        const p = project(t[0]);
        ctx.fillStyle = t[2]; ctx.fillText(t[1], p.x + 4, p.y);
      });
    }

    function draw() {
      ctx.clearRect(0, 0, W, H);
      // subtle radial background
      const g = ctx.createRadialGradient(cx, cy, 10, cx, cy, Math.max(W, H) * 0.7);
      g.addColorStop(0, "#10131a"); g.addColorStop(1, "#0a0c11");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      drawCube();
      drawPlane();
      // depth-sort points back→front
      const sp = pts.map(function (p) { return { p: p, s: project(p.c) }; })
                    .sort(function (a, b) { return a.s.z - b.s.z; });
      sp.forEach(function (o) {
        const s = o.s, isH = hover === o.p;
        const scored = o.p.d.scored !== false;
        const depth = (s.z + 1.4) / 2.8;                // 0..1 front
        const rad = (5 + 9 * (o.p.d.r || 0.5)) * s.f * (isH ? 1.5 : 1);
        ctx.globalAlpha = 0.35 + 0.65 * depth;
        if (scored) {
          // glow
          const gg = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, rad * 2.2);
          gg.addColorStop(0, o.p.color); gg.addColorStop(1, "rgba(0,0,0,0)");
          ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(s.x, s.y, rad * 2.2, 0, 7); ctx.fill();
          // core dot
          ctx.fillStyle = o.p.color; ctx.beginPath(); ctx.arc(s.x, s.y, rad, 0, 7); ctx.fill();
        } else {
          // diagnostic (below the compressibility plane): hollow ring, no glow
          ctx.fillStyle = "rgba(20,22,28,0.65)"; ctx.beginPath(); ctx.arc(s.x, s.y, rad, 0, 7); ctx.fill();
          ctx.strokeStyle = o.p.color; ctx.lineWidth = 1.5;
          ctx.setLineDash([3, 2]); ctx.beginPath(); ctx.arc(s.x, s.y, rad, 0, 7); ctx.stroke();
          ctx.setLineDash([]);
        }
        if (o.p.d.scale && scored) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 1; ctx.stroke(); }
        // label
        ctx.globalAlpha = 0.5 + 0.5 * depth;
        ctx.fillStyle = "#e8ecf2"; ctx.font = (isH ? "bold " : "") + "12px ui-monospace,Menlo,monospace";
        ctx.fillText(o.p.d.name, s.x + rad + 3, s.y + 4);
      });
      ctx.globalAlpha = 1;
      o_screens = sp; // for hit-testing
    }

    let o_screens = [];
    function pick(mx, my) {
      let best = null, bd = 18 * 18;
      o_screens.forEach(function (o) {
        const dx = o.s.x - mx, dy = o.s.y - my, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = o.p; }
      });
      return best;
    }

    // interaction: drag to rotate, wheel to zoom, hover for tooltip
    let drag = null;
    canvas.addEventListener("pointerdown", function (e) { drag = [e.clientX, e.clientY]; auto = false; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointerup", function () { drag = null; });
    canvas.addEventListener("pointermove", function (e) {
      const r = canvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      if (drag) {
        yaw += (e.clientX - drag[0]) * 0.01; pitch += (e.clientY - drag[1]) * 0.01;
        pitch = Math.max(-1.4, Math.min(1.4, pitch)); drag = [e.clientX, e.clientY]; draw();
      } else {
        const h = pick(mx, my);
        if (h !== hover) { hover = h; draw(); }
        if (opts.tooltipEl) {
          const t = opts.tooltipEl;
          if (h) {
            const d = h.d, dist = d.dist >= 1e6 ? (d.dist / 1e6).toFixed(1) + " MB"
              : d.dist >= 1e3 ? (d.dist / 1e3).toFixed(0) + " KB" : d.dist + " B";
            const gate = (d.scored !== false)
              ? "<span style='color:#7fd8a6'>scored</span>"
              : "<span style='color:#cc79a7'>diagnostic — below K plane, not scored</span>";
            t.innerHTML = "<b>" + d.name + "</b> · " + d.cat + " · " + gate + "<br>" +
              "entropy " + d.entropy.toFixed(2) + " bits/byte · repeat coverage " +
              (d.coverage * 100).toFixed(0) + "% · match distance " + dist + "<br>" +
              "size " + d.sizeMB.toFixed(1) + " MB · compressibility K " +
              (d.K != null ? d.K.toFixed(2) : "—");
            t.style.display = "block"; t.style.left = (mx + 14) + "px"; t.style.top = (my + 12) + "px";
          } else { t.style.display = "none"; }
        }
      }
    });
    canvas.addEventListener("wheel", function (e) { e.preventDefault(); dist = Math.max(2.2, Math.min(6, dist + e.deltaY * 0.002)); draw(); }, { passive: false });

    if (opts.legendEl) {
      var lg = Object.keys(cats).map(function (k) {
        return '<span class="lg"><i style="background:' + cats[k] + '"></i>' + k + "</span>";
      }).join("");
      lg += '<span class="lg"><i style="background:transparent;border:1.5px dashed #cc79a7;border-radius:50%"></i>'
          + 'hollow = below K plane (diagnostic, not scored)</span>';
      opts.legendEl.innerHTML = lg;
    }

    function tick() { if (auto) { yaw += 0.0025; draw(); } requestAnimationFrame(tick); }
    global.addEventListener("resize", function () { resize(); draw(); });
    resize(); draw(); tick();
  }

  global.SquishyCube = { mount: mount };
})(window);
