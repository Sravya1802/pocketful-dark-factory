#!/usr/bin/env python3
"""Per-seat tokens, time and API list-price equivalent, from Claude Code session logs.

    python3 factory/scripts/seat-costs.py <result-repo> [--mandates DIR] [--since ISO] [--md]

Each seat runs Claude Code with the result repo as its working directory, so its session
log lands in ~/.claude/projects/<repo path with / and spaces as ->/*.jsonl. A session is
assigned to a seat by finding that seat's mandate (its first sentence) in the log.

Prices: Claude API list prices per million tokens (claude-api skill, cached 2026-09-25).
Cache writes: 1.25x input (5 min), 2x input (1 h). Seats on a Claude subscription are not
billed per token; this is the API-equivalent cost, which is what FACTORY.md should quote.
"""
import argparse, collections, datetime as dt, json, pathlib, re, sys

PRICES = {  # input, output, cache read  ($ per 1M tokens)
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
    "claude-fable-5-1": (10.00, 50.00, 0.25),
}


def project_dir(repo: pathlib.Path) -> pathlib.Path:
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve()))
    return pathlib.Path.home() / ".claude" / "projects" / slug


def signatures(mandates: pathlib.Path) -> dict:
    sig = {}
    for f in sorted(mandates.glob("*.md")):
        body = [l for l in f.read_text().splitlines() if l.strip() and not re.match(r"^(#|Harness:|Model:)", l)]
        first = " ".join(body[:2]).split(". ")[0][:60] if body else ""
        if first:
            sig[f.stem] = first
    return sig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--mandates", default=None)
    ap.add_argument("--since", default=None, help="ignore messages before this ISO time")
    ap.add_argument("--md", action="store_true", help="print a markdown table")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo)
    sig = signatures(pathlib.Path(a.mandates) if a.mandates else repo / "mandates")
    pdir = project_dir(repo)
    if not pdir.is_dir():
        sys.exit(f"no Claude Code logs at {pdir}")
    seats = collections.defaultdict(lambda: {"turns": 0, "in": 0, "out": 0, "cr": 0, "cw5": 0, "cw1h": 0,
                                             "usd": 0.0, "models": set(), "first": None, "last": None, "sessions": 0})
    unknown = []
    for f in sorted(pdir.glob("*.jsonl")):
        text = f.read_text(errors="replace")
        seat = next((s for s, sg in sig.items() if sg and sg in text), None)
        if not seat:
            unknown.append(f.name); continue
        s = seats[seat]; s["sessions"] += 1; seen = set()
        for line in text.splitlines():
            try: d = json.loads(line)
            except ValueError: continue
            m = d.get("message")
            if not isinstance(m, dict) or not m.get("usage") or m.get("model") in (None, "<synthetic>"):
                continue
            if a.since and d.get("timestamp", "") < a.since:
                continue
            mid = m.get("id") or d.get("requestId") or d.get("uuid")
            if mid in seen:
                continue  # streamed messages are logged once per content block
            seen.add(mid)
            u, model = m["usage"], m["model"]
            cc = u.get("cache_creation") or {}
            cw1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
            cw5 = cc.get("ephemeral_5m_input_tokens", u.get("cache_creation_input_tokens", 0) - cw1h) or 0
            pin, pout, pcr = PRICES.get(model, PRICES["claude-opus-5-5"])
            s["turns"] += 1; s["models"].add(model)
            s["in"] += u.get("input_tokens", 0); s["out"] += u.get("output_tokens", 0)
            s["cr"] += u.get("cache_read_input_tokens", 0); s["cw5"] += cw5; s["cw1h"] += cw1h
            s["usd"] += (u.get("input_tokens", 0) * pin + u.get("output_tokens", 0) * pout
                         + u.get("cache_read_input_tokens", 0) * pcr + cw5 * pin * 1.25 + cw1h * pin * 2) / 1e6
            t = d.get("timestamp")
            if t:
                s["first"] = min(filter(None, [s["first"], t])); s["last"] = max(filter(None, [s["last"], t]))
    total = sum(s["usd"] for s in seats.values()) or 1
    rows = []
    for name in sorted(seats, key=lambda n: -seats[n]["usd"]):
        s = seats[name]
        rows.append((name, ",".join(sorted(s["models"])), s["turns"], s["in"] + s["cw5"] + s["cw1h"] + s["cr"], s["cr"], s["out"],
                     s["usd"], 100 * s["usd"] / total, (s["first"] or "")[11:16], (s["last"] or "")[11:16]))
    hdr = ("Seat", "Model", "Turns", "Input tokens (all)", "of which cached reads", "Output tokens", "API-equiv $", "Share", "First (UTC)", "Last (UTC)")
    if a.md:
        print("| " + " | ".join(hdr) + " |"); print("|" + "---|" * len(hdr))
        for r in rows:
            print(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:,} | {r[4]:,} | {r[5]:,} | ${r[6]:.2f} | {r[7]:.0f}% | {r[8]} | {r[9]} |")
        print(f"| **total** | | {sum(r[2] for r in rows)} | {sum(r[3] for r in rows):,} | {sum(r[4] for r in rows):,} | {sum(r[5] for r in rows):,} | **${sum(r[6] for r in rows):.2f}** | | | |")
    else:
        for r in rows:
            print(f"{r[0]:<12} {r[1]:<20} turns {r[2]:>4}  in {r[3]:>12,}  cached {r[4]:>12,}  out {r[5]:>9,}  ${r[6]:>7.2f}  {r[7]:>3.0f}%")
        print(f"{'TOTAL':<12} {'':<20} turns {sum(r[2] for r in rows):>4}  {'':>28}  {'':>23}  ${sum(r[6] for r in rows):>7.2f}")
    if unknown:
        print(f"\nunassigned session logs (no mandate signature found): {', '.join(unknown)}", file=sys.stderr)


if __name__ == "__main__":
    main()
