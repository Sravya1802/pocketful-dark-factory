() => {
  const lum = c => { const [r, g, b] = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const parse = s => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(parseFloat); return { c: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 }; };
  const bgOf = e => { let x = e; const stack = []; while (x) { const p = parse(getComputedStyle(x).backgroundColor); if (p && p.a > 0) { stack.push(p); if (p.a >= 1) break; } x = x.parentElement; } let base = [255, 255, 255]; for (let i = stack.length - 1; i >= 0; i--) { const p = stack[i]; base = base.map((v, j) => p.c[j] * p.a + v * (1 - p.a)); } return base; };
  const out = [], seen = new Set(), all = [];
  for (const e of document.querySelectorAll('body *')) {
    const own = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 0); if (!own) continue;
    const r = e.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(e); if (cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) continue;
    const fg = parse(cs.color); if (!fg) continue; const bg = bgOf(e);
    const fgc = fg.c.map((v, j) => v * fg.a + bg[j] * (1 - fg.a));
    const L1 = lum(fgc), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const fs = parseFloat(cs.fontSize); const bold = parseInt(cs.fontWeight) >= 700; const large = fs >= 24 || (fs >= 18.66 && bold); const need = large ? 3 : 4.5;
    all.push(ratio);
    if (ratio < need) { const k = (e.dataset.testid || e.tagName) + ':' + e.textContent.trim().slice(0, 18); if (!seen.has(k)) { seen.add(k); out.push([k, Math.round(ratio * 100) / 100, need, cs.color]); } }
  }
  return { low: out.slice(0, 8), n: all.length, min: Math.round(Math.min(...all) * 100) / 100 };
}
