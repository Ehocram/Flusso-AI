/* Sfondo animato a "rete neurale" per le pagine di autenticazione.
   Canvas puro, nessuna dipendenza esterna. Richiede <canvas id="neural">.
   Rispetta prefers-reduced-motion (disegna un singolo frame statico). */
(function () {
  const canvas = document.getElementById('neural');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let w, h, dpr, nodes = [], signals = [], raf;
  const RED = '226,0,26';

  function size() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = canvas.width = Math.floor(innerWidth * dpr);
    h = canvas.height = Math.floor(innerHeight * dpr);
    canvas.style.width = innerWidth + 'px';
    canvas.style.height = innerHeight + 'px';
  }

  function build() {
    const area = (w * h) / (dpr * dpr);
    const count = Math.max(34, Math.min(92, Math.round(area / 19000)));
    nodes = [];
    for (let i = 0; i < count; i++) {
      nodes.push({
        x: Math.random() * w, y: Math.random() * h,
        vx: (Math.random() - .5) * .22 * dpr, vy: (Math.random() - .5) * .22 * dpr,
        r: (Math.random() * 1.6 + 1.1) * dpr,
        red: Math.random() < 0.12
      });
    }
    signals = [];
  }

  const LINK = 150;
  function linkDist() { return LINK * dpr; }

  function neighbors(i) {
    const out = [], a = nodes[i], d = linkDist();
    for (let j = 0; j < nodes.length; j++) {
      if (j === i) continue;
      const b = nodes[j], dx = a.x - b.x, dy = a.y - b.y;
      if (dx * dx + dy * dy < d * d) out.push(j);
    }
    return out;
  }

  function spawnSignal(from) {
    const nb = neighbors(from);
    if (!nb.length) return;
    const to = nb[(Math.random() * nb.length) | 0];
    signals.push({ from, to, t: Math.random() * 0.15, speed: 0.006 + Math.random() * 0.01 });
  }

  function step() {
    ctx.clearRect(0, 0, w, h);
    const d = linkDist();

    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      a.x += a.vx; a.y += a.vy;
      if (a.x < 0 || a.x > w) a.vx *= -1;
      if (a.y < 0 || a.y > h) a.vy *= -1;
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j], dx = a.x - b.x, dy = a.y - b.y;
        const dist2 = dx * dx + dy * dy;
        if (dist2 < d * d) {
          const o = (1 - Math.sqrt(dist2) / d);
          ctx.strokeStyle = 'rgba(120,150,195,' + (o * 0.34).toFixed(3) + ')';
          ctx.lineWidth = dpr * 0.6;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
    }

    for (const n of nodes) {
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      if (n.red) {
        ctx.fillStyle = 'rgba(' + RED + ',.85)';
        ctx.shadowColor = 'rgba(' + RED + ',.8)'; ctx.shadowBlur = 10 * dpr;
      } else {
        ctx.fillStyle = 'rgba(165,190,225,.75)';
        ctx.shadowColor = 'rgba(120,150,195,.5)'; ctx.shadowBlur = 5 * dpr;
      }
      ctx.fill(); ctx.shadowBlur = 0;
    }

    for (let k = signals.length - 1; k >= 0; k--) {
      const s = signals[k], a = nodes[s.from], b = nodes[s.to];
      if (!a || !b) { signals.splice(k, 1); continue; }
      s.t += s.speed;
      const x = a.x + (b.x - a.x) * s.t, y = a.y + (b.y - a.y) * s.t;
      ctx.strokeStyle = 'rgba(' + RED + ',' + (0.5 * (1 - s.t)).toFixed(3) + ')';
      ctx.lineWidth = dpr * 1.3;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(x, y); ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 2.4 * dpr, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(' + RED + ',1)';
      ctx.shadowColor = 'rgba(' + RED + ',.9)'; ctx.shadowBlur = 12 * dpr;
      ctx.fill(); ctx.shadowBlur = 0;
      if (s.t >= 1) {
        signals.splice(k, 1);
        if (Math.random() < 0.6 && signals.length < 16) spawnSignal(s.to);
      }
    }

    if (signals.length < 9 && Math.random() < 0.05) spawnSignal((Math.random() * nodes.length) | 0);
    if (!reduce) raf = requestAnimationFrame(step);
  }

  function start() { size(); build(); cancelAnimationFrame(raf); step(); }
  addEventListener('resize', start);
  start();
})();
