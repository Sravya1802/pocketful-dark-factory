"""Independent stage-3 model (written by the adversary from the spec only). Times are integer microseconds since the epoch.
The model is fed with facts observed from write responses (ids, created_at, recorded_at, hold events) and answers what the
spec says /me?as_of&known_at, /statement and correction outcomes must be."""
import re, calendar
INF = 1 << 62
RX = re.compile(r"(\d{4})-(\d\d)-(\d\d)[Tt](\d\d):(\d\d):(\d\d)(?:\.(\d+))?([Zz]|[+-]\d\d:\d\d)$")
def parse(s):
    m = RX.match(s); assert m, s
    y, mo, d, h, mi, se, fr, tz = m.groups(); fr = (fr or "")
    us = int((fr + "000000")[:6]) if fr else 0
    off = 0 if tz in "Zz" else (1 if tz[0] == "+" else -1) * (int(tz[1:3]) * 60 + int(tz[4:6])) * 60
    return (calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(se))) - off) * 1_000_000 + us
def fmt(us, offmin=0, digits=6):
    sec, frac = divmod(us + offmin * 60 * 1_000_000, 1_000_000); t = __import__("time").gmtime(sec)
    s = "%04d-%02d-%02dT%02d:%02d:%02d" % t[:6]
    if digits: s += "." + ("%06d" % frac)[:digits]
    return s + ("+" if offmin >= 0 else "-") + "%02d:%02d" % divmod(abs(offmin), 60)

class Pay:
    def __init__(s, pid, frm, to, amount, eff, rec, kind="plain", refund_of=None, sid=None):
        s.id, s.frm, s.to, s.kind, s.refund_of, s.sid = pid, frm, to, kind, refund_of, sid; s.revs = [(1, amount, eff, rec)]
class Hold:
    def __init__(s, hid, payer, rcv, amount, created, expires):
        s.id, s.payer, s.rcv, s.amount, s.created, s.expires = hid, payer, rcv, amount, created, expires; s.caps = []; s.closed = None
class Model:
    def __init__(s): s.opening = {}; s.pays = {}; s.holds = {}
    # --- views -----------------------------------------------------------
    def sel(s, p, known):
        best = None
        for r in p.revs:
            if r[3] <= known and (best is None or r[0] > best[0]): best = r
        return best
    def moves(s, known=INF):
        out = []
        for p in s.pays.values():
            r = s.sel(p, known)
            if r: out.append((p, r))
        return out
    def total(s, u, as_of=INF, known=INF):
        t = s.opening.get(u, 0)
        for p, r in s.moves(known):
            if r[2] <= as_of:
                if p.to == u: t += r[1]
                if p.frm == u: t -= r[1]
        return t
    def held(s, u, as_of=INF, known=INF):
        h = 0
        for ho in s.holds.values():
            if ho.payer != u or ho.created > known or ho.created > as_of: continue
            caps = sum(a for (t, a) in ho.caps if t <= as_of and t <= known)
            close = ho.expires
            if ho.closed is not None and ho.closed <= known: close = min(close, ho.closed)
            if as_of >= close: continue
            h += ho.amount - caps
        return h
    def view_cur(s, u, t):
        # current view: every recorded movement already took effect (effective_at <= now by construction); only hold expiry depends on the clock
        tt = s.total(u, INF, INF); h = s.held(u, t, INF); return {"balance": tt, "total": tt, "held": h, "available": tt - h}
    def view(s, u, as_of=INF, known=INF):
        t = s.total(u, as_of, known); h = s.held(u, as_of, known); return {"balance": t, "total": t, "held": h, "available": t - h}
    def statement(s, u, frm=None, to=INF, known=INF):
        ents = [(r[2], p.id, p, r) for p, r in s.moves(known) if p.frm == u or p.to == u]
        ents.sort(key=lambda e: (e[0], e[1]))
        bal = s.opening.get(u, 0); opening = None; closing = None; out = []
        for eff, pid, p, r in ents:
            if frm is not None and eff < frm: bal += (r[1] if p.to == u else -r[1]); continue
            if opening is None: opening = bal
            if eff >= to:
                if closing is None: closing = bal
                continue
            d = r[1] if p.to == u else -r[1]; bal += d; out.append({"id": p.id, "delta": d, "balance_after": bal, "revision": r[0], "effective_at": r[2], "recorded_at": r[3], "amount": r[1]})
        if opening is None: opening = bal if frm is not None else s.opening.get(u, 0)
        # opening is the balance immediately before `from`; recompute exactly
        opening = s.opening.get(u, 0) + sum((r[1] if p.to == u else -r[1]) for (e, i, p, r) in ents if frm is not None and e < frm)
        closing = s.opening.get(u, 0) + sum((r[1] if p.to == u else -r[1]) for (e, i, p, r) in ents if e < to)
        return {"opening": opening, "entries": out, "closing": closing}
    # --- correction prediction -------------------------------------------
    def boundaries(s, now):
        ts = set()
        for p, r in s.moves(INF): ts.add(r[2])
        for h in s.holds.values():
            ts.add(h.created); ts.add(h.expires)
            for (t, a) in h.caps: ts.add(t)
            if h.closed is not None: ts.add(h.closed)
        return sorted(t for t in ts if t <= now)
    def overdraft(s, now):
        users = set(s.opening) | {p.frm for p in s.pays.values()} | {p.to for p in s.pays.values()}
        for t in s.boundaries(now):
            for u in users:
                v = s.view(u, t, INF)
                if v["total"] < 0 or v["available"] < 0: return (u, t, v)
        return None
    def predict_correction(s, pid, new_amount, eff, now):
        p = s.pays[pid]; cur = s.sel(p, INF); diff = new_amount - cur[1]
        if diff != 0:
            debited = p.frm if diff > 0 else p.to; cv = s.view_cur(debited, now)
            if cv["available"] < abs(diff): return "insufficient_funds"
        saved = list(p.revs); p.revs.append((cur[0] + 1, new_amount, eff, now + 1))
        try: bad = s.overdraft(now)
        finally: p.revs[:] = saved
        return "historical_overdraft" if bad else "ok"

    # --- stage 4 ------------------------------------------------------------
    def refunded(s, pid):
        return sum(p.revs[0][1] for p in s.pays.values() if p.refund_of == pid)
    def predict_refund(s, actor, pid, amount, now):
        p = s.pays[pid]
        if actor != p.to: return "forbidden"
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1 or amount > 1_000_000_000: return "validation_failed"
        if p.kind == "refund": return "invalid_refund_target"
        cur = s.sel(p, INF)[1]
        if amount > cur - s.refunded(pid): return "refund_exceeds_payment"
        if s.view_cur(actor, now)["available"] < amount: return "insufficient_funds"
        return "ok"
    def predict_single_correction(s, pid, new_amount, eff, now, expected_rev):
        p = s.pays[pid]
        if p.kind in ("capture", "refund", "settlement"): return "linked_payment_immutable"
        cur = s.sel(p, INF)
        if expected_rev != cur[0]: return "stale_revision"
        if new_amount < s.refunded(pid): return "refund_exceeds_payment"
        return s.predict_correction(pid, new_amount, eff, now)
    def predict_batch(s, items, now):
        """items: list of dict(pid, rev, amount, eff). Per-item errors in input order (immutable, stale, refund floor), then
        settlement completeness, identical effective instants of members, combined current available, historical."""
        for it in items:
            p = s.pays[it["pid"]]
            if p.kind in ("capture", "refund"): return "linked_payment_immutable"
            cur = s.sel(p, INF)
            if it["rev"] != cur[0]: return "stale_revision"
            if it["amount"] < s.refunded(it["pid"]): return "refund_exceeds_payment"
        inc = {it["pid"] for it in items}
        for it in items:
            p = s.pays[it["pid"]]
            if p.sid is not None:
                members = {q.id for q in s.pays.values() if q.sid == p.sid}
                if not members <= inc: return "incomplete_settlement"
        by_sid = {}
        for it in items:
            p = s.pays[it["pid"]]
            if p.sid is not None: by_sid.setdefault(p.sid, set()).add(it["eff"])
        if any(len(v) > 1 for v in by_sid.values()): return "validation_failed"
        net = {}
        for it in items:
            p = s.pays[it["pid"]]; cur = s.sel(p, INF)[1]; diff = it["amount"] - cur
            net[p.frm] = net.get(p.frm, 0) - diff; net[p.to] = net.get(p.to, 0) + diff
        for u, d in net.items():
            if s.view_cur(u, now)["available"] + d < 0: return "insufficient_funds"
        saved = {}
        for it in items:
            p = s.pays[it["pid"]]; saved[it["pid"]] = list(p.revs); cur = s.sel(p, INF); p.revs.append((cur[0] + 1, it["amount"], it["eff"], now + 1))
        try: bad = s.overdraft(now)
        finally:
            for pid, r in saved.items(): s.pays[pid].revs[:] = r
        return "historical_overdraft" if bad else "ok"
