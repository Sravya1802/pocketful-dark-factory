'use strict';
// Strict JSON (RFC 8259) parser and serializer that never routes numbers through
// binary floating point. Numbers are kept as their source text (JNum) and are only
// turned into exact BigInt values by `numInfo`. Objects are prototype-less so that
// keys such as "__proto__" are ordinary data.

class JNum {
  constructor(text) {
    this.text = text;
  }
}

class JsonSyntaxError extends Error {}

const MAX_DEPTH = 256;
const NUM_RE = /-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/y;

function parse(text) {
  let i = 0;
  if (text.charCodeAt(0) === 0xfeff) i = 1;
  const n = text.length;

  function fail(msg) {
    throw new JsonSyntaxError(msg + ' at offset ' + i);
  }
  function ws() {
    while (i < n) {
      const c = text.charCodeAt(i);
      if (c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) i++;
      else break;
    }
  }
  function str() {
    // text[i] === '"'
    i++;
    let out = '';
    let start = i;
    while (true) {
      if (i >= n) fail('unterminated string');
      const c = text.charCodeAt(i);
      if (c === 0x22) {
        out += text.slice(start, i);
        i++;
        return out;
      }
      if (c < 0x20) fail('control character in string');
      if (c === 0x5c) {
        out += text.slice(start, i);
        i++;
        if (i >= n) fail('bad escape');
        const e = text[i];
        switch (e) {
          case '"': out += '"'; break;
          case '\\': out += '\\'; break;
          case '/': out += '/'; break;
          case 'b': out += '\b'; break;
          case 'f': out += '\f'; break;
          case 'n': out += '\n'; break;
          case 'r': out += '\r'; break;
          case 't': out += '\t'; break;
          case 'u': {
            const hex = text.slice(i + 1, i + 5);
            if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail('bad unicode escape');
            out += String.fromCharCode(parseInt(hex, 16));
            i += 4;
            break;
          }
          default: fail('bad escape');
        }
        i++;
        start = i;
        continue;
      }
      i++;
    }
  }
  function value(depth) {
    if (depth > MAX_DEPTH) fail('nesting too deep');
    ws();
    if (i >= n) fail('unexpected end');
    const c = text[i];
    if (c === '{') {
      i++;
      const obj = Object.create(null);
      ws();
      if (text[i] === '}') { i++; return obj; }
      while (true) {
        ws();
        if (text[i] !== '"') fail('expected key');
        const k = str();
        ws();
        if (text[i] !== ':') fail('expected colon');
        i++;
        obj[k] = value(depth + 1);
        ws();
        if (text[i] === ',') { i++; continue; }
        if (text[i] === '}') { i++; return obj; }
        fail('expected , or }');
      }
    }
    if (c === '[') {
      i++;
      const arr = [];
      ws();
      if (text[i] === ']') { i++; return arr; }
      while (true) {
        arr.push(value(depth + 1));
        ws();
        if (text[i] === ',') { i++; continue; }
        if (text[i] === ']') { i++; return arr; }
        fail('expected , or ]');
      }
    }
    if (c === '"') return str();
    if (text.startsWith('true', i)) { i += 4; return true; }
    if (text.startsWith('false', i)) { i += 5; return false; }
    if (text.startsWith('null', i)) { i += 4; return null; }
    NUM_RE.lastIndex = i;
    const m = NUM_RE.exec(text);
    if (m && m[0].length > 0) {
      i += m[0].length;
      return new JNum(m[0]);
    }
    fail('unexpected token');
  }

  const v = value(0);
  ws();
  if (i !== n) fail('trailing data');
  return v;
}

const NUM_PARTS = /^(-?)([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$/;
const HUGE_DIGITS = 40n;

// Exact analysis of a JSON number literal.
// Returns { integral, value, canon }: `value` is a BigInt when integral and of
// reasonable magnitude, null when integral but astronomically large.
function numInfo(text) {
  const m = NUM_PARTS.exec(text);
  const neg = m[1] === '-';
  const frac = m[3] || '';
  let digits = (m[2] + frac).replace(/^0+/, '');
  let exp = BigInt(m[4] || '0') - BigInt(frac.length);
  if (digits === '') return { integral: true, value: 0n, canon: '0' };
  const trimmed = digits.replace(/0+$/, '');
  exp += BigInt(digits.length - trimmed.length);
  digits = trimmed;
  const canon = (neg ? '-' : '') + digits + 'e' + exp.toString();
  if (exp < 0n) return { integral: false, value: null, canon };
  if (BigInt(digits.length) + exp > HUGE_DIGITS) return { integral: true, value: null, canon };
  let v = BigInt(digits) * 10n ** exp;
  if (neg) v = -v;
  return { integral: true, value: v, canon };
}

// Integral value of a parsed JSON value, or undefined when it is not an integral number.
function intValue(v) {
  if (typeof v === 'number') return Number.isSafeInteger(v) ? BigInt(v) : undefined;
  if (!(v instanceof JNum)) return undefined;
  const info = numInfo(v.text);
  if (!info.integral || info.value === null) return undefined;
  return info.value;
}

// Canonical text of a parsed JSON value: equal JSON values give equal text
// regardless of key order, whitespace or number spelling.
function canonical(v) {
  if (v === null) return 'null';
  if (v === true) return 'true';
  if (v === false) return 'false';
  if (typeof v === 'string') return JSON.stringify(v);
  if (v instanceof JNum) return 'n' + numInfo(v.text).canon;
  if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
  const keys = Object.keys(v).sort();
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}';
}

// Fingerprint of a request body for idempotency: a digest of its canonical text.
const crypto = require('crypto');
const DIGEST_PREFIX = 's256:';
function digestCanonical(text) {
  return DIGEST_PREFIX + crypto.createHash('sha256').update(text, 'utf8').digest('base64');
}
function fingerprint(v) {
  return digestCanonical(canonical(v));
}

// Serializer for response values: BigInt and integer Number become plain digits.
function stringify(v) {
  if (v === null || v === undefined) return 'null';
  switch (typeof v) {
    case 'string': return JSON.stringify(v);
    case 'bigint': return v.toString();
    case 'boolean': return v ? 'true' : 'false';
    case 'number':
      if (!Number.isSafeInteger(v)) throw new Error('non-integer number in output');
      return String(v);
  }
  if (v instanceof JNum) return v.text;
  if (Array.isArray(v)) return '[' + v.map(stringify).join(',') + ']';
  const parts = [];
  for (const k of Object.keys(v)) {
    if (v[k] === undefined) continue;
    parts.push(JSON.stringify(k) + ':' + stringify(v[k]));
  }
  return '{' + parts.join(',') + '}';
}

// Large bodies (reset fixtures, imported exports) are parsed by the engine's native
// parser, which is much faster and leaner than `parse`. Numbers stay exact: the reviver
// sees each number's source text. A plain integer of at most 15 digits is exactly
// representable and is kept as a number; every other number keeps its text (JNum).
const SMALL_INT = /^-?(?:0|[1-9][0-9]{0,14})$/;
function reviveNumber(key, value, context) {
  if (typeof value !== 'number') return value;
  return SMALL_INT.test(context.source) ? value : new JNum(context.source);
}

function parseLarge(text) {
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  try {
    return JSON.parse(text, reviveNumber);
  } catch (e) {
    if (e instanceof SyntaxError || e instanceof RangeError) throw new JsonSyntaxError(e.message);
    throw e;
  }
}

// Native serializer for large values; BigInt is written as its exact digits.
function stringifyLarge(v) {
  return JSON.stringify(v, (k, x) => (typeof x === 'bigint' ? JSON.rawJSON(x.toString()) : x));
}

// Serializes an object whose array members may be very large into a list of string
// pieces (each at most about `pieceSize` characters) instead of one string, so the
// output is never bounded by the engine's maximum string length. Concatenating the
// pieces gives exactly stringifyLarge(v). Containers nested deeper than `depth` levels
// are serialized whole by the native serializer.
function stringifyPieces(v, pieceSize = 1 << 20, depth = 3) {
  const pieces = [];
  let cur = '';
  const emit = (s) => {
    cur += s;
    if (cur.length >= pieceSize) {
      pieces.push(cur);
      cur = '';
    }
  };
  const walk = (x, d) => {
    if (d >= depth) {
      emit(stringifyLarge(x));
    } else if (Array.isArray(x)) {
      emit('[');
      for (let i = 0; i < x.length; i++) {
        if (i) emit(',');
        walk(x[i], d + 1);
      }
      emit(']');
    } else if (x !== null && typeof x === 'object' && !(x instanceof JNum)) {
      emit('{');
      let first = true;
      for (const k of Object.keys(x)) {
        if (x[k] === undefined) continue;
        if (!first) emit(',');
        first = false;
        emit(JSON.stringify(k) + ':');
        walk(x[k], d + 1);
      }
      emit('}');
    } else {
      emit(stringifyLarge(x));
    }
  };
  walk(v, 0);
  if (cur) pieces.push(cur);
  return pieces;
}

module.exports = { JNum, stringifyPieces, JsonSyntaxError, parse, parseLarge, numInfo, intValue, canonical, fingerprint, digestCanonical,
  DIGEST_PREFIX, stringify, stringifyLarge };
