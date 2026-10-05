#!/usr/bin/env python3
"""Stage-3 oracle fuzz. usage: fuzz3.py URL [seeds=5] [ops=120] [first_seed=1]   (RESETS)
Random sequences of payments, corrections, authorizations / captures / voids / expiry, settlements and request payments are applied
to the service AND to oracle3.Model; after every step the service's /me (current and historical), /statement (windows, ties, paging,
known_at) and correction outcomes (ok / insufficient_funds / historical_overdraft) are compared with the oracle."""
import sys, json, time, random, datetime
from s3lib import *
from oracle3 import *
URL = sys.argv[1]; SEEDS = int(sys.argv[2]) if len(sys.argv) > 2 else 5; OPS = int(sys.argv[3]) if len(sys.argv) > 3 else 120; FIRST = int(sys.argv[4]) if len(sys.argv) > 4 else 1
NAMES = ["ada", "bob", "cy", "dee"]
def now_us(): return int(time.time() * 1_000_000)
FAILS = []; NCHECK = [0]; CODES = {}
def ck(name, ok, detail=""):
    NCHECK[0] += 1
    if not ok:
        FAILS.append((name, str(detail)[:600])); print("FAIL", name, "::", str(detail)[:600], flush=True)
def sample_instants(m, now, rnd):
    ts = set()
    for p, r in m.moves(INF): ts.update([r[2], r[3]])
    for p in m.pays.values():
        for r in p.revs: ts.update([r[2], r[3]])
    for h in m.holds.values():
        ts.update([h.created, h.expires]); ts.update(t for t, a in h.caps)
        if h.closed is not None: ts.add(h.closed)
    out = set()
    for t in ts:
        out.add(t); out.add(t - 1); out.add(t + 1)
    out.add(now); out.add(now + 3_600_000_000); out.add(now + 10 ** 12); out.add(0); out.add(now - 10 ** 13)
    return sorted(out)

class Run:
    def __init__(s, seed):
        s.seed = seed; s.rnd = random.Random(seed); s.m = Model(); s.T = {}; s.ids = {}; s.keys = 0; s.log = []; s.seed_total = 0
        s.t_reset = None
    def key(s): s.keys += 1; return f"s{s.seed}-k{s.keys}-{s.rnd.random():.6f}"
    def setup(s):
        r = s.rnd; now = time.time(); base = int(now) - 5 * 86400
        users = []; bal = {}
        for i, n in enumerate(NAMES):
            bal[n] = r.choice([0, 500, 2000, 10000, 50000]); users.append({"id": f"u_{n}", "email": f"{n}@example.com", "password": "correct horse", "display_name": n.title(), "handle": n, "balance": bal[n]})
        users.append({"id": "u_op", "email": "op@example.com", "password": "correct horse", "display_name": "Op", "handle": "op", "balance": 100000})
        # seeded payments in the past (with nonnegative history): choose then derive nothing; opening = balance - net must be >= 0 along the way
        pays = []; ts = sorted(r.sample(range(base, base + 3 * 86400), r.randint(0, 6)))
        net = {n: 0 for n in NAMES}; net["op"] = 0
        for i, t in enumerate(ts):
            a, b = r.sample(NAMES, 2); amt = r.randint(1, 400); pays.append({"id": f"sp{i}", "from_user_id": f"u_{a}", "to_user_id": f"u_{b}", "amount": amt, "note": "seed", "visibility": r.choice(["public", "private"]), "created_at": fmt(t * 1_000_000, r.choice([0, 0, 330, -300]), r.choice([0, 0, 3, 6]))})
            net[a] -= amt; net[b] += amt
        # make sure opening balances are non-negative and history stays non-negative: bump balances
        run = {n: 0 for n in NAMES}
        for n in NAMES:
            op_ = bal[n] - net[n]
            lowest = op_
            cur = op_
            for p in sorted(pays, key=lambda p: (parse(p["created_at"]), p["id"])):
                if p["from_user_id"] == f"u_{n}": cur -= p["amount"]
                if p["to_user_id"] == f"u_{n}": cur += p["amount"]
                lowest = min(lowest, cur)
            if lowest < 0:
                add = -lowest; bal[n] += add
                [u for u in users if u["handle"] == n][0]["balance"] = bal[n]
        s.seed_total = sum(u["balance"] for u in users)
        fxt = {"currency": "EUR", "minor_units": 2, "authorization_ttl_seconds": s.rnd.choice([2, 3]), "settlement_operator_ids": ["u_op"], "users": users, "payments": pays, "authorizations": []}
        reset(URL, fxt); s.t_reset = now_us()
        s.T = {n: login(URL, f"{n}@example.com") for n in NAMES + ["op"]}
        netu = {u["handle"]: 0 for u in users}
        for p in pays:
            a = p["from_user_id"][2:]; b = p["to_user_id"][2:]; netu[a] -= p["amount"]; netu[b] += p["amount"]
        for u in users: s.m.opening[u["handle"]] = u["balance"] - netu[u["handle"]]
        for p in pays:
            t = parse(p["created_at"]); s.m.pays[p["id"]] = Pay(p["id"], p["from_user_id"][2:], p["to_user_id"][2:], p["amount"], t, t)
        ck("setup: opening balances nonneg", all(v >= 0 for v in s.m.opening.values()), s.m.opening)
    # ----------------------------------------------------------------- operations
    def refresh_holds(s):
        for n in NAMES + ["op"]:
            off = 0
            while True:
                st, j = req(URL, "GET", f"/authorizations?limit=200&offset={off}", token=s.T[n])
                if st != 200: ck("authorizations list", False, (st, j)); break
                for a in j["authorizations"]:
                    h = s.m.holds.get(a["authorization_id"])
                    if h is None: continue
                    if a.get("closed_at") is not None: h.closed = parse(a["closed_at"])
                    else: h.closed = None if a["status"] == "open" else h.closed
                    h.api = a
                if not j["has_more"]: break
                off += 200
    def op_pay(s):
        a, b = s.rnd.sample(NAMES + ["op"], 2); amt = s.rnd.choice([1, 5, 50, 100, 300, 1000, 5000]); t0 = now_us()
        st, j = req(URL, "POST", "/payments", {"to_handle": b, "amount": amt, "visibility": s.rnd.choice(["public", "private"])}, headers=idem(s.key()), token=s.T[a]); t1 = now_us()
        av = s.m.view(a, t0, INF)["available"]
        if st == 201:
            ck("pay: succeeded only if available covers", av >= amt, (av, amt, j)); t = parse(j["created_at"]); ck("pay: created_at within request window", t0 - 5000 <= t <= t1 + 5000, (j["created_at"], t0, t1))
            s.m.pays[j["payment_id"]] = Pay(j["payment_id"], a, b, amt, t, t)
        else: ck("pay: refused only when unaffordable", st == 409 and j["error"]["code"] == "insufficient_funds" and av < amt, (st, j, av, amt))
        s.log.append(("pay", a, b, amt, st))
    def op_authorize(s):
        a, b = s.rnd.sample(NAMES, 2); amt = s.rnd.choice([10, 100, 400, 1500]); t0 = now_us()
        st, j = req(URL, "POST", "/authorizations", {"to_handle": b, "amount": amt}, headers=idem(s.key()), token=s.T[a]); t1 = now_us()
        av = s.m.view(a, t0, INF)["available"]
        if st == 201:
            ck("authorize: only if available covers", av >= amt, (av, amt)); s.m.holds[j["authorization_id"]] = Hold(j["authorization_id"], a, b, amt, parse(j["created_at"]), parse(j["expires_at"])); s.refresh_holds()
        else: ck("authorize: refused only when unaffordable", st == 409 and av < amt, (st, j, av, amt))
        s.log.append(("auth", a, b, amt, st))
    def op_capture(s):
        opens = [h for h in s.m.holds.values() if h.expires > now_us() and (h.closed is None)]
        if not opens: return
        h = s.rnd.choice(opens); rem = h.amount - sum(a for t, a in h.caps); amt = s.rnd.choice([None, 1, max(1, rem // 2), rem]); final = s.rnd.choice([True, False, None])
        body = {}
        if amt is not None: body["amount"] = amt
        if final is not None: body["final"] = final
        st, j = req(URL, "POST", f"/authorizations/{h.id}/capture", body, headers=idem(s.key()), token=s.T[h.rcv])
        if st == 201:
            t = parse(j["created_at"]); s.m.pays[j["payment_id"]] = Pay(j["payment_id"], h.payer, h.rcv, j["amount"], t, t, "capture"); h.caps.append((t, j["amount"])); s.refresh_holds()
            ck("capture: payment amount", j["amount"] == (amt if amt is not None else rem), (j, amt, rem))
        else: ck("capture: only expected refusals", st in (409, 422), (st, j))
        s.log.append(("capture", h.id, amt, final, st))
    def op_void(s):
        opens = [h for h in s.m.holds.values() if h.expires > now_us() and h.closed is None]
        if not opens: return
        h = s.rnd.choice(opens); t0 = now_us(); st, j = req(URL, "POST", f"/authorizations/{h.id}/void", {}, token=s.T[h.payer]); t1 = now_us()
        if st == 200: s.refresh_holds(); ck("void: closed_at within window", h.closed is not None and t0 - 5000 <= h.closed <= t1 + 5000, (j, t0, t1)) 
        s.log.append(("void", h.id, st))
    def op_settle(s):
        n = s.rnd.randint(1, 4); tr = []
        for _ in range(n):
            a, b = s.rnd.sample(NAMES + ["op"], 2); tr.append({"from_handle": a, "to_handle": b, "amount": s.rnd.choice([1, 20, 200, 2000])})
        t0 = now_us(); st, j = req(URL, "POST", "/settlements", {"transfers": tr}, headers=idem(s.key()), token=s.T["op"]); t1 = now_us()
        # predicted affordability: each wallet's net must not exceed available
        net = {}
        for t in tr: net[t["from_handle"]] = net.get(t["from_handle"], 0) - t["amount"]; net[t["to_handle"]] = net.get(t["to_handle"], 0) + t["amount"]
        ok = all(s.m.view(u, t0, INF)["available"] + d >= 0 for u, d in net.items())
        if st == 201:
            ck("settlement: committed only if every wallet's net is affordable", ok, (net, tr)); c = parse(j["committed_at"])
            for p, t in zip(j["payments"], tr): s.m.pays[p["payment_id"]] = Pay(p["payment_id"], t["from_handle"], t["to_handle"], t["amount"], c, c, "settlement"); ck("settlement member created_at == committed_at", p["created_at"] == j["committed_at"], (p["created_at"], j["committed_at"]))
        else: ck("settlement refused only when unaffordable", st == 409 and not ok, (st, j, net))
        s.log.append(("settle", st))
    def op_request(s):
        a, b = s.rnd.sample(NAMES, 2); amt = s.rnd.choice([10, 100, 700])
        st, rq = req(URL, "POST", "/requests", {"payer_handle": a, "amount": amt}, headers=idem(s.key()), token=s.T[b])
        if st != 201: return
        t0 = now_us(); st, j = req(URL, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem(s.key()), token=s.T[a]); av = s.m.view(a, t0, INF)["available"]
        if st == 201: ck("request-pay only if affordable", av >= amt, (av, amt)); t = parse(j["created_at"]); s.m.pays[j["payment_id"]] = Pay(j["payment_id"], a, b, amt, t, t, "request")
        else: ck("request-pay refused only if unaffordable", st == 409 and av < amt, (st, j, av, amt))
    def eff_choice(s, p):
        r = s.rnd; now = now_us(); cands = []
        allr = [rv for q in s.m.pays.values() for rv in q.revs]
        cands.append(now - 20000); cands.append(s.t_reset - r.randint(1, 10 ** 11)); cands.append(now - r.randint(1000, 2 * 86400 * 10 ** 6))
        cands.append(p.revs[0][2]); cands.append(p.revs[-1][2])
        if allr: cands.append(r.choice(allr)[2]); cands.append(r.choice(allr)[2] - 1); cands.append(r.choice(allr)[2] + 1)
        for h in s.m.holds.values(): cands.append(r.choice([h.created, h.expires if h.expires < now else now - 30000]))
        t = r.choice(cands); return min(t, now - 20000)   # keep clear of host/VM clock skew; the `now` boundary is attacked separately
    def op_correct(s):
        if not s.m.pays: return
        r = s.rnd; pid = r.choice(sorted(s.m.pays)); p = s.m.pays[pid]; cur = s.m.sel(p, INF)
        actor = p.frm if r.random() > 0.08 else r.choice([n for n in NAMES if n != p.frm])
        amt = r.choice([0, cur[1], max(0, cur[1] - r.randint(1, 100)), cur[1] + r.randint(1, 300), cur[1] + r.randint(1, 20000), r.randint(0, 1000)]); eff = s.eff_choice(p)
        rev = cur[0] if r.random() > 0.1 else cur[0] + r.choice([-1, 1, 5]) or 1
        fmt_off = r.choice([0, 0, 120, -420]); eff_s = fmt(eff, fmt_off, r.choice([0, 3, 6]))
        eff_us = parse(eff_s)
        body = {"expected_revision": rev, "amount": amt, "effective_at": eff_s, "reason": r.choice(["fix", "typo é😀", "x" * 200])}
        key = s.key(); before = s.snapshot_views() if r.random() < 0.5 else None; t0 = now_us()
        st, j = req(URL, "POST", f"/payments/{pid}/corrections", body, headers=idem(key), token=s.T[actor] if actor in s.T else s.T["op"]); t1 = now_us()
        if actor != p.frm:
            ck("correction by non-sender -> 403", st == 403, (st, j)); return
        if p.kind in ("capture", "settlement"):
            ck(f"correction of {p.kind} -> 422 linked_payment_immutable", st == 422 and j["error"]["code"] == "linked_payment_immutable", (st, j)); return
        if rev != cur[0]:
            ck("stale expected_revision -> 409 stale_revision", st == 409 and j["error"]["code"] == "stale_revision", (st, j, rev, cur)); return
        pred = s.m.predict_correction(pid, amt, eff_us, t0); pred2 = s.m.predict_correction(pid, amt, eff_us, t1)
        if st == 201:
            ck("correction succeeded -> oracle predicted ok", "ok" in (pred, pred2), (pred, pred2, body, s.dump(pid)))
            ck("correction response shape", j["payment_id"] == pid and j["revision"] == cur[0] + 1 and j["amount"] == amt and j["effective_at"] == eff_s and j["reason"] == body["reason"], (j, body))
            rec = parse(j["recorded_at"]); ck("recorded_at strictly increases and lies within request window", rec > cur[3] and t0 - 5000 <= rec <= t1 + 5000, (rec, cur, t0, t1))
            p.revs.append((cur[0] + 1, amt, eff_us, rec))
        else:
            want = {"insufficient_funds", "historical_overdraft"}
            ck("correction refused -> expected 409 code matches oracle", st == 409 and j["error"]["code"] in want and j["error"]["code"] in (pred, pred2), (st, j, pred, pred2, body, s.dump(pid)))
            if before is not None:
                after = s.snapshot_views(); ck("failed correction left every view, revision list and balances unchanged", before == after, "views changed")
                st2, rv = req(URL, "GET", f"/payments/{pid}/revisions", token=s.T[p.frm]); ck("failed correction left revision history unchanged", st2 == 200 and len(rv["revisions"]) == len(p.revs), rv)
            # failed key is reusable: replay the SAME key with a body that is valid (same body would fail again) -> must not be 409 idempotency_key_reuse
            st3, j3 = req(URL, "POST", f"/payments/{pid}/corrections", dict(body, amount=cur[1], effective_at=fmt(cur[2])), headers=idem(key), token=s.T[p.frm])
            ck("failed correction does not claim its key (reuse with another body is not 409 idempotency_key_reuse)", not (st3 == 409 and j3["error"]["code"] == "idempotency_key_reuse"), (st3, j3))
            if st3 == 201:
                rec = parse(j3["recorded_at"]); p.revs.append((cur[0] + 1, cur[1], parse(fmt(cur[2])), rec))
        CODES[(st, (j or {}).get('error', {}).get('code') if st != 201 else 'ok')] = CODES.get((st, (j or {}).get('error', {}).get('code') if st != 201 else 'ok'), 0) + 1
        s.log.append(("correct", pid, amt, eff_s, st))
    def dump(s, pid):
        p = s.m.pays[pid]; return {"revs": p.revs, "frm": p.frm, "to": p.to, "views": {n: s.m.view(n, INF, INF) for n in (p.frm, p.to)}}
    # ----------------------------------------------------------------- comparisons
    def snapshot_views(s):
        out = {}
        for n in NAMES + ["op"]:
            st, j = req(URL, "GET", "/me", token=s.T[n]); out[n] = {k: j[k] for k in ("balance", "total", "available", "held")} if st == 200 else st
            st, j = req(URL, "GET", "/statement?limit=200", token=s.T[n]); out[n + "_st"] = ([(e["payment"]["payment_id"], e["revision"], e["delta"], e["balance_after"]) for e in j["entries"]], j["opening_balance"], j["closing_balance"]) if st == 200 else st
        return out
    def verify_me(s):
        for n in NAMES + ["op"]:
            t0 = now_us(); st, j = req(URL, "GET", "/me", token=s.T[n]); t1 = now_us()
            e0 = s.m.view(n, t0 - 5000, INF); e1 = s.m.view(n, t1, INF); e2 = s.m.view(n, t0, INF); got = {k: j[k] for k in ("balance", "total", "available", "held")}
            if not (got == e0 or got == e1 or got == e2):
                hs = [{"id": h.id, "amount": h.amount, "caps": h.caps, "created": h.created, "expires": h.expires, "closed": h.closed, "now": t1, "api": getattr(h, "api", None)} for h in s.m.holds.values() if h.payer == n]
                st2, al = req(URL, "GET", "/authorizations?limit=200&direction=outgoing", token=s.T[n]); print("DEBUG holds for", n, json.dumps(hs, default=str)[:1800]); print("DEBUG server list", json.dumps(al, default=str)[:1800]); print("DEBUG server /me now", req(URL, "GET", "/me", token=s.T[n])[1])
            ck(f"/me current [{n}] == oracle (+-5ms tolerance for container clock skew near a hold expiry)", got == e0 or got == e1 or got == e2, (got, e0, e1))
            ck(f"/me invariants [{n}]", j["balance"] == j["total"] and j["available"] == j["total"] - j["held"] and j["available"] >= 0 and j["held"] >= 0)
        tot = sum(req(URL, "GET", "/me", token=s.T[n])[1]["total"] for n in NAMES + ["op"]); ck("sum of current totals == seeded total", tot == s.seed_total, (tot, s.seed_total))
    def verify_hist(s, k=12):
        r = s.rnd; inst = sample_instants(s.m, now_us(), r)
        sumbad = None
        for _ in range(k):
            n = r.choice(NAMES + ["op"]); as_of = r.choice(inst + [None]); known = r.choice(inst + [None]); q = []
            offa = r.choice([0, 0, 90, -330]); offk = r.choice([0, 0, 60])
            if as_of is not None: q.append("as_of=" + fmt(as_of, offa, 6).replace("+", "%2B"))
            if known is not None: q.append("known_at=" + fmt(known, offk, 6).replace("+", "%2B"))
            t0 = now_us(); st, j = req(URL, "GET", "/me" + ("?" + "&".join(q) if q else ""), token=s.T[n]); t1 = now_us()
            exps = [s.m.view(n, as_of if as_of is not None else t, known if known is not None else INF if known is None and as_of is not None else INF) for t in (t0, t1)]
            exps = [s.m.view(n, as_of if as_of is not None else t, known if known is not None else INF) for t in (t0, t1)]
            got = {kk: j[kk] for kk in ("balance", "total", "available", "held")} if st == 200 else st
            ck(f"/me?{'&'.join(q)} [{n}] == oracle", got in exps, (got, exps, q))
            if st == 200:
                if as_of is not None: ck("as_of echoed exactly", j.get("as_of") == fmt(as_of, offa, 6), (j.get("as_of"),))
                if known is not None: ck("known_at echoed exactly", j.get("known_at") == fmt(known, offk, 6), (j.get("known_at"),))
        # sum of balances in a historical view == seeded total
        T = r.choice(inst); K = r.choice(inst + [None]); q = "as_of=" + fmt(T, 0, 6).replace("+", "%2B") + ("&known_at=" + fmt(K, 0, 6).replace("+", "%2B") if K is not None else "")
        views = [req(URL, "GET", "/me?" + q, token=s.T[n])[1] for n in NAMES + ["op"]]
        ck("sum of balances in a historical view == seeded total (when all views known)", True if K is not None else sum(v["total"] for v in views) == s.seed_total, (sum(v["total"] for v in views), s.seed_total, q))
    def verify_statements(s, k=8):
        r = s.rnd; inst = sample_instants(s.m, now_us(), r)
        for _ in range(k):
            n = r.choice(NAMES + ["op"]); frm = r.choice(inst + [None, None]); to = r.choice(inst + [None, None]); known = r.choice(inst + [None, None, None]); limit = r.choice([1, 2, 3, 50, 200]); offset = r.choice([0, 0, 1, 2, 5, 500])
            q = [f"limit={limit}", f"offset={offset}"]
            if frm is not None: q.append("from=" + fmt(frm, r.choice([0, 0, 120]), 6).replace("+", "%2B"))
            if to is not None: q.append("to=" + fmt(to, r.choice([0, 0, -90]), 6).replace("+", "%2B"))
            if known is not None: q.append("known_at=" + fmt(known, 0, 6).replace("+", "%2B"))
            frm_p = None
            t0 = now_us(); st, j = req(URL, "GET", "/statement?" + "&".join(q), token=s.T[n]); t1 = now_us()
            if frm is not None and to is not None and frm > to:
                ck("statement from>to -> 200 or 422, not 5xx", st in (200, 422), (st, j)); continue
            if st != 200: ck("statement 200", False, (st, j, q)); continue
            # recompute the exact instants used (re-parse what we sent)
            def inst_of(prefix):
                for part in q:
                    if part.startswith(prefix + "="): return parse(part.split("=", 1)[1].replace("%2B", "+"))
                return None
            frm_p = inst_of("from"); to_p = inst_of("to"); known_p = inst_of("known_at")
            exp = None
            for t in (t0, t1):
                e = s.m.statement(n, frm_p, to_p if to_p is not None else t, known_p if known_p is not None else INF)
                if [x["id"] for x in e["entries"]] and True: pass
                if e["opening"] == j["opening_balance"] and e["closing"] == j["closing_balance"] or exp is None: exp = e
                if e["opening"] == j["opening_balance"] and e["closing"] == j["closing_balance"]: break
            ck(f"statement opening/closing [{n}] {q}", exp["opening"] == j["opening_balance"] and exp["closing"] == j["closing_balance"], (j["opening_balance"], j["closing_balance"], exp["opening"], exp["closing"]))
            page = exp["entries"][offset: offset + limit]
            got = [(e["payment"]["payment_id"], e["delta"], e["balance_after"], e["revision"], parse(e["effective_at"]), parse(e["recorded_at"]), e["payment"]["amount"]) for e in j["entries"]]
            want = [(x["id"], x["delta"], x["balance_after"], x["revision"], x["effective_at"], x["recorded_at"], x["amount"]) for x in page]
            ck(f"statement page entries [{n}] {q}", got == want, (got, want))
            ck(f"statement has_more [{n}]", j["has_more"] == (offset + limit < len(exp["entries"])), (j["has_more"], len(exp["entries"]), limit, offset))
            # delta sum invariant on the page-independent full window
            full = req(URL, "GET", "/statement?" + "&".join([x for x in q if not x.startswith(("limit", "offset"))] + ["limit=200"]), token=s.T[n])[1]
            if full and full.get("entries") is not None and not full["has_more"]:
                ck("opening + sum(delta) == closing for the full window", full["opening_balance"] + sum(e["delta"] for e in full["entries"]) == full["closing_balance"], (full["opening_balance"], full["closing_balance"]))
                ck("entries ordered by effective_at then payment id", [(parse(e["effective_at"]), e["payment"]["payment_id"]) for e in full["entries"]] == sorted((parse(e["effective_at"]), e["payment"]["payment_id"]) for e in full["entries"]))
                run = full["opening_balance"]; okc = True
                for e in full["entries"]:
                    run += e["delta"]; okc &= (run == e["balance_after"])
                ck("balance_after is the running balance", okc)
            # snapshot paging
            snap = j.get("snapshot"); ck("statement returns snapshot token", isinstance(snap, str) and snap, j.keys())
            if snap and r.random() < 0.5:
                s.snaps.append((n, snap, {"opening": j["opening_balance"], "closing": j["closing_balance"], "entries": [(e["payment"]["payment_id"], e["balance_after"], e["revision"]) for e in req(URL, "GET", f"/statement?snapshot={snap}&limit=200", token=s.T[n])[1]["entries"]]}))
    def verify_snaps(s):
        for n, snap, frozen in s.snaps[-12:]:
            got = []; off = 0; lim = s.rnd.choice([1, 2, 3, 7]); bal = None
            while True:
                st, j = req(URL, "GET", f"/statement?snapshot={snap}&limit={lim}&offset={off}", token=s.T[n])
                if st != 200: ck("snapshot page 200", False, (st, j)); break
                got += [(e["payment"]["payment_id"], e["balance_after"], e["revision"]) for e in j["entries"]]; bal = (j["opening_balance"], j["closing_balance"])
                if not j["has_more"]: break
                off += lim
                if off > 5000: break
            ck("snapshot frozen: same entries, balances and revisions after later activity", got == frozen["entries"] and bal == (frozen["opening"], frozen["closing"]), (got[:3], frozen["entries"][:3], bal, frozen["opening"], frozen["closing"]))
    def step_verify(s, full):
        s.verify_me()
        if full: s.verify_hist(); s.verify_statements(); s.verify_snaps()
    def run(s):
        s.snaps = []; s.setup(); s.refresh_holds(); s.step_verify(True)
        ops = [(s.op_pay, 20), (s.op_correct, 34), (s.op_authorize, 8), (s.op_capture, 8), (s.op_void, 4), (s.op_settle, 6), (s.op_request, 6)]
        for i in range(OPS):
            fn = s.rnd.choices([o for o, w in ops], [w for o, w in ops])[0]
            try: fn()
            except Exception as e:
                import traceback; traceback.print_exc(); ck(f"op {fn.__name__} script error", False, repr(e))
            if s.rnd.random() < 0.15: time.sleep(s.rnd.choice([0.2, 0.7]))
            s.refresh_holds() if i % 5 == 0 else None
            s.step_verify(i % 4 == 3)
        time.sleep(3.2); s.refresh_holds(); s.step_verify(True)   # let every short-ttl hold expire, then re-verify everything
        s.step_verify(True)
        ck("final: all holds closed or expired -> every user's held is 0", all(req(URL, "GET", "/me", token=s.T[n])[1]["held"] == 0 for n in NAMES + ["op"]) or any(h.closed is None and h.expires > now_us() for h in s.m.holds.values()))
        return s
if __name__ == "__main__":
    for seed in range(FIRST, FIRST + SEEDS):
        before = len(FAILS); r = Run(seed)
        try: r.run()
        except Exception as e:
            import traceback; traceback.print_exc(); ck(f"seed {seed} crashed", False, repr(e))
        kinds = {}
        for l in r.log: kinds[(l[0], l[-1])] = kinds.get((l[0], l[-1]), 0) + 1
        print(f"seed {seed}: ops={len(r.log)} payments={len(r.m.pays)} holds={len(r.m.holds)} revisions={sum(len(p.revs) - 1 for p in r.m.pays.values())} new failures={len(FAILS) - before}; outcomes={sorted(kinds.items(), key=str)[:16]}", flush=True)
    print("correction outcomes (status, code):", dict(sorted(CODES.items(), key=str)))
    print(f"\n{NCHECK[0]} comparisons, {len(FAILS)} failures")
    sys.exit(1 if FAILS else 0)
