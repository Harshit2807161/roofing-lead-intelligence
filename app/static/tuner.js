/* F15 ranking tuner: re-weight score factors and re-rank every lead in the browser. No writes.
   Each factor's points are multiplied by its weight (0x to 2x); the complaints penalty is unchanged. */
(() => {
  const el = document.getElementById("tuner-data");
  if (!el) return;
  const D = JSON.parse(el.textContent);
  const TOP = 25;
  const MAX = Object.fromEntries(D.factors.map(f => [f.key, f.max]));
  const w = Object.fromEntries(D.factors.map(f => [f.key, 1]));
  const N = D.leads.length;

  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const r1 = n => Math.round(n * 10) / 10;
  const num = n => { const v = r1(n); return Number.isInteger(v) ? String(v) : v.toFixed(1); };
  const mult = x => (Number.isInteger(x) ? String(x) : x.toFixed(1)) + "×";

  /* ---------- tooltip (shared look with .cite-pop) ---------- */
  const tip = document.getElementById("tip");
  let tipEl = null;
  function place(target, x) {
    const r = target.getBoundingClientRect();
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const cx = x ?? r.left + r.width / 2;
    let top = r.top - th - 10;
    if (top < 8) top = r.bottom + 10;
    tip.style.left = Math.min(Math.max(8, cx - tw / 2), innerWidth - tw - 8) + "px";
    tip.style.top = top + "px";
  }
  function show(target, x) {
    if (tipEl !== target) {
      const [head, ...rest] = target.dataset.tip.split("\n");
      tip.innerHTML = `<span class="t-label">${esc(head)}</span>${esc(rest.join(" "))}`;
      tipEl = target;
    }
    tip.hidden = false;
    place(target, x);
  }
  function hide() { tip.hidden = true; tipEl = null; }
  document.addEventListener("mousemove", e => {
    const t = e.target.closest && e.target.closest("[data-tip]");
    if (t) show(t, e.clientX); else if (!tip.hidden) hide();
  });
  document.addEventListener("focusin", e => {
    const t = e.target.closest && e.target.closest("[data-tip]");
    if (t) show(t); else hide();
  });
  document.addEventListener("scroll", hide, { passive: true });

  /* ---------- scoring ---------- */
  function rescore() {
    return D.leads
      .map(L => {
        let t = 0;
        L.f.forEach(f => { t += f.k in w ? f.p * w[f.k] : f.p; });
        return { L, t: r1(Math.max(0, t)) };
      })
      .sort((a, b) => b.t - a.t || a.L.rank - b.L.rank); // official rank already encodes the tie-break
  }

  /* Factor strip: segment width = weighted max, fill = share of that max the lead earned. */
  function strip(L) {
    let h = `<span class="dna-wrap"><span class="dna" role="img" aria-label="${esc(L.name)} factor breakdown">`;
    let pen = "";
    L.f.forEach(f => {
      if (f.k in w) {
        const top = MAX[f.k] * w[f.k];
        if (top <= 0) return;
        const fill = MAX[f.k] ? (f.p / MAX[f.k]) * 100 : 0;
        h += `<span class="seg" style="flex-grow:${top}" data-tip="${esc(`${f.l}: ${num(f.p * w[f.k])}/${num(top)}\n${f.r}`)}"><i style="width:${fill}%"></i></span>`;
      } else if (f.p < 0) {
        pen = `<span class="seg" style="width:${Math.min(100, (-f.p / 7) * 100)}%" data-tip="${esc(`${f.l}: ${num(f.p)} (not re-weighted)\n${f.r}`)}"><i></i></span>`;
      }
    });
    return `${h}</span><span class="pen">${pen}</span></span>`;
  }

  /* ---------- drawing ---------- */
  const box = document.getElementById("wi-form");
  const list = document.getElementById("wi-list");
  const more = document.getElementById("wi-more");
  const summary = document.getElementById("wi-summary");

  function drawSliders() {
    D.factors.forEach(({ key }) => {
      const changed = w[key] !== 1;
      const pts = num(MAX[key] * w[key]);
      const out = document.getElementById("o-" + key);
      out.textContent = `${mult(w[key])} · ${pts} pts`;
      out.title = `${D.version} default: 1× · ${num(MAX[key])} pts`;
      box.querySelector(`.wi-slider[data-k="${key}"]`).classList.toggle("changed", changed);
    });
    const total = D.factors.reduce((s, f) => s + f.max * w[f.key], 0);
    document.getElementById("wi-max").textContent = `Max ${num(total)} pts`;
  }

  function row({ L, t }, i) {
    const now = i + 1, mv = L.rank - now;
    const was = `<small>was #${L.rank}</small>`;
    const arrow = mv > 0 ? `<span class="mv up" title="Up ${mv} from #${L.rank}">▲${mv}${was}</span>`
      : mv < 0 ? `<span class="mv down" title="Down ${-mv} from #${L.rank}">▼${-mv}${was}</span>`
      : `<span class="mv same" title="No change">—</span>`;
    const d = r1(t - L.total);
    const delta = d === 0 ? "" : `${d > 0 ? "+" : "−"}${num(Math.abs(d))}`;
    const meta = `<span class="lead-meta"><a class="lead-name" href="/leads/${esc(L.id)}">${esc(L.name)}</a><span class="lead-sub"><span>${esc(L.city)}, ${esc(L.state)}</span><span class="dot">·</span><span>${esc(L.play)}</span></span></span>`;
    const isNew = now <= TOP && L.rank > TOP;
    return `<li><div class="wi-row${isNew ? " is-new" : ""}"><span class="lead-rank">${now}</span>${arrow}${meta}${strip(L)}`
      + `<span class="wi-num wi-score"><span class="wi-delta ${d > 0 ? "up" : d < 0 ? "down" : ""}">${delta}</span><span class="score">${Math.round(t)}</span></span>`
      + `<span class="wi-num wi-official" title="Official ${esc(D.version)} score">${num(L.total)}</span></div></li>`;
  }

  function drawSummary(ranked) {
    const moved = ranked.filter(({ L }, i) => L.rank !== i + 1);
    if (!moved.length) { summary.textContent = `Official ${D.version} order`; return; }
    const entered = ranked.slice(0, TOP).filter(({ L }) => L.rank > TOP).length;
    let best = null;
    ranked.forEach(({ L }, i) => { const up = L.rank - (i + 1); if (up > 0 && (!best || up > best.up)) best = { L, up }; });
    summary.textContent = `${moved.length} of ${N} move · ${entered} new in top ${TOP}`
      + (best ? ` · biggest climb: ${best.L.name} ▲${best.up}` : "");
  }

  function draw() {
    drawSliders();
    const ranked = rescore();
    list.innerHTML = ranked.map(row).join("");
    drawSummary(ranked);
  }

  box.addEventListener("input", e => {
    const k = e.target.dataset.k;
    if (!k) return;
    w[k] = +e.target.value;
    draw();
  });
  document.getElementById("wi-reset").addEventListener("click", () => {
    D.factors.forEach(({ key }) => { w[key] = 1; box.querySelector(`input[data-k="${key}"]`).value = 1; });
    draw();
  });

  const copyBtn = document.getElementById("wi-copy");
  copyBtn.addEventListener("click", async () => {
    const json = JSON.stringify({
      base: D.version,
      weights: Object.fromEntries(D.factors.map(f => [f.key, w[f.key]])),
      max_points: Object.fromEntries(D.factors.map(f => [f.key, r1(f.max * w[f.key])])),
    }, null, 2);
    const label = copyBtn.textContent;
    try {
      await navigator.clipboard.writeText(json);
      copyBtn.textContent = "Copied";
    } catch {
      window.prompt("Copy these weights", json);
    }
    setTimeout(() => { copyBtn.textContent = label; }, 1500);
  });

  more.hidden = N <= TOP;
  more.addEventListener("click", () => {
    const open = list.classList.toggle("show-all");
    more.textContent = open ? `Show top ${TOP}` : `Show all ${N}`;
    more.setAttribute("aria-expanded", String(open));
  });

  draw();
})();
