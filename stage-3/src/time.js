'use strict';
// Instants are integer microseconds since the Unix epoch (BigInt). The service clock
// follows the wall clock (re-anchored whenever the monotonic timer drifts from it by a
// millisecond or more, e.g. after the host slept), has microsecond resolution between
// wall-clock ticks, and never repeats or goes backwards: every server-assigned instant
// is unique and later than every earlier one.

let anchorWall = BigInt(Date.now()) * 1000n;
let anchorHr = process.hrtime.bigint() / 1000n;
let lastUs = 0n;

function nowUs() {
  const wall = BigInt(Date.now()) * 1000n;
  const hr = process.hrtime.bigint() / 1000n;
  let us = anchorWall + (hr - anchorHr);
  if (us < wall || us >= wall + 1000n) {
    anchorWall = wall;
    anchorHr = hr;
    us = wall;
  }
  if (us <= lastUs) us = lastUs + 1n;
  lastUs = us;
  return us;
}

function pad(n, w = 2) {
  return String(n).padStart(w, '0');
}

const US_PER_MS = 1000n;

// RFC 3339 with microseconds and an explicit +00:00 offset.
function formatUs(us) {
  let ms = us / US_PER_MS;
  let frac = us % US_PER_MS;
  if (frac < 0n) {
    frac += US_PER_MS;
    ms -= 1n;
  }
  const d = new Date(Number(ms));
  const y = d.getUTCFullYear();
  const year = y >= 0 && y <= 9999 ? pad(y, 4) : String(y);
  return year + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) + 'T'
    + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds()) + '.'
    + pad(d.getUTCMilliseconds(), 3) + pad(Number(frac), 3) + '+00:00';
}

function stamp(us) {
  return { us, iso: formatUs(us) };
}

function now() {
  return stamp(nowUs());
}

const INSTANT_RE = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?(Z|([+-])([0-9]{2}):([0-9]{2}))$/;
const DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

function leap(y) {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

// Parses an RFC 3339 instant with an offset. Returns { us, ceilUs } — the instant
// rounded down and up to whole microseconds (equal unless more than six fraction
// digits were given) — or null when the text is not such an instant.
function parseInstant(text) {
  if (typeof text !== 'string') return null;
  const m = INSTANT_RE.exec(text);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  const h = Number(m[4]);
  const mi = Number(m[5]);
  const s = Number(m[6]);
  if (mo < 1 || mo > 12 || d < 1) return null;
  const dim = mo === 2 && leap(y) ? 29 : DAYS[mo - 1];
  if (d > dim || h > 23 || mi > 59 || s > 59) return null;
  let offMin = 0;
  if (m[8] !== 'Z') {
    const oh = Number(m[10]);
    const om = Number(m[11]);
    if (oh > 23 || om > 59) return null;
    offMin = (oh * 60 + om) * (m[9] === '-' ? -1 : 1);
  }
  const base = new Date(0);
  base.setUTCFullYear(y, mo - 1, d);
  base.setUTCHours(h, mi, s, 0);
  const ms = base.getTime();
  if (!Number.isFinite(ms)) return null;
  const frac = m[7] || '';
  const micro = BigInt((frac + '000000').slice(0, 6));
  const rest = frac.length > 6 && /[1-9]/.test(frac.slice(6));
  const us = (BigInt(ms) - BigInt(offMin) * 60000n) * 1000n + micro;
  return { us, ceilUs: rest ? us + 1n : us };
}

module.exports = { nowUs, now, stamp, formatUs, parseInstant };
