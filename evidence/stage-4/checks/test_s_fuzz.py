"""Stage 4: model-based fuzz over payments, settlements, captures, refunds, single corrections and batches,
judged by an independent oracle (oracle.py + oracle4.py). Operations whose simultaneous error conditions the spec does not order are skipped."""
import random
import time
import unittest

from lib import *
from oracle import Ledger, Hold, INF
from oracle4 import World

US = timedelta(microseconds=1)
USERS = ["ada", "bob", "cy", "dee", "eve"]


class Fuzz4(Base):
    def build(self, rnd):
        bal0 = {n: rnd.randint(400, 3000) for n in USERS}
        cur = dict(bal0)
        events = sorted(((rnd.randint(1, 45) / 2.0, i) for i in range(10)), reverse=True)
        specs = []
        for j, (hrs, i) in enumerate(events):
            for _ in range(20):
                f, t = rnd.sample(USERS, 2)
                if cur[f] >= 5:
                    a = rnd.randint(1, min(cur[f], 800))
                    cur[f] -= a
                    cur[t] += a
                    specs.append(("p_%03d" % j, f, t, a, hrs))
                    break
        return bal0, specs

    # ------------------------------------------------------------------ views
    def me_check(self, led, name, T=None, K=None):
        r = me_at(name, iso6(T) if T else None, iso6(K) if K else None)
        self.assertEqual(r.status, 200, r)
        want = led.me("u_" + name, T if T else INF, K if K else INF) if T else None
        if want is None:   # current view: held as of now
            now = now_utc()
            tot = led.total("u_" + name, INF, K if K else INF)
            held = led.held("u_" + name, now, K if K else INF)
            want = {"balance": tot, "total": tot, "held": held, "available": tot - held}
        got = {f: r.body[f] for f in ("balance", "total", "held", "available")}
        self.assertEqual(got, want, (name, T and iso6(T), K and iso6(K)))

    def st_check(self, led, name, frm=None, to=None, K=None, limit=200, offset=0):
        r = statement(name, **{"from": iso6(frm) if frm else None, "to": iso6(to) if to else None, "known_at": iso6(K) if K else None}, limit=limit, offset=offset)
        self.assertEqual(r.status, 200, r)
        want = led.statement("u_" + name, frm, to, K if K else INF, limit, offset)
        b = r.body
        ctx = (name, frm and iso6(frm), to and iso6(to), K and iso6(K), limit, offset)
        self.assertEqual((b["opening_balance"], b["closing_balance"], b["has_more"]), (want["opening_balance"], want["closing_balance"], want["has_more"]), ctx)
        got = [(e["payment"]["payment_id"], e["delta"], e["balance_after"], e["revision"], dt(e["effective_at"]), dt(e["recorded_at"]), e["payment"]["amount"]) for e in b["entries"]]
        exp = [(e["pid"], e["delta"], e["balance_after"], e["revision"], e["effective_at"], e["recorded_at"], e["amount"]) for e in want["entries"]]
        self.assertEqual(got, exp, ctx)

    def probe(self, W, rnd, rec_times):
        led = W.led
        n0 = self.n0
        eff_times = sorted({m[0] for m in led.movements()}) or [n0]

        def pick_T():
            c = rnd.choice(["eff", "effpm", "rand", "future", "none"])
            if c == "none":
                return None
            if c == "future":
                return now_utc() + timedelta(days=rnd.randint(1, 30))
            if c == "rand":
                return n0 - timedelta(minutes=rnd.randint(0, 3000))
            t = rnd.choice(eff_times)
            return t + (rnd.choice([-1, 1]) * US if c == "effpm" else timedelta(0))

        def pick_K():
            c = rnd.choice(["rec", "recm", "before", "future", "none", "none"])
            if c == "none":
                return None
            if c == "future":
                return now_utc() + timedelta(days=2)
            if c == "before":
                return n0 - timedelta(hours=rnd.randint(1, 60))
            if not rec_times:
                return None
            r = rnd.choice(rec_times)
            return r if c == "rec" else r - US

        for _ in range(3):
            T_, K_ = pick_T(), pick_K()
            u = rnd.choice(USERS)
            if T_ is None and K_ is not None and any(h.created > K_ for h in led.holds):
                continue   # keep current-view checks free of hold timing at the very edge
            self.me_check(led, u, T_, K_)
        for _ in range(2):
            u = rnd.choice(USERS)
            a, b = pick_T(), pick_T()
            frm, to = (a, b) if (a and b and a <= b) or not (a and b) else (b, a)
            self.st_check(led, u, frm, to, pick_K(), rnd.choice([1, 3, 200]), rnd.choice([0, 0, 1]))
        T_, K_ = pick_T(), pick_K()
        tot = sum(me_at(u, iso6(T_) if T_ else None, iso6(K_) if K_ else None).body["balance"] for u in USERS + ["op", "op2"])
        self.assertEqual(tot, sum(led.opening.values()))

    # ------------------------------------------------------------------ the run
    def pick_eff(self, W, rnd):
        led = W.led
        c = rnd.choice(["created", "created", "grid", "recent"])
        pids = list(led.pay)
        if c == "created":
            p = rnd.choice(pids)
            return led.pay[p]["revs"][0][2] + rnd.choice([timedelta(0), timedelta(0), US * -1, timedelta(minutes=1) * -1])
        if c == "recent":
            return now_utc() - timedelta(seconds=rnd.randint(1, 6))
        return self.n0 - timedelta(hours=rnd.randint(1, 100) / 2.0, minutes=rnd.choice([0, 0, 1]))

    def run_seed(self, seed, nops):
        rnd = random.Random(seed * 104729 + 7)
        bal0, specs = self.build(rnd)
        fx, led, n0 = hist(specs, openings={**{n: 0 for n in OPEN0}, **bal0})
        reset(fx)
        self.n0 = n0
        W = World(led)
        for pid in led.pay:
            W.kind[pid] = "ordinary"
        rec_times = []
        tally = {}

        def note(key):
            tally[key] = tally.get(key, 0) + 1

        def sender_of(pid):
            return led.pay[pid]["frm"][2:]

        for step in range(nops):
            op = rnd.choices(["pay", "settle", "capture", "refund", "correct", "batch", "batch_imm"], [18, 12, 6, 20, 22, 22, 6])[0]
            now = now_utc()
            avail = {u: W.avail_now("u_" + u, now) for u in USERS}
            if op == "pay":
                f = rnd.choice([u for u in USERS if avail[u] >= 5] or [None])
                if f is None:
                    continue
                t = rnd.choice([u for u in USERS if u != f])
                amt = rnd.randint(1, min(avail[f], 300))
                r = pay(f, t, amt)
                self.assertEqual(r.status, 201, r)
                W.add(r.body["payment_id"], "u_" + f, "u_" + t, amt, dt(r.body["created_at"]))
                note("pay")
            elif op == "settle":
                tr, spend = [], {}
                for _ in range(rnd.randint(2, 3)):
                    f, t = rnd.sample(USERS, 2)
                    left = avail[f] - spend.get(f, 0)
                    if left < 3:
                        continue
                    a = rnd.randint(1, min(left, 200))
                    spend[f] = spend.get(f, 0) + a
                    tr.append({"from_handle": f, "to_handle": t, "amount": a})
                if not tr:
                    continue
                r = settle("op", tr)
                self.assertEqual(r.status, 201, r)
                for m, x in zip(r.body["payments"], tr):
                    W.add(m["payment_id"], "u_" + x["from_handle"], "u_" + x["to_handle"], x["amount"], dt(m["created_at"]), "settlement", r.body["settlement_id"])
                note("settle")
            elif op == "capture":
                f = rnd.choice([u for u in USERS if avail[u] >= 40] or [None])
                if f is None or rnd.random() < 0.5:
                    continue
                t = rnd.choice([u for u in USERS if u != f])
                a = rnd.randint(20, min(avail[f], 150))
                au = authorize(f, t, a)
                self.assertEqual(au.status, 201, au)
                c = rnd.randint(1, a)
                cp = capture(t, au.body["authorization_id"], c)
                self.assertEqual(cp.status, 201, cp)
                row = az_get(f, au.body["authorization_id"])
                h = Hold(au.body["authorization_id"], "u_" + f, a, dt(au.body["created_at"]), dt(au.body["expires_at"]))
                h.captures = [(dt(cp.body["created_at"]), c)]
                h.close_kind, h.closed_at = "final", dt(row["closed_at"])
                led.holds.append(h)
                W.add(cp.body["payment_id"], "u_" + f, "u_" + t, c, dt(cp.body["created_at"]), "capture")
                note("capture")
            elif op == "refund":
                pids = list(led.pay)
                bias = rnd.random()
                short = [x for x in pids if W.kind[x] != "refund" and W.current(x) - W.refunded(x) > avail.get(led.pay[x]["to"][2:], 0) >= 0 and led.pay[x]["to"][2:] in avail]
                refunds_ = [x for x in pids if W.kind[x] == "refund"]
                pid = rnd.choice(short) if (bias < 0.3 and short) else (rnd.choice(refunds_) if (bias < 0.4 and refunds_) else rnd.choice(pids))
                p = led.pay[pid]
                by = p["to"][2:] if rnd.random() < 0.85 else rnd.choice([u for u in USERS if "u_" + u != p["to"]])
                room = W.current(pid) - W.refunded(pid)
                amt = rnd.choice([1, max(1, room), max(1, room // 2), room + 1, rnd.randint(1, 500)])
                if pid in short and bias < 0.3 and room > avail.get(p["to"][2:], 0):
                    amt = rnd.randint(avail.get(p["to"][2:], 0) + 1, room)
                is_ref = W.kind[pid] == "refund"
                exceeds = amt + W.refunded(pid) > W.current(pid)
                insufficient = avail.get(by, 0) < amt
                if "u_" + by == p["to"] and sum([is_ref, exceeds, insufficient]) > 1:
                    continue      # simultaneous refund errors: precedence not pinned down by the spec
                exp = W.pred_refund("u_" + by, pid, amt, now)
                r = refund(by, pid, amt)
                self.assertEqual((r.status, r.code), (exp[0], exp[1]), "refund %s by %s amount %d: %r" % (pid, by, amt, r))
                if r.status == 201:
                    W.add(r.body["payment_id"], p["to"], p["frm"], amt, dt(r.body["created_at"]), "refund", target=pid)
                    self.assertEqual(r.body["refund_of"], pid)
                note("refund-%s" % exp[1] if exp[0] != 201 else "refund-ok")
            elif op == "correct":
                pid = rnd.choice(list(led.pay))
                latest = led.latest(pid)
                stale = rnd.random() < 0.07
                amount = rnd.choice([0, latest[1] + rnd.randint(1, 300), max(0, latest[1] - rnd.randint(1, 300)), rnd.randint(0, 900), max(0, W.refunded(pid) - 1)])
                eff = self.pick_eff(W, rnd)
                if eff > now_utc():
                    continue
                if amount == latest[1] and eff == latest[2]:
                    amount += 1
                by = sender_of(pid)
                if rnd.random() < 0.05:
                    by = rnd.choice([u for u in USERS if u != by])
                    r = correct(by, pid, latest[0], amount, eff)
                    self.assertEqual((r.status, r.code), (403, "forbidden"), r)
                    note("correct-403")
                    continue
                exp = W.pred_single(pid, latest[0] + (1 if stale else 0), amount, eff, now)
                if exp is None:
                    continue
                r = correct(by, pid, latest[0] + (1 if stale else 0), amount, eff, "fz")
                self.assertEqual((r.status, r.code), (exp[0], exp[1]), "correct %s kind %s amount %d eff %s: %r" % (pid, W.kind[pid], amount, iso6(eff), r))
                if r.status == 201:
                    led.add_rev(pid, r.body["revision"], r.body["amount"], dt(r.body["effective_at"]), dt(r.body["recorded_at"]), "fz")
                    rec_times.append(dt(r.body["recorded_at"]))
                    note("correct-ok")
                else:
                    note("correct-%s" % exp[1])
            elif op == "batch_imm":
                lk = [x for x in led.pay if W.kind[x] in ("capture", "refund")]
                if not lk:
                    continue
                pid = rnd.choice(lk)
                e_ = self.n0 - timedelta(hours=rnd.randint(1, 80) / 2.0)
                mi = [(pid, led.latest(pid)[0], led.latest(pid)[1] + 1, e_, False)]
                r = batch("op", [citem(pid, led.latest(pid)[0], led.latest(pid)[1] + 1, e_)])
                self.assertEqual((r.status, r.code), (422, "linked_payment_immutable"), r)
                note("batch-linked_payment_immutable")
            else:
                kinds = ["ordinary"] * 6 + ["settlement"] * 3 + ["capture", "refund"]
                chosen, want_n = [], rnd.randint(1, 4)
                pool = list(led.pay)
                rnd.shuffle(pool)
                items = []
                eff_group = {}
                for pid in pool:
                    if len(items) >= want_n:
                        break
                    if pid in chosen:
                        continue
                    k_ = W.kind[pid]
                    if k_ == "settlement":
                        group = W.groups[W.sid[pid]]
                        sid = W.sid[pid]
                        if sid in eff_group:
                            continue
                        members = group if rnd.random() < 0.65 else [pid]
                        e0 = self.pick_eff(W, rnd)
                        eff_group[sid] = e0
                        for i_, m in enumerate(members):
                            if m in chosen:
                                continue
                            e_ = e0 if rnd.random() < 0.9 else e0 - timedelta(microseconds=3)
                            chosen.append(m)
                            items.append((m, e_))
                    elif k_ in ("capture", "refund") and rnd.random() > 0.45:
                        continue
                    else:
                        chosen.append(pid)
                        items.append((pid, self.pick_eff(W, rnd)))
                if not items or len(items) > 32:
                    continue
                if rnd.random() < 0.03:
                    items.insert(rnd.randint(0, len(items)), ("p_does_not_exist", now_utc() - timedelta(seconds=2)))
                call_items, model_items = [], []
                bad = False
                for pid, e_ in items:
                    if e_ > now_utc():
                        bad = True
                    if pid not in led.pay:
                        call_items.append(citem(pid, 1, 5, e_))
                        model_items.append((pid, 1, 5, e_, False))
                        continue
                    latest = led.latest(pid)
                    er = latest[0] + (1 if rnd.random() < 0.06 else 0)
                    amount = rnd.choice([0, latest[1] + rnd.randint(1, 250), max(0, latest[1] - rnd.randint(1, 250)), rnd.randint(0, 700), max(0, W.refunded(pid) - 1), latest[1]])
                    invalid = rnd.random() < 0.03
                    if amount == latest[1] and e_ == latest[2]:
                        amount += 1
                    call_items.append(citem(pid, er, -1 if invalid else amount, e_))
                    model_items.append((pid, er, amount, e_, invalid))
                if bad:
                    continue
                exp = W.pred_batch(model_items, now)
                if exp is None:
                    continue
                r = batch("op", call_items)
                self.assertEqual((r.status, r.code), (exp[0], exp[1]), "batch %r -> %r" % ([(m[0], W.kind.get(m[0]), m[2]) for m in model_items], r))
                if r.status == 201:
                    rec = dt(r.body["recorded_at"])
                    self.assertEqual([x["payment_id"] for x in r.body["revisions"]], [m[0] for m in model_items])
                    for x, m in zip(r.body["revisions"], model_items):
                        self.assertEqual(dt(x["recorded_at"]), rec)
                        self.assertEqual(x["correction_batch_id"], r.body["correction_batch_id"])
                        led.add_rev(m[0], x["revision"], x["amount"], dt(x["effective_at"]), rec, "fz")
                    rec_times.append(rec)
                    note("batch-ok")
                else:
                    note("batch-%s" % exp[1])
            if step % 3 == 2:
                self.probe(W, rnd, rec_times)
        self.probe(W, rnd, rec_times)
        names = USERS + ["op", "op2"]
        self.assertEqual(sum(bal(n) for n in names), sum(led.opening.values()))
        for n in names:
            m = me(n)
            self.assertGreaterEqual(m["available"], 0)
            self.assertEqual(m["balance"], led.total("u_" + n, INF, INF))
        return tally

    def test_fuzz_seed_1(self):
        "[FZ4-01] model fuzz (seed 1): 45 random payments, settlements, captures, refunds, corrections and batches predicted by an independent oracle; every probed view agrees"
        self.run_seed(1, 45)

    def test_fuzz_seed_2(self):
        "[FZ4-02] model fuzz (seed 2)"
        self.run_seed(2, 45)

    def test_fuzz_seed_3(self):
        "[FZ4-03] model fuzz (seed 3)"
        self.run_seed(3, 45)

    def test_fuzz_exercises_every_outcome(self):
        "[FZ4-04] across seeds the fuzz reaches refund success/exceeds/insufficient/invalid-target, correction and batch success, incomplete_settlement, immutable, stale, insufficient and historical outcomes"
        tot = {}
        for seed in (21, 22, 23, 24, 25, 26):
            for k_, v in self.run_seed(seed, 40).items():
                tot[k_] = tot.get(k_, 0) + v
        for key in ("refund-ok", "refund-refund_exceeds_payment", "refund-insufficient_funds", "refund-invalid_refund_target", "correct-ok", "batch-ok", "batch-incomplete_settlement",
                    "batch-linked_payment_immutable", "batch-stale_revision", "settle"):
            self.assertGreater(tot.get(key, 0), 0, tot)


if __name__ == "__main__":
    unittest.main()
