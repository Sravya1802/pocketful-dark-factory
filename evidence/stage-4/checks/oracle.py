"""Independent model of the stage-3 temporal ledger, written from the requirements. Used by the fuzz/model checks.
Times are aware datetimes (microsecond precision)."""
from datetime import datetime, timezone

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
INF = datetime(9999, 1, 1, tzinfo=UTC)


class Hold:
    def __init__(self, hid, uid, amount, created, expires):
        self.id, self.uid, self.amount, self.created, self.expires = hid, uid, amount, created, expires
        self.captures = []       # (time, amount) every capture, nonfinal and final
        self.close_kind = None   # 'void' | 'final' | None (expiry is implied by the deadline)
        self.closed_at = None

    def held(self, T, K):
        """hold remaining in the view (as_of T, known_at K); events other than expiry are known at their own time, expiry once creation is known"""
        if self.created > K or T < self.created:
            return 0
        rel = [self.expires]
        if self.close_kind in ("void", "final") and self.closed_at <= K:
            rel.append(self.closed_at)
        if T >= min(rel):
            return 0
        return self.amount - sum(a for t, a in self.captures if t <= T and t <= K)


class Ledger:
    def __init__(self, opening):
        self.opening = dict(opening)  # uid -> opening balance
        self.pay = {}                 # pid -> dict(frm,to,revs=[(rev,amount,eff,rec,reason)])
        self.holds = []

    def add_payment(self, pid, frm, to, amount, created):
        self.pay[pid] = {"frm": frm, "to": to, "revs": [(1, amount, created, created, "")]}

    def latest(self, pid):
        return self.pay[pid]["revs"][-1]

    def add_rev(self, pid, rev, amount, eff, rec, reason):
        self.pay[pid]["revs"].append((rev, amount, eff, rec, reason))

    def selected(self, pid, K):
        best = None
        for r in self.pay[pid]["revs"]:
            if r[3] <= K:
                best = r
        return best

    def movements(self, K=INF, overrides=None):
        """[(eff, pid, frm, to, amount, rev, rec)] for selected revisions"""
        out = []
        for pid, p in self.pay.items():
            r = self.selected(pid, K) if not overrides or pid not in overrides else overrides[pid]
            if r:
                out.append((r[2], pid, p["frm"], p["to"], r[1], r[0], r[3]))
        out.sort(key=lambda m: (m[0], m[1]))
        return out

    def total(self, uid, T=INF, K=INF, overrides=None):
        b = self.opening.get(uid, 0)
        for eff, pid, f, t, a, rev, rec in self.movements(K, overrides):
            if eff <= T:
                b += -a if f == uid else (a if t == uid else 0)
        return b

    def held(self, uid, T=INF, K=INF):
        return sum(h.held(T, K) for h in self.holds if h.uid == uid)

    def me(self, uid, T=INF, K=INF):
        tot = self.total(uid, T, K)
        h = self.held(uid, T, K)
        return {"balance": tot, "total": tot, "held": h, "available": tot - h}

    def statement(self, uid, frm=None, to=None, K=INF, limit=50, offset=0):
        to = INF if to is None else to
        base = self.opening.get(uid, 0)
        run, pre, entries = base, 0, []
        for eff, pid, f, t, a, rev, rec in self.movements(K):
            if uid not in (f, t):
                continue
            d = -a if f == uid else a
            if frm is not None and eff < frm:
                pre += d
                run += d
                continue
            if eff >= to:
                continue
            run += d
            entries.append({"pid": pid, "delta": d, "balance_after": run, "revision": rev, "effective_at": eff, "recorded_at": rec, "amount": a})
        opening = base + pre
        closing = opening + sum(e["delta"] for e in entries)
        return {"opening_balance": opening, "closing_balance": closing, "entries": entries[offset:offset + limit], "all": entries,
                "has_more": len(entries) > offset + limit}

    # ---- correction acceptance (mirrors the stage-3 rules)
    def boundaries(self, overrides=None):
        ts = {m[0] for m in self.movements(INF, overrides)}
        for h in self.holds:
            ts |= {h.created, h.expires}
            ts |= {t for t, a in h.captures}
            if h.closed_at:
                ts.add(h.closed_at)
        return sorted(ts)

    def violates(self, overrides=None):
        users = set(self.opening)
        for pid, p in self.pay.items():
            users |= {p["frm"], p["to"]}
        for uid in users:
            if self.opening.get(uid, 0) < 0:
                return True
        for t in self.boundaries(overrides):
            # a boundary in the model includes every hold event; payments only matter at their effective times
            for uid in users:
                tot = self.total(uid, t, INF, overrides)
                if tot < 0 or tot - self.held(uid, t, INF) < 0:
                    return True
        return False
