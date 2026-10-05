"""Stage-4 world model (refunds, settlements, captures, batches) on top of the stage-3 temporal ledger in oracle.py.
Written from the requirements; independent of any service code. Predicts the outcome of every operation the fuzz issues."""
from datetime import datetime, timezone

from oracle import Ledger, Hold, INF

US_ = None


class World:
    def __init__(self, ledger):
        self.led = ledger
        self.kind = {}        # pid -> 'ordinary' | 'capture' | 'refund' | 'settlement'
        self.sid = {}         # pid -> settlement id
        self.groups = {}      # settlement id -> [pids]
        self.refund_of = {}   # refund pid -> target pid

    # ---- registration
    def add(self, pid, frm, to, amount, created, kind="ordinary", sid=None, target=None):
        self.led.add_payment(pid, frm, to, amount, created)
        self.kind[pid] = kind
        if sid:
            self.sid[pid] = sid
            self.groups.setdefault(sid, []).append(pid)
        if target:
            self.refund_of[pid] = target

    def refunded(self, pid):
        return sum(self.led.latest(r)[1] for r, t in self.refund_of.items() if t == pid)

    def current(self, pid):
        return self.led.latest(pid)[1]

    def avail_now(self, uid, now):
        return self.led.total(uid, INF, INF) - self.led.held(uid, now, INF)

    # ---- single refund prediction: returns (status, code)
    def pred_refund(self, by, pid, amount, now):
        if pid not in self.led.pay:
            return 404, "not_found"
        p = self.led.pay[pid]
        if by != p["to"]:
            return 403, "forbidden"
        if self.kind[pid] == "refund":
            return 422, "invalid_refund_target"
        if amount + self.refunded(pid) > self.current(pid):
            return 422, "refund_exceeds_payment"
        if self.avail_now(by, now) < amount:
            return 409, "insufficient_funds"
        return 201, None

    # ---- item-level flags (shared by single and batch)
    def item_flags(self, pid, er, amount, batch):
        if pid not in self.led.pay:
            return ["unknown"]
        fl = []
        k = self.kind[pid]
        if k in ("capture", "refund") or (k == "settlement" and not batch):
            fl.append("immutable")
        if er != self.led.latest(pid)[0]:
            fl.append("stale")
        if amount < self.refunded(pid):
            fl.append("floor")
        return fl

    def money_flags(self, items, now):
        """items: [(pid, amount, eff)] -> (funds_fail, hist_fail)"""
        led = self.led
        net = {}
        ov = {}
        for pid, amount, eff in items:
            p = led.pay[pid]
            diff = amount - self.current(pid)
            net[p["frm"]] = net.get(p["frm"], 0) - diff
            net[p["to"]] = net.get(p["to"], 0) + diff
            ov[pid] = (led.latest(pid)[0] + 1, amount, eff, INF, "x")
        funds = any(n < 0 and self.avail_now(u, now) + n < 0 for u, n in net.items())
        return funds, led.violates(ov)

    CODES = {"unknown": (404, "not_found"), "immutable": (422, "linked_payment_immutable"), "stale": (409, "stale_revision"), "floor": (422, "refund_exceeds_payment")}

    def pred_single(self, pid, er, amount, eff, now):
        """None when the combination of simultaneous error conditions is not pinned down by the spec (the fuzz then skips the op)"""
        fl = self.item_flags(pid, er, amount, False)
        if len(fl) > 1:
            return None
        if fl:
            return self.CODES[fl[0]]
        funds, hist = self.money_flags([(pid, amount, eff)], now)
        if funds:
            return 409, "insufficient_funds"
        if hist:
            return 409, "historical_overdraft"
        return 201, None

    def pred_batch(self, items, now):
        """items: [(pid, er, amount, eff, invalid)] in input order. None when ambiguous."""
        for pid, er, amount, eff, invalid in items:
            fl = ["invalid"] if invalid else self.item_flags(pid, er, amount, True)
            if len(fl) > 1:
                return None
            if fl:
                return (422, "validation_failed") if fl[0] == "invalid" else self.CODES[fl[0]]
        touched = {}
        for pid, er, amount, eff, invalid in items:
            if pid in self.sid:
                touched.setdefault(self.sid[pid], []).append(eff)
        incomplete = [s for s in touched if {p for p in self.groups[s]} != {i[0] for i in items if self.sid.get(i[0]) == s}]
        differing = [s for s, effs in touched.items() if len(set(effs)) > 1]
        if incomplete and differing:
            return None
        if incomplete:
            return 422, "incomplete_settlement"
        if differing:
            return 422, "validation_failed"
        funds, hist = self.money_flags([(i[0], i[2], i[3]) for i in items], now)
        if funds:
            return 409, "insufficient_funds"
        if hist:
            return 409, "historical_overdraft"
        return 201, None
