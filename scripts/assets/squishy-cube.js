/* squishy-cube.js — a dependency-free 3D scatter of the Squishy corpus.
 *
 * WHY VANILLA (no three.js): the scene is ~24 points and must stay a single, frozen,
 * citable artifact for ~20 years with zero CDN/runtime dependency. A vendored WebGL
 * engine (hundreds of KB) is pure liability here; a carefully-designed 2.5D canvas
 * scatter reads better and stays ~15 KB self-contained.
 *
 * Each dataset is one point, positioned by three INTRINSIC, codec-free byte properties:
 *   x = entropy        (bits/byte, 0..8  — how random the bytes look)
 *   y = repeat coverage(0..1           — how much of the file exactly recurs)
 *   z = match distance (bytes, LOG     — how far back the repeats sit)
 * coloured by category (Okabe–Ito, colour-blind-safe), sized by file size (dot AREA ∝
 * log size, so the encoding is honest). Every file is scored (one vote per file in the
 * Squishy Score) — there is no gate and no wall; every dot is drawn solid and glowing.
 *
 * Depth cues, in order of strength: occlusion (painter's sort) · perspective foreshortening
 * · size-by-distance · shading/desaturation fog with depth · a shadow projected onto the
 * gridded floor · a back/floor wall "gizmo" with ticks + units instead of a bare cube.
 *
 * Accessibility: honours prefers-reduced-motion (no auto-orbit, no animated camera);
 * the canvas is focusable and arrow-keys rotate / +- zoom / 0 resets / Enter cycles the
 * focused point; a full data <table> fallback is rendered by the page for no-WebGL / SR.
 *
 *   SquishyCube.mount(canvasEl, data, {legendEl, tooltipEl, statusEl})
 */
(function (global) {
  "use strict";

  var REDUCED = global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  function esc(s) {
    s = s == null ? "" : String(s);
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function normalize(v, ax) {
    var t = ax.log ? Math.log10(v) : v;
    var lo = ax.log ? Math.log10(ax.min) : ax.min;
    var hi = ax.log ? Math.log10(ax.max) : ax.max;
    return clamp(((t - lo) / (hi - lo)) * 2 - 1, -1, 1); // → [-1, 1]
  }

  // yaw about Y, then pitch about X, on a [-1,1] cube point.
  function rotate(p, yaw, pitch) {
    var cy = Math.cos(yaw), sy = Math.sin(yaw);
    var cx = Math.cos(pitch), sx = Math.sin(pitch);
    var x = p[0] * cy + p[2] * sy;
    var z = -p[0] * sy + p[2] * cy;
    var y = p[1] * cx - z * sx;
    z = p[1] * sx + z * cx;
    return [x, y, z];
  }

  // hex → rgb, and a fog mixer toward the background as points recede.
  function hexRGB(h) {
    h = h.replace("#", "");
    if (h.length === 3) h = h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
    return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)];
  }
  function mix(c1, c2, t) {
    return "rgb(" + Math.round(c1[0]+(c2[0]-c1[0])*t) + "," +
                    Math.round(c1[1]+(c2[1]-c1[1])*t) + "," +
                    Math.round(c1[2]+(c2[2]-c1[2])*t) + ")";
  }

  // light theme — matches the page background (#fafafa). Fog recedes toward FAR.
  var BG_NEAR = [252, 252, 253], BG_FAR = [232, 235, 240];
  var INK = [28, 37, 48];                               // dark ink for labels/ticks on light

  function shortBytes(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + " MB";
    if (n >= 1e3) return (n / 1e3).toFixed(0) + " KB";
    return Math.round(n) + " B";
  }
  function shortSize(mb) {
    return mb >= 1000 ? (mb / 1000).toFixed(1) + " GB" : mb >= 1 ? mb.toFixed(1) + " MB"
         : (mb * 1000).toFixed(0) + " KB";
  }

  function mount(canvas, data, opts) {
    opts = opts || {};
    var ctx = canvas.getContext("2d");
    var cats = data.categories;          // {name: "#rrggbb"}
    var ax = data.axes;                  // {x:{label,min,max,log,unit}, y:…, z:…}
    var catRGB = {}; Object.keys(cats).forEach(function (k) { catRGB[k] = hexRGB(cats[k]); });

    // pre-normalize every point into the unit cube once. y is negated (screen-down).
    var pts = data.points.map(function (d) {
      return {
        d: d,
        c: [normalize(d.x, ax.x), -normalize(d.y, ax.y), -normalize(d.z, ax.z)],
        rgb: catRGB[d.cat] || [154, 160, 166],
        color: cats[d.cat] || "#9aa0a6",
      };
    });

    // default camera: a gentle 3/4 view that reads instantly — x to the right,
    // coverage rising, z going back. tuned so the floor + both back walls are visible.
    var HOME = { yaw: -0.62, pitch: -0.46, dist: 4.8 };
    var VY = 1.0;                        // (no vertical stretch; headroom comes from a taller canvas)
    var yaw = HOME.yaw, pitch = HOME.pitch, dist = HOME.dist;
    var zoom = 1;                        // true magnification (1 = home); wheel/pinch/keys change it
    var MINZ = 0.6, MAXZ = 6;
    var auto = !REDUCED, focusIdx = -1, hover = null;
    var W = 0, H = 0, cx = 0, cy = 0, scale = 1;
    var screens = [];                    // last projected points, for hit-testing

    function resize() {
      var dpr = Math.min(global.devicePixelRatio || 1, 2.5);
      var r = canvas.getBoundingClientRect();
      W = r.width; H = r.height;
      if (!W || !H) return;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      // size the cube to the WIDTH and leave generous empty room above and below, so a
      // point rotated near + low (or high) stays on screen. cy centred for symmetry.
      cx = W / 2; cy = H * 0.5; scale = Math.min(W * 0.34, H * 0.30);
    }

    function project(c3) {
      var r = rotate([c3[0], c3[1] * VY, c3[2]], yaw, pitch);   // vertical stretch applied to every point
      var f = dist / (dist - r[2]);      // perspective foreshortening
      var pf = f * zoom;                 // fold zoom in so dots, gizmo + labels all magnify together
      return { x: cx + r[0] * scale * pf, y: cy + r[1] * scale * pf, z: r[2], f: pf };
    }
    // depth in 0..1 (1 = nearest the camera) for fog/size/alpha.
    function depthOf(z) { return clamp((z + 1.5) / 3.0, 0, 1); }

    function seg(a, b, style, width, dash) {
      var pa = project(a), pb = project(b);
      ctx.strokeStyle = style; ctx.lineWidth = width || 1;
      if (dash) ctx.setLineDash(dash);
      ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
      if (dash) ctx.setLineDash([]);
    }

    // ── ticks: map a real value on an axis to its [-1,1] coord, choose nice ticks ──
    function axisTicks(a) {
      if (a.log) {
        var lo = Math.ceil(Math.log10(a.min)), hi = Math.floor(Math.log10(a.max)), t = [];
        for (var e = lo; e <= hi; e++) t.push({ v: Math.pow(10, e), label: shortBytes(Math.pow(10, e)) });
        return t;
      }
      var span = a.max - a.min, step = span / 4, out = [];
      for (var i = 0; i <= 4; i++) {
        var v = a.min + step * i;
        out.push({ v: v, label: (a.max <= 1 ? v.toFixed(2).replace(/0$/, "") : v.toFixed(0)) });
      }
      return out;
    }
    var TX = axisTicks(ax.x), TY = axisTicks(ax.y), TZ = axisTicks(ax.z);

    // back/floor "gizmo": a floor grid at y=+1 and two back walls (z=+1, x=-1) with
    // ticks + units — far more legible than a bare wireframe cube.
    function drawGizmo() {
      var faint = "rgba(0,0,0,0.07)", line = "rgba(0,0,0,0.22)";
      // floor grid (coverage = 0 plane) — z lines and x lines
      TZ.forEach(function (t) {
        var nz = -normalize(t.v, ax.z);
        seg([-1, 1, nz], [1, 1, nz], faint, 1);
      });
      TX.forEach(function (t) {
        var nx = normalize(t.v, ax.x);
        seg([nx, 1, -1], [nx, 1, 1], faint, 1);
      });
      // back wall (z=+1): coverage rising, entropy across — horizontal coverage lines
      TY.forEach(function (t) {
        var ny = -normalize(t.v, ax.y);
        seg([-1, ny, 1], [1, ny, 1], faint, 1);
      });
      // the three framing edges nearest the data origin (back-bottom corner)
      var o = [-1, 1, 1];
      seg(o, [1, 1, 1], line, 1.5);      // entropy edge (floor, back)
      seg(o, [-1, -1, 1], line, 1.5);    // coverage edge (back wall, left)
      seg(o, [-1, 1, -1], line, 1.5);    // distance edge (floor, left)

      // tick labels — dark, legible on the light background
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.font = "12px ui-monospace,Menlo,monospace";
      ctx.fillStyle = "rgba(40,46,54,0.78)";
      TX.forEach(function (t) {            // entropy, back-bottom edge
        var p = project([normalize(t.v, ax.x), 1, 1]); ctx.fillText(t.label, p.x, p.y + 13);
      });
      TZ.forEach(function (t) {            // repeat distance (log), left-bottom edge
        var p = project([-1, 1, -normalize(t.v, ax.z)]); ctx.fillText(t.label, p.x - 8, p.y + 11);
      });
      ctx.textAlign = "right";
      TY.forEach(function (t) {            // repetition, back-left vertical edge
        var p = project([-1, -normalize(t.v, ax.y), 1]); ctx.fillText(t.label, p.x - 9, p.y);
      });

      // axis titles — bold, axis-coloured, sized for instant reading
      ctx.font = "700 14px -apple-system,Segoe UI,sans-serif";
      var xa = project([0, 1, 1]); ctx.textAlign = "center"; ctx.fillStyle = "#b23a6b";
      ctx.fillText(ax.x.label, xa.x, xa.y + 30);
      var za = project([-1, 1, 0]); ctx.fillStyle = "#2a6f9e"; ctx.textAlign = "right";
      ctx.fillText(ax.z.label, za.x - 6, za.y + 28);
      var ya = project([-1, -1.2, 1]); ctx.fillStyle = "#1f8a5a"; ctx.textAlign = "center";
      ctx.fillText(ax.y.label, ya.x, ya.y);
    }

    function dotRadius(p, isHover) {
      // honest size: AREA ∝ normalized log-size. r in [4.5, 15] px at unit perspective.
      var base = Math.sqrt(0.18 + 0.82 * (p.d.r || 0.5)) * 13 + 1.5;
      return base * (isHover ? 1.35 : 1);
    }

    function drawShadow(s, rad, depth) {
      // project the point straight down onto the floor (coverage=0 → y=+1) for a soft
      // contact shadow — a strong, honest depth cue.
      var d = pts[s.i];
      var floor = project([d.c[0], 1, d.c[2]]);
      var rr = rad * 0.9 * floor.f / s.f;
      var g = ctx.createRadialGradient(floor.x, floor.y, 0, floor.x, floor.y, rr * 1.6);
      g.addColorStop(0, "rgba(0,0,0," + (0.32 * depth).toFixed(2) + ")");
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.ellipse(floor.x, floor.y, rr * 1.6, rr * 0.7, 0, 0, 7); ctx.fill();
    }

    function draw() {
      if (!W || !H) return;
      ctx.clearRect(0, 0, W, H);
      var g = ctx.createRadialGradient(cx, cy - H * 0.1, 20, cx, cy, Math.max(W, H) * 0.75);
      g.addColorStop(0, "rgb(" + BG_NEAR.join(",") + ")");
      g.addColorStop(1, "rgb(" + BG_FAR.join(",") + ")");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

      drawGizmo();

      // project + painter-sort back→front
      var sp = pts.map(function (p, i) { var s = project(p.c); s.i = i; return s; })
                  .sort(function (a, b) { return a.z - b.z; });

      // floor shadows first (all of them, so dots overlay cleanly)
      sp.forEach(function (s) {
        var p = pts[s.i]; var rad = dotRadius(p, false) * s.f;
        drawShadow(s, rad, depthOf(s.z));
      });

      sp.forEach(function (s) {
        var p = pts[s.i], isH = hover === p || focusIdx === s.i;
        var depth = depthOf(s.z);
        var rad = dotRadius(p, isH) * s.f;
        // fog: recede toward background, and desaturate
        var col = mix(p.rgb, BG_FAR, (1 - depth) * 0.55);
        ctx.globalAlpha = 1;
        // every file is scored → one solid, glossy lit-sphere read for every dot
        var gg = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, rad * 2.4);
        gg.addColorStop(0, mix(p.rgb, BG_FAR, (1 - depth) * 0.3));
        gg.addColorStop(1, "rgba(0,0,0,0)");
        ctx.globalAlpha = 0.32 + 0.4 * depth;
        ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(s.x, s.y, rad * 2.4, 0, 7); ctx.fill();
        ctx.globalAlpha = 1;
        ctx.fillStyle = col; ctx.beginPath(); ctx.arc(s.x, s.y, rad, 0, 7); ctx.fill();
        // crisp edge + soft top highlight → a glossy lit-sphere read on light bg
        ctx.lineWidth = 1; ctx.strokeStyle = "rgba(0,0,0," + (0.30 * (0.5 + 0.5 * depth)).toFixed(2) + ")";
        ctx.beginPath(); ctx.arc(s.x, s.y, rad, 0, 7); ctx.stroke();
        var sg = ctx.createRadialGradient(s.x - rad * 0.35, s.y - rad * 0.4, rad * 0.05, s.x, s.y, rad);
        sg.addColorStop(0, "rgba(255,255,255," + (0.55 * depth).toFixed(2) + ")");
        sg.addColorStop(0.5, "rgba(255,255,255,0)");
        ctx.fillStyle = sg; ctx.beginPath(); ctx.arc(s.x, s.y, rad, 0, 7); ctx.fill();
        if (isH) { ctx.globalAlpha = 1; ctx.strokeStyle = "#1c2530"; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(s.x, s.y, rad + 3, 0, 7); ctx.stroke(); }
        s.rad = rad;
      });

      // labels last, decluttered: show only the focused/hovered + the largest few,
      // and suppress labels that would collide.
      var labelled = [], placed = [];
      var order = sp.slice().sort(function (a, b) { return pts[b.i].d.r - pts[a.i].d.r; });
      order.forEach(function (s) {
        var p = pts[s.i], isH = hover === p || focusIdx === s.i;
        if (!isH && labelled.length >= 8) return;
        var lx = s.x + s.rad + 4, ly = s.y + 4;
        var collide = placed.some(function (q) { return Math.abs(q.x - lx) < 70 && Math.abs(q.y - ly) < 13; });
        if (collide && !isH) return;
        placed.push({ x: lx, y: ly }); labelled.push({ s: s, isH: isH });
      });
      ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      labelled.forEach(function (L) {
        var s = L.s, p = pts[s.i], depth = depthOf(s.z);
        ctx.globalAlpha = L.isH ? 1 : 0.4 + 0.55 * depth;
        ctx.font = (L.isH ? "bold " : "600 ") + "12.5px -apple-system,Segoe UI,sans-serif";
        var name = p.d.label || p.d.name;        // short, friendly name
        // light halo for legibility over busy areas, dark ink text
        ctx.lineWidth = 3.5; ctx.strokeStyle = "rgba(250,250,250,0.95)";
        ctx.strokeText(name, s.x + s.rad + 5, s.y + 4);
        ctx.fillStyle = "#1c2530"; ctx.fillText(name, s.x + s.rad + 5, s.y + 4);
      });
      ctx.globalAlpha = 1;

      screens = sp;
    }

    function tooltipHTML(d) {
      var sw = '<i style="display:inline-block;width:.6rem;height:.6rem;border-radius:50%;vertical-align:middle;' +
               'background:' + (cats[d.cat] || "#9aa") + '"></i>';
      var links = [];
      if (d.source_url) links.push('<a href="' + esc(d.source_url) + '" target="_blank" rel="noopener">source ↗</a>');
      if (d.url) links.push('<a href="' + esc(d.url) + '" target="_blank" rel="noopener">download ↗</a>');
      if (d.license) links.push('<span style="color:#888">' + esc(d.license) + '</span>');
      return "<b>" + esc(d.label || d.name) + "</b> &nbsp;" + sw +
        " <span style='color:#888'>" + esc(d.cat) + "</span>" +
        (d.desc ? "<div class='tdesc'>" + esc(d.desc) + "</div>" : "") +
        "<div class='tnums'>entropy <b>" + d.entropy.toFixed(2) + "</b> · repeats <b>" +
        (d.coverage * 100).toFixed(0) + "%</b> · repeat distance farthest <b>" +
        shortBytes(d.distp90 != null ? d.distp90 : d.dist) + "</b> / typical <b>" + shortBytes(d.dist) +
        "</b> · size <b>" + shortSize(d.sizeMB) + "</b></div>" +
        (links.length ? "<div class='tlinks'>" + links.join("") + "</div>" : "");
    }

    function pick(mx, my) {
      // front-most within radius wins (iterate near→far)
      for (var i = screens.length - 1; i >= 0; i--) {
        var s = screens[i], dx = s.x - mx, dy = s.y - my, rr = (s.rad || 6) + 6;
        if (dx * dx + dy * dy <= rr * rr) return pts[s.i];
      }
      return null;
    }

    var hideTimer = null;
    function hideTip() {
      if (opts.tooltipEl) opts.tooltipEl.style.display = "none";
      if (hover) { hover = null; draw(); }
    }
    function showTip(p) {
      var t = opts.tooltipEl; if (!t) return;
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
      if (!p) { t.style.display = "none"; return; }
      t.innerHTML = tooltipHTML(p.d); t.style.display = "block";
      // anchor to the DOT, centred, and placed above it (or below if no room) so the
      // card never covers the point you're inspecting.
      var s = project(p.c), rad = dotRadius(p, true) * s.f, gap = 14;
      var tw = t.offsetWidth || 240, th = t.offsetHeight || 90;
      var left = clamp(s.x - tw / 2, 6, Math.max(6, W - tw - 6));
      var above = s.y - rad - gap - th;
      var top = above >= 6 ? above : Math.min(s.y + rad + gap, Math.max(6, H - th - 6));
      t.style.left = left + "px"; t.style.top = top + "px";
    }
    // keep the card open while the pointer is inside it, so its links are clickable
    if (opts.tooltipEl) {
      opts.tooltipEl.addEventListener("pointerenter", function () {
        if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
      });
      opts.tooltipEl.addEventListener("pointerleave", hideTip);
    }

    // ── interaction: 1-finger/drag rotate, 2-finger pinch + wheel zoom, hover tip ──
    var drag = null;                     // last pos for one-pointer rotate
    var pointers = {};                   // active pointers by id, for pinch
    var pinchBase = 0;                   // baseline finger distance for the current pinch
    function pinchSpan() {
      var id = Object.keys(pointers); if (id.length < 2) return 0;
      var a = pointers[id[0]], b = pointers[id[1]];
      return Math.hypot(a.x - b.x, a.y - b.y);
    }
    canvas.addEventListener("pointerdown", function (e) {
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      auto = false; canvas.focus();
      try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
      var n = Object.keys(pointers).length;
      if (n >= 2) { drag = null; pinchBase = pinchSpan(); }   // second finger → pinch, stop rotating
      else { drag = [e.clientX, e.clientY]; }
    });
    function endDrag(e) {
      if (e && e.pointerId != null) delete pointers[e.pointerId];
      var ids = Object.keys(pointers);
      if (ids.length < 2) pinchBase = 0;
      drag = ids.length === 1 ? [pointers[ids[0]].x, pointers[ids[0]].y] : null;
    }
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("pointermove", function (e) {
      if (pointers[e.pointerId]) pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      if (Object.keys(pointers).length >= 2) {            // pinch-to-zoom
        var span = pinchSpan();
        if (pinchBase > 0 && span > 0) zoom = clamp(zoom * span / pinchBase, MINZ, MAXZ);
        pinchBase = span; draw(); return;
      }
      var r = canvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      if (drag) {
        yaw += (e.clientX - drag[0]) * 0.01;
        pitch = clamp(pitch + (e.clientY - drag[1]) * 0.01, -1.45, 0.2);
        drag = [e.clientX, e.clientY]; draw();
      } else {
        var h = pick(mx, my);
        if (h !== hover) { hover = h; canvas.style.cursor = h ? "pointer" : "grab"; draw(); }
        showTip(h);
      }
    });
    canvas.addEventListener("pointerleave", function () {
      // delay hide so the pointer can travel into the card to use its links
      if (!drag) { if (hideTimer) clearTimeout(hideTimer); hideTimer = setTimeout(hideTip, 260); }
    });
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault(); auto = false;
      // multiplicative zoom toward/away — exponential so each notch feels even
      zoom = clamp(zoom * Math.exp(-e.deltaY * 0.0015), MINZ, MAXZ); draw();
    }, { passive: false });

    // ── keyboard accessibility ──
    canvas.addEventListener("keydown", function (e) {
      var k = e.key, used = true;
      if (k === "ArrowLeft") yaw -= 0.12;
      else if (k === "ArrowRight") yaw += 0.12;
      else if (k === "ArrowUp") pitch = clamp(pitch - 0.1, -1.45, 0.2);
      else if (k === "ArrowDown") pitch = clamp(pitch + 0.1, -1.45, 0.2);
      else if (k === "+" || k === "=") zoom = clamp(zoom * 1.18, MINZ, MAXZ);
      else if (k === "-" || k === "_") zoom = clamp(zoom / 1.18, MINZ, MAXZ);
      else if (k === "0") { yaw = HOME.yaw; pitch = HOME.pitch; dist = HOME.dist; zoom = 1; }
      else if (k === "Enter" || k === " ") {
        focusIdx = (focusIdx + 1) % pts.length;
        var p = pts[focusIdx];
        showTip(p);
        if (opts.statusEl) opts.statusEl.textContent = "Focused: " + p.d.name +
          " · " + (p.d.cat || "");
      } else used = false;
      if (used) { e.preventDefault(); auto = false; draw(); }
    });

    // reset button (if present) + a public reset
    if (opts.resetEl) opts.resetEl.addEventListener("click", function () {
      yaw = HOME.yaw; pitch = HOME.pitch; dist = HOME.dist; zoom = 1; auto = !REDUCED; focusIdx = -1; draw();
    });

    // ── legend ──
    if (opts.legendEl) {
      var lg = '<span class="lg" style="color:#666">colour = kind:</span>';
      lg += Object.keys(cats).map(function (k) {
        return '<span class="lg"><i style="background:' + cats[k] + '"></i>' + k + "</span>";
      }).join("");
      lg += '<span class="lg"><i class="dotbig"></i><i class="dotsm"></i>dot size = file size (MB → GB)</span>';
      opts.legendEl.innerHTML = lg;
    }

    // ── render loop (auto-orbit unless reduced-motion / interacted) ──
    var running = true;
    function tick() {
      if (!running) return;
      if (auto) { yaw += 0.0022; draw(); }
      requestAnimationFrame(tick);
    }
    var ro = (typeof ResizeObserver !== "undefined")
      ? new ResizeObserver(function () { resize(); draw(); }) : null;
    if (ro) ro.observe(canvas); else global.addEventListener("resize", function () { resize(); draw(); });

    resize(); draw();
    if (REDUCED) { /* static frame, no orbit */ } else tick();

    // pause auto-orbit when offscreen (perf + battery)
    if (typeof IntersectionObserver !== "undefined") {
      new IntersectionObserver(function (es) {
        running = es[0].isIntersecting;
        if (running && !REDUCED) tick();
      }).observe(canvas);
    }

    return {
      reset: function () { yaw = HOME.yaw; pitch = HOME.pitch; dist = HOME.dist; zoom = 1; auto = !REDUCED; draw(); },
    };
  }

  global.SquishyCube = { mount: mount };
})(window);
