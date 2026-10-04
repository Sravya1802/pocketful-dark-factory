/* Pocketful browser client. Plain JavaScript, no dependencies, nothing fetched from
   outside this service. Money is handled as integer minor units (BigInt) throughout:
   typed amounts are converted with string arithmetic, never through floating point. */
(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Storage: the session and every unfinished form survive a refresh. Each write
  // submission keeps its idempotency key until its outcome is known, so a retry after a
  // lost response sends the same key and the same body.

  const store = {
    get(k) {
      try {
        const v = localStorage.getItem(k);
        return v === null ? null : JSON.parse(v);
      } catch (e) {
        return null;
      }
    },
    set(k, v) {
      try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage full or blocked */ }
    },
    del(k) {
      try { localStorage.removeItem(k); } catch (e) { /* ignore */ }
    },
  };

  const SESSION_KEY = 'pocketful.session';
  let session = store.get(SESSION_KEY); // { token, userId, displayName, handle }
  if (session && (typeof session.token !== 'string' || typeof session.userId !== 'string')) session = null;

  const userKey = (name) => 'pocketful.u.' + session.userId + '.' + name;

  function saveSession() {
    store.set(SESSION_KEY, session);
  }

  function forgetUserData() {
    const prefix = session ? 'pocketful.u.' + session.userId + '.' : null;
    if (!prefix) return;
    try {
      for (let i = localStorage.length - 1; i >= 0; i--) {
        const k = localStorage.key(i);
        if (k && k.startsWith(prefix)) localStorage.removeItem(k);
      }
    } catch (e) { /* ignore */ }
  }

  function newKey() {
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  }

  // ---------------------------------------------------------------------------
  // DOM helper: h('tag', { class, testid, text, on*: fn, attr: value }, ...children)

  function h(tag, props, ...kids) {
    const node = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === 'class') node.className = v;
        else if (k === 'testid') node.setAttribute('data-testid', v);
        else if (k === 'text') node.textContent = v;
        else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
        else if (k === 'value') node.value = v;
        else if (k === 'checked') node.checked = !!v;
        else node.setAttribute(k, v === true ? '' : String(v));
      }
    }
    for (const kid of kids.flat()) {
      if (kid === undefined || kid === null || kid === false) continue;
      node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return node;
  }

  const $ = (sel, root) => (root || document).querySelector(sel);

  // Replaces a node's children, skipping absent (null/false) parts.
  function put(node, ...kids) {
    node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
  }

  // ---------------------------------------------------------------------------
  // Money

  const MAX_AMOUNT = 1000000000n;
  const profile = { currency: null, minorUnits: 2, userId: null, handle: null, displayName: null };

  function fmt(minor) {
    let v = BigInt(minor);
    const neg = v < 0n;
    if (neg) v = -v;
    let s = v.toString();
    const mu = profile.minorUnits;
    if (mu > 0) {
      s = s.padStart(mu + 1, '0');
      s = s.slice(0, s.length - mu) + '.' + s.slice(s.length - mu);
    }
    return (neg ? '-' : '') + s + ' ' + profile.currency;
  }

  // Minor units -> the decimal a person would type, without the currency.
  function toDecimal(minor) {
    const text = fmt(minor);
    return text.slice(0, text.lastIndexOf(' '));
  }

  function amountExample() {
    return profile.minorUnits === 0 ? '1500' : '15.' + '0'.repeat(profile.minorUnits);
  }

  // Exact conversion of a typed decimal to minor units: returns { value } or { error }.
  function parseAmount(text) {
    const t = String(text).trim();
    const mu = profile.minorUnits;
    if (t === '') return { error: 'Enter an amount.' };
    const m = /^([0-9]+)(?:\.([0-9]+))?$/.exec(t);
    if (!m) return { error: 'Enter the amount as a number, for example ' + amountExample() + '.' };
    const frac = m[2] || '';
    if (frac.length > mu) {
      return { error: mu === 0
        ? 'Amounts in ' + profile.currency + ' are whole numbers — leave out the decimal places.'
        : 'Use at most ' + mu + ' decimal places, for example ' + amountExample() + '.' };
    }
    const value = BigInt(m[1] + frac.padEnd(mu, '0'));
    if (value < 1n) return { error: 'Enter an amount greater than zero.' };
    if (value > MAX_AMOUNT) return { error: 'The most you can move at once is ' + fmt(MAX_AMOUNT) + '.' };
    return { value };
  }

  // Equal split (shares differ by at most one minor unit; extra units go to the
  // earliest participants), the same rule the service applies.
  function splitShares(amount, n) {
    const count = BigInt(n);
    const base = amount / count;
    const extra = amount % count;
    const out = [];
    for (let i = 0; i < n; i++) out.push(BigInt(i) < extra ? base + 1n : base);
    return out;
  }

  function cleanHandle(text) {
    return String(text).trim().replace(/^@+/, '');
  }

  function codePoints(s) {
    return Array.from(s).length;
  }

  // ---------------------------------------------------------------------------
  // Time, for people

  function human(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const now = new Date();
    const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    const day = (x) => x.getFullYear() + '-' + x.getMonth() + '-' + x.getDate();
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    const tomorrow = new Date(now);
    tomorrow.setDate(now.getDate() + 1);
    if (day(d) === day(now)) return 'today at ' + time;
    if (day(d) === day(yesterday)) return 'yesterday at ' + time;
    if (day(d) === day(tomorrow)) return 'tomorrow at ' + time;
    const opts = { day: 'numeric', month: 'short' };
    if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString(undefined, opts) + ' at ' + time;
  }

  const capital = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  // ---------------------------------------------------------------------------
  // API

  class Uncertain extends Error {}

  async function api(method, path, opts) {
    const o = opts || {};
    const headers = { Accept: 'application/json' };
    if (o.body !== undefined) headers['Content-Type'] = 'application/json';
    if (o.auth !== false && session) headers.Authorization = 'Bearer ' + session.token;
    if (o.key) headers['Idempotency-Key'] = o.key;
    let res;
    let text;
    try {
      res = await fetch(path, {
        method,
        headers,
        body: o.body === undefined ? undefined : JSON.stringify(o.body),
        cache: 'no-store',
        credentials: 'same-origin',
      });
      text = await res.text();
    } catch (e) {
      throw new Uncertain('network');
    }
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (e) {
        if (res.ok) throw new Uncertain('unreadable');
      }
    }
    if (res.status === 401 && o.auth !== false) sessionEnded();
    return { status: res.status, ok: res.ok, data };
  }

  function sessionEnded() {
    // Keep unfinished forms (keyed by user) so nothing typed is lost; only the token goes.
    if (session) {
      session = null;
      store.del(SESSION_KEY);
    }
    const next = encodeURIComponent(location.pathname);
    location.replace('/login?next=' + next + '&ended=1');
  }

  // One idempotent submission. The key is reused while the same body is retried after
  // an unknown outcome, and after success (an unchanged resubmission replays the
  // original instead of creating anything new). A refused submission frees the key.
  async function submitOnce(scope, path, body) {
    const fp = JSON.stringify(body);
    const prev = store.get(userKey('attempt.' + scope));
    const reuse = prev && prev.fp === fp && prev.status !== 'refused';
    const key = reuse ? prev.key : newKey();
    store.set(userKey('attempt.' + scope), { key, fp, status: 'sending' });
    let r;
    try {
      r = await api('POST', path, { body, key });
    } catch (e) {
      if (!(e instanceof Uncertain)) throw e;
      store.set(userKey('attempt.' + scope), { key, fp, status: 'uncertain' });
      return { kind: 'uncertain' };
    }
    if (r.status >= 500) {
      store.set(userKey('attempt.' + scope), { key, fp, status: 'uncertain' });
      return { kind: 'uncertain' };
    }
    if (r.ok) {
      store.set(userKey('attempt.' + scope), { key, fp, status: 'done' });
      return { kind: 'ok', data: r.data, replayed: r.status === 200 };
    }
    store.set(userKey('attempt.' + scope), { key, fp, status: 'refused' });
    return { kind: 'refused', status: r.status, error: (r.data && r.data.error) || {} };
  }

  function attemptState(scope, body) {
    const prev = store.get(userKey('attempt.' + scope));
    if (!prev || prev.fp !== JSON.stringify(body)) return null;
    return prev.status === 'sending' ? 'uncertain' : prev.status;
  }

  function explain(error, context) {
    const code = error && error.code;
    switch (code) {
      case 'insufficient_funds':
        return profile.held && profile.held > 0n
          ? 'You don’t have enough available. Money on hold can’t be spent until it is collected, released or expires.'
          : 'You don’t have enough money available for this.';
      case 'not_found':
        return context === 'request' ? 'This request no longer exists.'
          : context === 'hold' ? 'This hold no longer exists.'
            : 'No one on Pocketful has that handle. Check the spelling and try again.';
      case 'self_payment':
        return context === 'hold' ? 'You can’t reserve money for yourself.' : 'You can’t send money to yourself.';
      case 'self_request':
        return 'You can’t request money from yourself.';
      case 'request_not_pending':
        return 'This request has already been paid, declined or cancelled.';
      case 'forbidden':
        return 'You aren’t allowed to do that.';
      case 'authorization_not_open':
        return 'This hold is no longer open.';
      case 'authorization_expired':
        return 'This hold has expired, so it can no longer be collected.';
      case 'capture_exceeds_authorization':
        return 'That is more than is still held.';
      case 'validation_failed':
        return 'Some details aren’t valid. Check the handle, amount and note, then try again.';
      case 'idempotency_key_reuse':
        return 'This was already submitted with different details. Please submit again.';
      default:
        return 'That didn’t go through. Please check the details and try again.';
    }
  }

  // ---------------------------------------------------------------------------
  // Shared UI pieces

  function notice(kind, testid, title, detail) {
    const role = kind === 'error' ? 'alert' : 'status';
    return h('div', { class: 'notice notice-' + kind, role, testid },
      h('div', null, h('strong', { text: title }), detail ? h('span', { text: detail }) : null));
  }

  // A message area that shows at most one notice at a time.
  function messageSlot() {
    const node = h('div', { class: 'msgs', 'aria-live': 'polite' });
    return {
      node,
      show(kind, testid, title, detail) { put(node, notice(kind, testid, title, detail)); },
      clear() { put(node); },
    };
  }

  function setBusy(button, busy, busyText) {
    if (busy) {
      button.dataset.label = button.dataset.label || button.textContent;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      put(button, h('span', { class: 'spinner', 'aria-hidden': 'true' }), busyText);
    } else {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.textContent = button.dataset.label || button.textContent;
    }
  }

  let fieldSeq = 0;
  function field(label, input, opts) {
    const o = opts || {};
    const id = 'f' + (++fieldSeq);
    input.id = id;
    const hintId = o.hint ? id + '-hint' : null;
    if (hintId) input.setAttribute('aria-describedby', hintId);
    let control = input;
    if (o.prefix || o.suffix) {
      control = h('div', { class: 'affix' },
        o.prefix ? h('span', { class: 'prefix', 'aria-hidden': 'true', text: o.prefix }) : null,
        input,
        o.suffix ? h('span', { class: 'suffix', 'aria-hidden': 'true', text: o.suffix }) : null);
    }
    return h('div', { class: 'field' },
      h('label', { for: id }, label, o.optional ? h('span', { class: 'optional', text: ' (optional)' }) : null),
      control,
      hintId ? h('p', { class: 'hint', id: hintId, text: o.hint }) : null);
  }

  function textInput(testid, props) {
    return h('input', Object.assign({ type: 'text', testid, autocomplete: 'off', spellcheck: 'false' }, props || {}));
  }

  function handleInput(testid) {
    return textInput(testid, { autocapitalize: 'none', placeholder: 'handle' });
  }

  function amountInput(testid) {
    return textInput(testid, { inputmode: 'decimal', placeholder: amountExample() });
  }

  function visibilitySelect(testid) {
    return h('select', { testid },
      h('option', { value: 'public', text: 'Public — everyone' }),
      h('option', { value: 'private', text: 'Private — just you two' }));
  }

  // Keeps a form's fields in storage so a refresh never empties it.
  function persistForm(name, inputs) {
    const key = userKey('draft.' + name);
    const saved = store.get(key);
    if (saved) {
      for (const [k, input] of Object.entries(inputs)) {
        if (typeof saved[k] === 'string') {
          if (input.tagName === 'SELECT' && ![...input.options].some((o) => o.value === saved[k])) continue;
          input.value = saved[k];
        } else if (typeof saved[k] === 'boolean') {
          input.checked = saved[k];
        }
      }
    }
    const save = () => {
      const v = {};
      for (const [k, input] of Object.entries(inputs)) v[k] = input.type === 'checkbox' ? input.checked : input.value;
      store.set(key, v);
    };
    for (const input of Object.values(inputs)) {
      input.addEventListener('input', save);
      input.addEventListener('change', save);
    }
    return { save, clear() { store.del(key); } };
  }

  // ---------------------------------------------------------------------------
  // Top bar

  const NAV = [['/', 'Wallet'], ['/requests', 'Requests'], ['/split', 'Split'], ['/authorizations', 'Holds']];

  function renderTopbar() {
    const bar = $('#topbar');
    const brand = h('span', { class: 'brand' }, h('img', { src: '/assets/icon.svg', alt: '' }), 'Pocketful');
    if (!session) {
      const here = location.pathname;
      put(bar, h('div', { class: 'topbar-inner' }, brand,
        h('nav', { class: 'nav', 'aria-label': 'Account' },
          h('a', { href: '/login', 'aria-current': here === '/login' ? 'page' : null, text: 'Log in' }),
          h('a', { href: '/signup', 'aria-current': here === '/signup' ? 'page' : null, text: 'Sign up' }))));
      return;
    }
    const nav = h('nav', { class: 'nav', 'aria-label': 'Main' },
      NAV.map(([href, label]) => h('a', { href, 'aria-current': location.pathname === href ? 'page' : null, text: label })));
    const who = h('div', { class: 'who' },
      h('div', { class: 'who-text' },
        h('span', { class: 'who-name', testid: 'current-user', text: session.displayName || '' }),
        h('span', { class: 'handle', testid: 'current-handle', text: session.handle || '' })),
      h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: 'logout-button', onclick: logout }, 'Log out'));
    put(bar, h('div', { class: 'topbar-inner' }, brand, nav, who));
  }

  function updateIdentity(me) {
    session.displayName = me.display_name;
    session.handle = me.handle;
    saveSession();
    const name = $('[data-testid="current-user"]');
    const handle = $('[data-testid="current-handle"]');
    if (name) name.textContent = me.display_name;
    if (handle) handle.textContent = me.handle;
  }

  function logout() {
    forgetUserData();
    session = null;
    store.del(SESSION_KEY);
    location.assign('/login');
  }

  // ---------------------------------------------------------------------------
  // Balance card. Latest refresh wins: every read carries a sequence number and a
  // response is applied only if no later read has been applied already.

  function balanceCard(opts) {
    const compact = !!(opts && opts.compact);
    // The refresh button refreshes everything the page shows, not only the balance.
    const refreshPage = () => (opts && opts.onRefresh ? opts.onRefresh() : refresh());
    const node = h('section', { class: 'card balance' + (compact ? ' compact' : ''), 'aria-labelledby': 'bal-label' });
    const listeners = [];
    let seq = 0;
    let applied = 0;
    let loaded = false;

    function renderLoading() {
      node.setAttribute('aria-busy', 'true');
      put(node,
        h('p', { class: 'balance-label', id: 'bal-label', text: 'Available to spend' }),
        h('span', { class: 'skeleton', 'aria-hidden': 'true' }),
        h('p', { class: 'balance-hint', role: 'status', text: 'Loading your balance…' }));
    }

    function renderError() {
      node.removeAttribute('aria-busy');
      put(node,
        h('p', { class: 'balance-label', id: 'bal-label', text: 'Available to spend' }),
        h('div', { class: 'state-panel error', role: 'alert' },
          h('strong', { text: 'We couldn’t load your balance.' }),
          h('span', { text: 'Check your connection, then try again.' }),
          h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: () => refresh() }, 'Try again')));
    }

    function render(me) {
      node.removeAttribute('aria-busy');
      const total = BigInt(me.total !== undefined ? me.total : me.balance);
      const held = BigInt(me.held || 0);
      const available = BigInt(me.available !== undefined ? me.available : total - held);
      const refreshBtn = h('button', { type: 'button', class: 'btn btn-secondary btn-small',
        testid: compact ? 'balance-refresh' : 'wallet-refresh', onclick: () => refreshPage() }, 'Refresh');
      put(node,
        h('p', { class: 'balance-label', id: 'bal-label', text: 'Available to spend' }),
        h('p', { class: 'balance-headline', testid: 'wallet-available', 'data-amount': available.toString(),
          text: fmt(available) }),
        h('dl', { class: 'balance-sub' },
          h('div', null, h('dt', { text: 'Total balance' }),
            h('dd', { testid: 'wallet-balance', 'data-amount': total.toString(), text: fmt(total) })),
          held > 0n ? h('div', { class: 'held' }, h('dt', { text: 'On hold' }),
            h('dd', { testid: 'wallet-held', 'data-amount': held.toString(), text: fmt(held) })) : null),
        h('div', { class: 'balance-foot' },
          h('p', { class: 'balance-hint', text: held > 0n
            ? 'Money on hold is reserved for someone to collect, so it can’t be spent yet.'
            : 'Updated ' + human(new Date().toISOString()) + '.' }),
          refreshBtn));
    }

    async function refresh() {
      const mine = ++seq;
      if (!loaded) renderLoading();
      else node.setAttribute('aria-busy', 'true');
      let r;
      try {
        r = await api('GET', '/me');
      } catch (e) {
        r = null;
      }
      if (mine <= applied) return; // a later refresh already won
      applied = mine;
      if (!r || !r.ok || !r.data) {
        node.removeAttribute('aria-busy');
        if (!loaded) renderError();
        return;
      }
      const me = r.data;
      profile.currency = me.currency;
      profile.minorUnits = me.minor_units;
      profile.userId = me.user_id;
      profile.held = BigInt(me.held || 0);
      updateIdentity(me);
      loaded = true;
      render(me);
      listeners.forEach((fn) => fn(me));
    }

    renderLoading();
    return { node, refresh, onLoad: (fn) => listeners.push(fn) };
  }

  // Loads the profile once (currency and minor units are needed to format anything).
  async function loadProfile() {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const r = await api('GET', '/me');
        if (r.ok && r.data) {
          profile.currency = r.data.currency;
          profile.minorUnits = r.data.minor_units;
          profile.userId = r.data.user_id;
          profile.held = BigInt(r.data.held || 0);
          updateIdentity(r.data);
          return true;
        }
        if (r.status === 401) return false;
      } catch (e) { /* retry */ }
      await new Promise((res) => setTimeout(res, 400));
    }
    return false;
  }

  function fatal(main, title, detail) {
    put(main, h('div', { class: 'card' },
      h('div', { class: 'state-panel error', role: 'alert' },
        h('strong', { text: title }),
        h('span', { text: detail }),
        h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: () => location.reload() },
          'Try again'))));
  }

  // ---------------------------------------------------------------------------
  // Activity feed

  function feedCard() {
    const list = h('div');
    const node = h('section', { class: 'card', 'aria-labelledby': 'feed-title' },
      h('div', { class: 'card-head' },
        h('h2', { id: 'feed-title', text: 'Activity' }),
        h('p', { class: 'card-sub', text: 'Your payments, plus public ones between others' })),
      list);
    let seq = 0;
    let applied = 0;
    let items = [];
    let hasMore = false;
    let loaded = false;

    function item(p) {
      const out = p.from_user_id === profile.userId;
      const inbound = p.to_user_id === profile.userId;
      const other = out ? p.to_handle : p.from_handle;
      const tags = [];
      if (p.request_id) tags.push('Request');
      if (p.authorization_id) tags.push('Collected hold');
      if (p.settlement_id) tags.push('Settlement');
      return h('li', { class: 'item', testid: 'activity-item-' + p.payment_id, 'data-visibility': p.visibility },
        h('span', { class: 'avatar ' + (inbound ? 'in' : out ? 'out' : ''), 'aria-hidden': 'true', text: other.charAt(0) }),
        h('div', { class: 'item-main' },
          h('p', { class: 'item-title', testid: 'activity-parties-' + p.payment_id,
            text: '@' + p.from_handle + ' paid @' + p.to_handle }),
          h('p', { class: 'item-note', testid: 'activity-note-' + p.payment_id, text: p.note }),
          h('p', { class: 'item-meta' },
            h('span', { class: 'tag', text: p.visibility === 'private' ? 'Private' : 'Public' }),
            tags.map((t) => h('span', { class: 'tag', text: t })),
            h('span', { text: capital(human(p.created_at)) }))),
        h('div', { class: 'item-side' },
          h('span', { class: 'direction', text: out ? 'Sent' : inbound ? 'Received' : 'Between others' }),
          h('span', { class: 'item-amount' + (inbound ? ' in' : ''), testid: 'activity-amount-' + p.payment_id,
            text: fmt(p.amount) })));
    }

    function render() {
      node.removeAttribute('aria-busy');
      if (!items.length) {
        put(list, h('div', { class: 'state-panel', testid: 'empty-activity' },
          h('strong', { text: 'No activity yet' }),
          h('span', { text: 'Payments you send or receive, and public payments between others, will show up here.' })));
        return;
      }
      put(list,
        h('ul', { class: 'list', testid: 'activity-list' }, items.map(item)),
        hasMore ? h('div', { class: 'list-foot' },
          h('button', { type: 'button', class: 'btn btn-quiet btn-small', onclick: more }, 'Show older activity')) : null);
    }

    function renderLoading() {
      node.setAttribute('aria-busy', 'true');
      put(list, h('div', { role: 'status' },
        h('span', { class: 'skeleton short', 'aria-hidden': 'true' }),
        h('p', { class: 'balance-hint', text: 'Loading activity…' })));
    }

    function renderError() {
      node.removeAttribute('aria-busy');
      put(list, h('div', { class: 'state-panel error', role: 'alert' },
        h('strong', { text: 'We couldn’t load your activity.' }),
        h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: () => refresh() }, 'Try again')));
    }

    async function refresh() {
      const mine = ++seq;
      if (!loaded) renderLoading();
      let r;
      try {
        r = await api('GET', '/activity?limit=50&offset=0');
      } catch (e) {
        r = null;
      }
      if (mine <= applied) return;
      applied = mine;
      if (!r || !r.ok || !r.data) {
        if (!loaded) renderError();
        return;
      }
      loaded = true;
      items = r.data.payments;
      hasMore = r.data.has_more;
      render();
    }

    async function more() {
      const base = applied;
      let r;
      try {
        r = await api('GET', '/activity?limit=50&offset=' + items.length);
      } catch (e) {
        return;
      }
      if (base !== applied || !r.ok || !r.data) return; // a refresh replaced the list meanwhile
      const seen = new Set(items.map((p) => p.payment_id));
      items = items.concat(r.data.payments.filter((p) => !seen.has(p.payment_id)));
      hasMore = r.data.has_more;
      render();
    }

    renderLoading();
    return { node, refresh };
  }

  // ---------------------------------------------------------------------------
  // Forms with an idempotent submission (pay, request, authorise, split)

  // `spec`: { name, testPrefix, title, fields, submitLabel, build(values) -> {body}|{error},
  //           path, success(data, replayed) -> [title, detail], afterSuccess, afterRefusal, context }
  function moneyForm(spec) {
    const msgs = messageSlot();
    const button = h('button', { type: 'submit', class: 'btn btn-primary', testid: spec.testPrefix + '-submit' },
      spec.submitLabel);
    const form = h('form', { novalidate: true, 'aria-label': spec.title }, spec.layout, msgs.node,
      h('div', { class: 'actions' }, button));
    const draft = persistForm(spec.name, spec.inputs);
    let busy = false;

    function current() {
      const v = {};
      for (const [k, input] of Object.entries(spec.inputs)) v[k] = input.type === 'checkbox' ? input.checked : input.value;
      return v;
    }

    // Restore an outcome that was still unknown when the page was left.
    function restore() {
      const built = spec.build(current());
      if (!built.body) return;
      const state = attemptState(spec.name, built.body);
      if (state === 'uncertain') {
        msgs.show('uncertain', spec.testPrefix + '-uncertain', 'We couldn’t confirm whether this went through.',
          'Your details are kept. Submit again to retry safely — it will not be sent twice.');
      }
    }

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (busy) return;
      draft.save();
      const built = spec.build(current());
      if (built.error) {
        msgs.show('error', spec.testPrefix + '-error', built.error);
        return;
      }
      busy = true;
      setBusy(button, true, spec.busyLabel);
      msgs.show('pending', spec.testPrefix + '-pending', spec.pendingText);
      let out;
      try {
        out = await submitOnce(spec.name, spec.path, built.body);
      } finally {
        busy = false;
        setBusy(button, false);
      }
      if (out.kind === 'ok') {
        const [title, detail] = spec.success(out.data, out.replayed, built.body);
        msgs.show('success', spec.testPrefix + '-success', title, detail);
        if (spec.afterSuccess) await spec.afterSuccess(out.data);
      } else if (out.kind === 'refused') {
        msgs.show('error', spec.testPrefix + '-error', explain(out.error, spec.context));
        if (spec.afterRefusal) await spec.afterRefusal(out.error);
      } else {
        msgs.show('uncertain', spec.testPrefix + '-uncertain', 'We couldn’t confirm whether this went through.',
          'The connection dropped before we heard back. Your details are kept — submit again to retry safely; '
          + 'it will not be sent twice.');
      }
      button.textContent = out.kind === 'uncertain' ? 'Retry safely' : spec.submitLabel;
      button.dataset.label = button.textContent;
    });

    for (const input of Object.values(spec.inputs)) {
      input.addEventListener('input', () => {
        const err = msgs.node.querySelector('[data-testid$="-error"]');
        if (err) msgs.clear();
      });
    }

    return { form, msgs, restore };
  }

  function noteError(note) {
    return codePoints(note) > 200 ? 'Notes can be at most 200 characters.' : null;
  }

  function payForm(onChange) {
    const inputs = {
      handle: handleInput('pay-handle'),
      amount: amountInput('pay-amount'),
      visibility: visibilitySelect('pay-visibility'),
      note: textInput('pay-note', { placeholder: 'What’s it for?' }),
    };
    const layout = h('div', { class: 'stack-fields' },
      field('To', inputs.handle, { prefix: '@' }),
      h('div', { class: 'field-row' },
        field('Amount', inputs.amount, { suffix: profile.currency }),
        field('Who can see it', inputs.visibility)),
      field('Note', inputs.note, { optional: true }));
    layout.className = 'field-stack';
    return moneyForm({
      name: 'pay',
      testPrefix: 'pay',
      title: 'Send money',
      inputs,
      layout,
      submitLabel: 'Send money',
      busyLabel: 'Sending…',
      pendingText: 'Sending your payment…',
      path: '/payments',
      context: 'pay',
      build(v) {
        const handle = cleanHandle(v.handle);
        if (!handle) return { error: 'Enter the handle of the person you’re paying.' };
        const amount = parseAmount(v.amount);
        if (amount.error) return { error: amount.error };
        const ne = noteError(v.note);
        if (ne) return { error: ne };
        return { body: { to_handle: handle, amount: Number(amount.value), note: v.note, visibility: v.visibility } };
      },
      success(p, replayed) {
        return replayed
          ? ['Already sent', fmt(p.amount) + ' to @' + p.to_handle + ' went through earlier. Nothing new was sent.']
          : ['Payment sent', fmt(p.amount) + ' is on its way to @' + p.to_handle + '.'];
      },
      afterSuccess: onChange,
      afterRefusal: onChange,
    });
  }

  function requestForm() {
    const inputs = {
      handle: handleInput('request-handle'),
      amount: amountInput('request-amount'),
      note: textInput('request-note', { placeholder: 'What’s it for?' }),
    };
    const layout = h('div', { class: 'field-stack' },
      h('div', { class: 'field-row' },
        field('From', inputs.handle, { prefix: '@' }),
        field('Amount', inputs.amount, { suffix: profile.currency })),
      field('Note', inputs.note, { optional: true }));
    return moneyForm({
      name: 'request',
      testPrefix: 'request',
      title: 'Request money',
      inputs,
      layout,
      submitLabel: 'Send request',
      busyLabel: 'Sending…',
      pendingText: 'Sending your request…',
      path: '/requests',
      context: 'requestform',
      build(v) {
        const handle = cleanHandle(v.handle);
        if (!handle) return { error: 'Enter the handle of the person you’re asking.' };
        const amount = parseAmount(v.amount);
        if (amount.error) return { error: amount.error };
        const ne = noteError(v.note);
        if (ne) return { error: ne };
        return { body: { payer_handle: handle, amount: Number(amount.value), note: v.note } };
      },
      success(r, replayed) {
        return replayed
          ? ['Already requested', 'You asked @' + r.payer_handle + ' for ' + fmt(r.amount) + ' earlier. Nothing new was sent.']
          : ['Request sent', '@' + r.payer_handle + ' has been asked for ' + fmt(r.amount) + '.'];
      },
    });
  }

  function authorizeForm(onChange) {
    const inputs = {
      handle: handleInput('authorize-handle'),
      amount: amountInput('authorize-amount'),
      visibility: visibilitySelect('authorize-visibility'),
      note: textInput('authorize-note', { placeholder: 'What’s it for?' }),
    };
    const layout = h('div', { class: 'field-stack' },
      field('For', inputs.handle, { prefix: '@', hint: 'The person who can collect the money.' }),
      h('div', { class: 'field-row' },
        field('Amount to reserve', inputs.amount, { suffix: profile.currency }),
        field('Who can see the payment', inputs.visibility)),
      field('Note', inputs.note, { optional: true }));
    return moneyForm({
      name: 'authorize',
      testPrefix: 'authorize',
      title: 'Reserve money',
      inputs,
      layout,
      submitLabel: 'Reserve money',
      busyLabel: 'Reserving…',
      pendingText: 'Placing the hold…',
      path: '/authorizations',
      context: 'hold',
      build(v) {
        const handle = cleanHandle(v.handle);
        if (!handle) return { error: 'Enter the handle of the person who will collect.' };
        const amount = parseAmount(v.amount);
        if (amount.error) return { error: amount.error };
        const ne = noteError(v.note);
        if (ne) return { error: ne };
        return { body: { to_handle: handle, amount: Number(amount.value), note: v.note, visibility: v.visibility } };
      },
      success(a, replayed) {
        return replayed
          ? ['Already reserved', fmt(a.amount) + ' was put on hold for @' + a.to_handle + ' earlier. Nothing new was held.']
          : ['Money reserved', fmt(a.amount) + ' is on hold for @' + a.to_handle + ' until ' + human(a.expires_at) + '.'];
      },
      afterSuccess: onChange,
      afterRefusal: onChange,
    });
  }

  // ---------------------------------------------------------------------------
  // Screens

  function pageHead(title, lede) {
    return h('div', { class: 'page-head' }, h('h1', { text: title }), lede ? h('p', { text: lede }) : null);
  }

  function walletScreen(main) {
    const balance = balanceCard({ onRefresh: () => refreshAll() });
    const feed = feedCard();
    const refreshAll = () => Promise.all([balance.refresh(), feed.refresh()]);
    const pay = payForm(refreshAll);
    const request = requestForm();
    put(main,
      h('h1', { class: 'visually-hidden', text: 'Wallet' }),
      h('div', { class: 'grid grid-wallet' },
        h('div', { class: 'stack' },
          balance.node,
          h('section', { class: 'card', 'aria-labelledby': 'pay-title' },
            h('div', { class: 'card-head' }, h('h2', { id: 'pay-title', text: 'Send money' })),
            pay.form),
          h('section', { class: 'card', 'aria-labelledby': 'req-title' },
            h('div', { class: 'card-head' }, h('h2', { id: 'req-title', text: 'Request money' }),
              h('p', { class: 'card-sub', text: 'Answers appear under Requests' })),
            request.form)),
        h('div', { class: 'stack' }, feed.node)));
    pay.restore();
    request.restore();
    refreshAll();
  }

  function requestsScreen(main) {
    const balance = balanceCard({ compact: true, onRefresh: () => refreshAll() });
    const msgs = messageSlot();
    const lists = h('div', { class: 'grid grid-two' });
    let seq = 0;
    let applied = 0;
    let loaded = false;
    const busy = new Set();

    put(main,
      pageHead('Requests', 'Money people have asked you for, and money you have asked others for.'),
      h('div', { class: 'stack' }, balance.node, msgs.node, lists));

    function statusWord(s) {
      return { pending: 'Waiting', paid: 'Paid', declined: 'Declined', cancelled: 'Cancelled' }[s] || capital(s);
    }

    function row(r, incoming) {
      const other = incoming ? r.requester_handle : r.payer_handle;
      const actions = [];
      if (r.status === 'pending' && incoming) {
        const vis = h('select', { testid: 'request-visibility-' + r.request_id },
          h('option', { value: 'public', text: 'Public' }), h('option', { value: 'private', text: 'Private' }));
        const visId = 'vis-' + r.request_id;
        vis.id = visId;
        actions.push(h('div', { class: 'inline-select' }, h('label', { for: visId, text: 'Payment visibility' }), vis));
        const payBtn = h('button', { type: 'button', class: 'btn btn-primary btn-small', testid: 'request-pay-' + r.request_id },
          'Pay ' + fmt(r.amount));
        payBtn.addEventListener('click', () => payRequest(r, vis.value, payBtn));
        const declineBtn = h('button', { type: 'button', class: 'btn btn-quiet btn-small',
          testid: 'request-decline-' + r.request_id }, 'Decline');
        declineBtn.addEventListener('click', () => closeRequest(r, 'decline', declineBtn));
        actions.push(payBtn, declineBtn);
      } else if (r.status === 'pending') {
        const cancelBtn = h('button', { type: 'button', class: 'btn btn-quiet btn-small',
          testid: 'request-cancel-' + r.request_id }, 'Cancel request');
        cancelBtn.addEventListener('click', () => closeRequest(r, 'cancel', cancelBtn));
        actions.push(cancelBtn);
      }
      return h('li', { class: 'item', testid: 'request-item-' + r.request_id, 'data-status': r.status },
        h('span', { class: 'avatar ' + (incoming ? 'out' : 'in'), 'aria-hidden': 'true', text: other.charAt(0) }),
        h('div', { class: 'item-main' },
          h('p', { class: 'item-title', text: incoming ? '@' + other + ' asked you' : 'You asked @' + other }),
          h('p', { class: 'item-note', text: r.note }),
          h('p', { class: 'item-meta' },
            h('span', { class: 'pill pill-' + r.status, text: statusWord(r.status) }),
            h('span', { text: capital(human(r.created_at)) }))),
        h('div', { class: 'item-side' },
          h('span', { class: 'direction', text: incoming ? 'To pay' : 'To receive' }),
          h('span', { class: 'item-amount', testid: 'request-amount-' + r.request_id, text: fmt(r.amount) })),
        actions.length ? h('div', { class: 'item-actions' }, actions) : null);
    }

    function render(all, hasMore) {
      lists.removeAttribute('aria-busy');
      const incoming = all.filter((r) => r.payer_id === profile.userId);
      const outgoing = all.filter((r) => r.requester_id === profile.userId);
      const none = !incoming.length && !outgoing.length;
      const emptyCard = none ? h('section', { class: 'card span-all' },
        h('div', { class: 'state-panel', testid: 'empty-requests' },
          h('strong', { text: 'No requests yet' }),
          h('span', { text: 'When someone asks you for money, or you ask someone from your wallet, it will appear here.' })))
        : null;
      const section = (title, id, testid, rows, empty, isIncoming) => h('section', { class: 'card', 'aria-labelledby': id },
        h('div', { class: 'card-head' }, h('h2', { id, text: title }),
          h('p', { class: 'card-sub', text: rows.filter((r) => r.status === 'pending').length + ' waiting' })),
        h('ul', { class: 'list', testid }, rows.map((r) => row(r, isIncoming))),
        rows.length ? null : h('p', { class: 'hint', text: empty }));
      put(lists, emptyCard,
        section('Asked of you', 'in-title', 'incoming-list', incoming, 'Nobody has asked you for money.', true),
        section('You asked', 'out-title', 'outgoing-list', outgoing, 'You haven’t asked anyone for money.', false));
      if (hasMore) lists.append(h('p', { class: 'hint', text: 'Showing your 200 most recent requests.' }));
    }

    async function refresh() {
      const mine = ++seq;
      if (!loaded) {
        lists.setAttribute('aria-busy', 'true');
        put(lists, h('section', { class: 'card', role: 'status' },
          h('span', { class: 'skeleton short', 'aria-hidden': 'true' }), h('p', { class: 'balance-hint', text: 'Loading requests…' })));
      }
      let r;
      try {
        r = await api('GET', '/requests?limit=200&offset=0');
      } catch (e) {
        r = null;
      }
      if (mine <= applied) return;
      applied = mine;
      if (!r || !r.ok || !r.data) {
        if (!loaded) {
          lists.removeAttribute('aria-busy');
          put(lists, h('section', { class: 'card' }, h('div', { class: 'state-panel error', role: 'alert' },
            h('strong', { text: 'We couldn’t load your requests.' }),
            h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: () => refresh() }, 'Try again'))));
        }
        return;
      }
      loaded = true;
      render(r.data.requests, r.data.has_more);
    }

    const refreshAll = () => Promise.all([balance.refresh(), refresh()]);

    async function payRequest(r, visibility, button) {
      if (busy.has(r.request_id)) return;
      busy.add(r.request_id);
      setBusy(button, true, 'Paying…');
      const out = await submitOnce('reqpay.' + r.request_id, '/requests/' + encodeURIComponent(r.request_id) + '/pay',
        { visibility });
      busy.delete(r.request_id);
      setBusy(button, false);
      if (out.kind === 'ok') {
        msgs.show('success', 'request-success', 'Paid', fmt(out.data.amount) + ' sent to @' + out.data.to_handle + '.');
      } else if (out.kind === 'refused') {
        msgs.show('error', 'request-error', explain(out.error, 'request'));
      } else {
        msgs.show('uncertain', 'request-uncertain', 'We couldn’t confirm whether this payment went through.',
          'Press Pay again to retry safely — it will not be paid twice.');
        return;
      }
      await refreshAll();
    }

    async function closeRequest(r, action, button) {
      if (busy.has(r.request_id)) return;
      busy.add(r.request_id);
      setBusy(button, true, action === 'decline' ? 'Declining…' : 'Cancelling…');
      let res;
      try {
        res = await api('POST', '/requests/' + encodeURIComponent(r.request_id) + '/' + action);
      } catch (e) {
        res = null;
      }
      busy.delete(r.request_id);
      setBusy(button, false);
      if (res && res.ok) {
        msgs.show('success', 'request-success', action === 'decline' ? 'Request declined' : 'Request cancelled');
      } else if (res && res.status < 500) {
        msgs.show('error', 'request-error', explain(res.data && res.data.error, 'request'));
      } else {
        msgs.show('uncertain', 'request-uncertain', 'We couldn’t confirm that change.',
          'The list below shows the latest state we could load.');
      }
      await refreshAll();
    }

    refreshAll();
  }

  function splitScreen(main) {
    const inputs = {
      amount: amountInput('split-amount'),
      handles: textInput('split-handles', { autocapitalize: 'none', placeholder: 'ada, bob, cy' }),
      note: textInput('split-note', { placeholder: 'Dinner, tickets, rent…' }),
    };
    const preview = h('div', { 'aria-live': 'polite' });

    function parse(v) {
      const handles = String(v.handles).split(',').map(cleanHandle).filter((x) => x.length);
      const amount = parseAmount(v.amount);
      return { handles, amount };
    }

    function build(v) {
      const { handles, amount } = parse(v);
      if (amount.error) return { error: amount.error };
      if (!handles.length) return { error: 'Add at least one person, separated by commas.' };
      const seen = new Set();
      for (const x of handles) {
        if (seen.has(x)) return { error: '@' + x + ' is listed twice. Each person can appear once.' };
        seen.add(x);
      }
      const ne = noteError(v.note);
      if (ne) return { error: ne };
      return { body: { amount: Number(amount.value), participant_handles: handles, note: v.note } };
    }

    function renderPreview() {
      const v = { amount: inputs.amount.value, handles: inputs.handles.value };
      const { handles, amount } = parse(v);
      const dupes = handles.length !== new Set(handles).size;
      if (amount.error || !handles.length || dupes) {
        put(preview, h('div', { class: 'preview', testid: 'split-preview' },
          h('div', { class: 'state-panel' },
            h('strong', { text: 'Shares appear here' }),
            h('span', { text: dupes ? 'Each person can appear only once.'
              : 'Enter the total and the people sharing it to see who owes what.' }))));
        return;
      }
      const shares = splitShares(amount.value, handles.length);
      const others = handles.filter((x) => x !== profile.handle);
      const asked = shares.reduce((sum, s, i) => (handles[i] === profile.handle ? sum : sum + s), 0n);
      put(preview,
        h('div', { class: 'preview', testid: 'split-preview' },
          handles.map((x, i) => h('div', { class: 'share' },
            h('span', { class: 'who-share' }, '@' + x, x === profile.handle ? h('span', { class: 'you', text: ' (you)' }) : null),
            h('span', { class: 'share-amount', testid: 'split-share-' + x, text: fmt(shares[i]) })))),
        h('p', { class: 'hint', text: others.length
          ? 'You’ll ask ' + others.length + (others.length === 1 ? ' person' : ' people') + ' for ' + fmt(asked) + ' in total.'
          : 'Only you are listed, so no requests will be sent.' }));
    }

    const layout = h('div', { class: 'field-stack' },
      field('Total amount', inputs.amount, { suffix: profile.currency }),
      field('People sharing it', inputs.handles, { hint: 'Handles separated by commas, in order. Include yourself to take a share.' }),
      field('Note', inputs.note, { optional: true }));

    const form = moneyForm({
      name: 'split',
      testPrefix: 'split',
      title: 'Split a bill',
      inputs,
      layout,
      submitLabel: 'Send requests',
      busyLabel: 'Sending…',
      pendingText: 'Creating the split…',
      path: '/splits',
      context: 'split',
      build,
      success(sp, replayed) {
        const n = sp.requests.length;
        const detail = n ? n + (n === 1 ? ' request' : ' requests') + ' for ' + fmt(sp.amount) + ' split ' + sp.shares.length + ' ways.'
          : 'Only you were listed, so no requests were needed.';
        return replayed ? ['Already split', 'This split was created earlier. ' + detail] : ['Split created', detail];
      },
    });

    inputs.amount.addEventListener('input', renderPreview);
    inputs.handles.addEventListener('input', renderPreview);

    put(main,
      pageHead('Split a bill', 'Paid for something shared? Split it evenly and ask everyone else for their share.'),
      h('div', { class: 'grid grid-two' },
        h('section', { class: 'card', 'aria-labelledby': 'split-title' },
          h('div', { class: 'card-head' }, h('h2', { id: 'split-title', text: 'Details' })),
          form.form),
        h('section', { class: 'card', 'aria-labelledby': 'shares-title' },
          h('div', { class: 'card-head' }, h('h2', { id: 'shares-title', text: 'Shares' }),
            h('p', { class: 'card-sub', text: 'Exactly what each person will be asked for' })),
          preview)));
    renderPreview();
    form.restore();
  }

  function authorizationsScreen(main) {
    const balance = balanceCard({ compact: true, onRefresh: () => refreshAll() });
    const msgs = messageSlot();
    const listBox = h('div');
    let seq = 0;
    let applied = 0;
    let loaded = false;
    let expiryTimer = null;
    const busy = new Set();

    const refreshAll = () => Promise.all([balance.refresh(), refresh()]);
    const form = authorizeForm(refreshAll);

    put(main,
      pageHead('Holds', 'Reserve money for someone to collect later. It stays in your wallet but can’t be spent '
        + 'until it is collected, released or expires.'),
      h('div', { class: 'grid grid-holds' },
        h('div', { class: 'stack' },
          balance.node,
          h('section', { class: 'card', 'aria-labelledby': 'auth-title' },
            h('div', { class: 'card-head' }, h('h2', { id: 'auth-title', text: 'Reserve money' })),
            form.form)),
        h('section', { class: 'card', 'aria-labelledby': 'holds-title' },
          h('div', { class: 'card-head' }, h('h2', { id: 'holds-title', text: 'Your holds' }),
            h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: 'authorizations-refresh',
              onclick: () => refreshAll() }, 'Refresh')),
          msgs.node,
          listBox)));

    const statusWord = { open: 'Held', captured: 'Collected', voided: 'Released', expired: 'Expired' };

    function row(a) {
      const outgoing = a.from_user_id === profile.userId;
      const other = outgoing ? a.to_handle : a.from_handle;
      const remaining = BigInt(a.remaining_amount !== undefined ? a.remaining_amount
        : a.status === 'open' ? BigInt(a.amount) - BigInt(a.captured_amount || 0) : 0);
      const captured = BigInt(a.captured_amount || 0);
      const side = [h('span', { class: 'direction', text: 'Reserved' }),
        h('span', { class: 'item-amount', testid: 'authorization-amount-' + a.authorization_id, text: fmt(a.amount) })];
      const facts = [];
      if (a.status === 'captured') {
        facts.push(h('span', null, 'Collected ',
          h('strong', { testid: 'authorization-captured-' + a.authorization_id, text: fmt(captured) })));
      } else if (captured > 0n) {
        facts.push(h('span', null, 'Collected so far ', h('strong', { text: fmt(captured) })));
      }
      if (a.status === 'open') facts.push(h('span', null, 'Still held ', h('strong', { text: fmt(remaining) })));
      const when = a.status === 'open' ? 'Expires ' + human(a.expires_at)
        : a.status === 'expired' ? 'Expired ' + human(a.expires_at) : 'Was due to expire ' + human(a.expires_at);
      const actions = [];
      if (a.status === 'open' && !outgoing) {
        const amt = amountInput('authorization-capture-amount-' + a.authorization_id);
        amt.value = toDecimal(remaining);
        const keep = h('input', { type: 'checkbox', testid: 'authorization-keep-open-' + a.authorization_id });
        const keepId = 'keep-' + a.authorization_id;
        keep.id = keepId;
        const btn = h('button', { type: 'submit', class: 'btn btn-primary btn-small',
          testid: 'authorization-capture-' + a.authorization_id }, 'Collect');
        const f = h('form', { class: 'capture', novalidate: true, 'aria-label': 'Collect from @' + other },
          field('Amount to collect', amt, { suffix: profile.currency }),
          btn,
          h('label', { class: 'check', for: keepId }, keep, 'Keep the rest on hold for a later collection'));
        f.addEventListener('submit', (ev) => {
          ev.preventDefault();
          capture(a, amt.value, keep.checked, btn);
        });
        actions.push(f);
      } else if (a.status === 'open') {
        const voidBtn = h('button', { type: 'button', class: 'btn btn-danger btn-small',
          testid: 'authorization-void-' + a.authorization_id }, 'Release hold');
        voidBtn.addEventListener('click', () => release(a, voidBtn));
        actions.push(voidBtn);
      }
      return h('li', { class: 'item', testid: 'authorization-item-' + a.authorization_id, 'data-status': a.status },
        h('span', { class: 'avatar hold', 'aria-hidden': 'true', text: other.charAt(0) }),
        h('div', { class: 'item-main' },
          h('p', { class: 'item-title', text: outgoing ? 'You reserved for @' + other : '@' + other + ' reserved for you' }),
          h('p', { class: 'item-note', text: a.note }),
          h('p', { class: 'item-meta' },
            h('span', { class: 'pill pill-' + a.status, text: statusWord[a.status] || capital(a.status) }),
            h('span', { class: 'tag', text: a.visibility === 'private' ? 'Private' : 'Public' }),
            facts),
          h('p', { class: 'item-meta' },
            h('span', { text: when }),
            h('span', { class: 'mono', title: 'Exact expiry time' },
              h('span', { class: 'visually-hidden', text: 'Exact expiry: ' }),
              h('span', { testid: 'authorization-expires-' + a.authorization_id, text: a.expires_at })))),
        h('div', { class: 'item-side' }, side),
        actions.length ? h('div', { class: 'item-actions' }, actions) : null);
    }

    function scheduleExpiry(items) {
      if (expiryTimer) clearTimeout(expiryTimer);
      expiryTimer = null;
      let soonest = Infinity;
      for (const a of items) {
        if (a.status !== 'open') continue;
        const t = Date.parse(a.expires_at);
        if (Number.isFinite(t)) soonest = Math.min(soonest, t);
      }
      if (!Number.isFinite(soonest)) return;
      const delay = Math.max(0, soonest - Date.now()) + 500;
      if (delay < 2147483647) expiryTimer = setTimeout(() => refreshAll(), delay);
    }

    function render(items, hasMore) {
      listBox.removeAttribute('aria-busy');
      scheduleExpiry(items);
      if (!items.length) {
        put(listBox, h('div', { class: 'state-panel', testid: 'empty-authorizations' },
          h('strong', { text: 'No holds yet' }),
          h('span', { text: 'Money you reserve for someone, or that someone reserves for you, will appear here.' })));
        return;
      }
      put(listBox, h('ul', { class: 'list', testid: 'authorization-list' }, items.map(row)),
        hasMore ? h('p', { class: 'hint', text: 'Showing your 200 most recent holds.' }) : null);
    }

    async function refresh() {
      const mine = ++seq;
      if (!loaded) {
        listBox.setAttribute('aria-busy', 'true');
        put(listBox, h('div', { role: 'status' }, h('span', { class: 'skeleton short', 'aria-hidden': 'true' }),
          h('p', { class: 'balance-hint', text: 'Loading holds…' })));
      }
      let r;
      try {
        r = await api('GET', '/authorizations?limit=200&offset=0');
      } catch (e) {
        r = null;
      }
      if (mine <= applied) return;
      applied = mine;
      if (!r || !r.ok || !r.data) {
        if (!loaded) {
          listBox.removeAttribute('aria-busy');
          put(listBox, h('div', { class: 'state-panel error', role: 'alert' },
            h('strong', { text: 'We couldn’t load your holds.' }),
            h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: () => refresh() }, 'Try again')));
        }
        return;
      }
      loaded = true;
      render(r.data.authorizations, r.data.has_more);
    }

    async function capture(a, amountText, keepOpen, button) {
      if (busy.has(a.authorization_id)) return;
      const amount = parseAmount(amountText);
      if (amount.error) {
        msgs.show('error', 'authorization-error', amount.error);
        return;
      }
      const body = keepOpen ? { amount: Number(amount.value), final: false } : { amount: Number(amount.value) };
      busy.add(a.authorization_id);
      setBusy(button, true, 'Collecting…');
      const out = await submitOnce('capture.' + a.authorization_id,
        '/authorizations/' + encodeURIComponent(a.authorization_id) + '/capture', body);
      busy.delete(a.authorization_id);
      setBusy(button, false);
      if (out.kind === 'ok') {
        msgs.show('success', 'authorization-success', 'Collected',
          fmt(out.data.amount) + ' from @' + out.data.from_handle + ' is now in your wallet.');
      } else if (out.kind === 'refused') {
        msgs.show('error', 'authorization-error', explain(out.error, 'hold'));
      } else {
        msgs.show('uncertain', 'authorization-uncertain', 'We couldn’t confirm whether this was collected.',
          'Press Collect again with the same amount to retry safely — it will not be collected twice.');
        return;
      }
      await refreshAll();
    }

    async function release(a, button) {
      if (busy.has(a.authorization_id)) return;
      busy.add(a.authorization_id);
      setBusy(button, true, 'Releasing…');
      let res;
      try {
        res = await api('POST', '/authorizations/' + encodeURIComponent(a.authorization_id) + '/void');
      } catch (e) {
        res = null;
      }
      busy.delete(a.authorization_id);
      setBusy(button, false);
      if (res && res.ok) {
        msgs.show('success', 'authorization-success', 'Hold released',
          fmt(a.remaining_amount !== undefined ? a.remaining_amount : a.amount) + ' is available to spend again.');
      } else if (res && res.status < 500) {
        msgs.show('error', 'authorization-error', explain(res.data && res.data.error, 'hold'));
      } else {
        msgs.show('uncertain', 'authorization-uncertain', 'We couldn’t confirm that the hold was released.',
          'The list shows the latest state we could load.');
      }
      await refreshAll();
    }

    form.restore();
    refreshAll();
  }

  // ---------------------------------------------------------------------------
  // Signup and login

  function safeNext() {
    const next = new URLSearchParams(location.search).get('next');
    return next && NAV.some(([href]) => href === next) ? next : '/';
  }

  function authScreen(main, mode) {
    const signup = mode === 'signup';
    const errorBox = h('div', { 'aria-live': 'assertive' });
    const inputs = signup ? {
      name: textInput('signup-display-name', { autocomplete: 'name', spellcheck: 'false' }),
      email: h('input', { type: 'email', testid: 'signup-email', autocomplete: 'email', autocapitalize: 'none' }),
      password: h('input', { type: 'password', testid: 'signup-password', autocomplete: 'new-password' }),
    } : {
      email: h('input', { type: 'email', testid: 'login-email', autocomplete: 'email', autocapitalize: 'none' }),
      password: h('input', { type: 'password', testid: 'login-password', autocomplete: 'current-password' }),
    };
    const button = h('button', { type: 'submit', class: 'btn btn-primary btn-block', testid: signup ? 'signup-submit' : 'login-submit' },
      signup ? 'Create account' : 'Log in');
    const form = h('form', { novalidate: true },
      signup ? field('Your name', inputs.name) : null,
      field('Email', inputs.email, signup ? { hint: 'Your handle is made from the part before the @.' } : null),
      field('Password', inputs.password, signup ? { hint: 'At least 8 characters.' } : null),
      errorBox,
      button);

    const showError = (text) => put(errorBox,
      h('div', { class: 'notice notice-error', role: 'alert', testid: 'auth-error' }, h('div', null, h('strong', { text }))));

    if (new URLSearchParams(location.search).get('ended') === '1') {
      put(errorBox, notice('pending', 'auth-info', 'Please log in again to continue.'));
    }
    // Already signed in: say so, and still offer the form to switch accounts.
    const signedIn = session ? h('div', { class: 'notice notice-success', role: 'status' },
      h('div', null, h('strong', { text: 'You’re logged in as ' + (session.displayName || 'yourself') + '.' }),
        h('span', null, h('a', { href: '/', text: 'Go to your wallet' }),
          signup ? ', or create another account below.' : ', or log in as someone else below.'))) : null;

    let busy = false;
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (busy) return;
      put(errorBox);
      const email = inputs.email.value.trim();
      const password = inputs.password.value;
      if (signup) {
        if (!inputs.name.value.trim()) return showError('Enter your name.');
        if (!/^[^\s@]+@[^\s@]+$/.test(email)) return showError('Enter an email address like name@example.com.');
        if (Array.from(password).length < 8) return showError('Choose a password of at least 8 characters.');
      } else if (!email || !password) {
        return showError('Enter your email and password.');
      }
      busy = true;
      setBusy(button, true, signup ? 'Creating account…' : 'Logging in…');
      let r;
      try {
        r = await api('POST', signup ? '/auth/signup' : '/auth/login', {
          auth: false,
          body: signup ? { email, password, display_name: inputs.name.value.trim() } : { email, password },
        });
      } catch (e) {
        r = null;
      }
      busy = false;
      setBusy(button, false);
      if (!r) return showError('We couldn’t reach Pocketful. Check your connection and try again.');
      if (r.ok && r.data && r.data.token) {
        if (session && session.userId !== r.data.user_id) forgetUserData();
        session = { token: r.data.token, userId: r.data.user_id, displayName: r.data.display_name, handle: '' };
        saveSession();
        location.assign(safeNext());
        return;
      }
      const code = r.data && r.data.error && r.data.error.code;
      if (code === 'unauthenticated') return showError('That email and password don’t match an account.');
      if (code === 'email_taken') return showError('An account with this email already exists. Log in instead.');
      if (code === 'handle_taken') {
        return showError('The handle made from this email is already taken. Try a different email address.');
      }
      if (code === 'validation_failed') return showError('Check your details: a valid email and a password of at least 8 characters.');
      return showError('Something went wrong. Please try again.');
    });

    put(main, h('div', { class: 'auth-wrap' },
      h('section', { class: 'card' },
        h('h1', { text: signup ? 'Create your account' : 'Welcome back' }),
        h('p', { class: 'lede', text: signup ? 'Send, request and split money with friends in seconds.'
          : 'Log in to see your balance and activity.' }),
        signedIn,
        form),
      h('p', { class: 'auth-switch' }, signup ? 'Already have an account? ' : 'New to Pocketful? ',
        h('a', { href: signup ? '/login' : '/signup', text: signup ? 'Log in' : 'Create an account' }))));
  }

  function notFoundScreen(main) {
    put(main, h('section', { class: 'card' },
      h('div', { class: 'state-panel' },
        h('strong', { text: 'We couldn’t find that page' }),
        h('span', { text: 'The link may be out of date.' }),
        h('a', { href: '/', class: 'btn btn-secondary btn-small', text: 'Go to your wallet' }))));
  }

  // ---------------------------------------------------------------------------
  // Boot

  async function boot() {
    const screen = document.body.dataset.screen;
    const main = $('#main');
    if (screen === 'login' || screen === 'signup') {
      renderTopbar();
      authScreen(main, screen);
      return;
    }
    if (screen === 'notfound') {
      renderTopbar();
      notFoundScreen(main);
      return;
    }
    if (!session) {
      location.replace('/login?next=' + encodeURIComponent(location.pathname));
      return;
    }
    renderTopbar();
    if (!(await loadProfile())) {
      if (session) fatal(main, 'We couldn’t load your wallet.', 'Check your connection, then try again.');
      return;
    }
    const screens = { wallet: walletScreen, requests: requestsScreen, split: splitScreen, authorizations: authorizationsScreen };
    (screens[screen] || notFoundScreen)(main);
  }

  boot();
})();
