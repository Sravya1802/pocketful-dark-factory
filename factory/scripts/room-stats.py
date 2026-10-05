#!/usr/bin/env python3
"""Work distribution and cost from a BAND room download plus the result repo's git history.

    python3 factory/scripts/room-stats.py <result-repo>            # reads <repo>/room.json
    python3 factory/scripts/room-stats.py <result-repo> --json     # machine-readable

The room.json schema is only partly documented: messages[] with senderId, senderName,
senderType, messageType, content. Timestamps and usage are found defensively by key name,
so check the "fields seen" line the first time you run it on a real download.
"""
import collections, datetime as dt, json, pathlib, re, subprocess, sys

TIME_KEYS = ("createdAt", "created_at", "insertedAt", "inserted_at", "timestamp", "sentAt", "time")
TOKEN_KEY = re.compile(r"(input|output|prompt|completion|cache\w*)_?tokens?$|^tokens?$", re.I)
COST_KEY = re.compile(r"(cost|usd|price|spend)", re.I)


def when(m):
    for k in TIME_KEYS:
        v = m.get(k)
        if isinstance(v, str):
            try:
                return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                pass
        if isinstance(v, (int, float)) and v > 1e9:
            return dt.datetime.fromtimestamp(v / 1000 if v > 1e12 else v, dt.timezone.utc)
    return None


def walk_numbers(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_numbers(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_numbers(v, path)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield path, obj
    elif isinstance(obj, str) and obj[:1] in "{[":
        try:
            yield from walk_numbers(json.loads(obj), path)
        except ValueError:
            pass


def main():
    repo = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    room = json.loads((repo / "room.json").read_text())
    msgs = room.get("messages", [])
    fields = collections.Counter(k for m in msgs for k in m)
    seats = collections.defaultdict(lambda: {"text": 0, "tool": 0, "other": 0, "chars": 0,
                                             "first": None, "last": None, "tokens": collections.Counter(), "cost": 0.0})
    humans = collections.Counter()
    for m in msgs:
        kind = str(m.get("senderType", "")).lower()
        name = m.get("senderName") or m.get("senderId") or "?"
        if kind != "agent":
            if m.get("messageType") == "text":
                humans[name] += 1
            continue
        s = seats[name]
        mt = m.get("messageType")
        s["text" if mt == "text" else "tool" if mt and "tool" in str(mt) else "other"] += 1
        s["chars"] += len(str(m.get("content") or ""))
        t = when(m)
        if t:
            s["first"] = min(filter(None, [s["first"], t]))
            s["last"] = max(filter(None, [s["last"], t]))
        for k, v in walk_numbers({k: v for k, v in m.items() if k != "content"}):
            if TOKEN_KEY.search(k):
                s["tokens"][k] += v
            elif COST_KEY.search(k):
                s["cost"] += v
    authors = collections.Counter()
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", "--format=%an"], capture_output=True, text=True, check=True).stdout
        authors.update(l for l in out.splitlines() if l)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    if "--json" in sys.argv:
        print(json.dumps({"seats": {k: {**v, "first": str(v["first"]), "last": str(v["last"]), "tokens": dict(v["tokens"])}
                                    for k, v in seats.items()}, "human_messages": humans, "commits": authors}, indent=2))
        return
    print(f"fields seen on messages: {', '.join(sorted(fields))}\n")
    total = sum(s["text"] + s["tool"] for s in seats.values()) or 1
    print("| Seat | Messages | Tool events | Share of activity | Commits | Active from | to | Tokens | $ |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, s in sorted(seats.items()):
        share = 100 * (s["text"] + s["tool"]) / total
        tok = ", ".join(f"{k} {int(v):,}" for k, v in s["tokens"].items()) or "n/a"
        fmt = lambda t: t.strftime("%m-%d %H:%M") if t else "?"
        print(f"| {name} | {s['text']} | {s['tool']} | {share:.0f}% | {authors.get(name, 0)} | {fmt(s['first'])} | {fmt(s['last'])} | {tok} | {s['cost']:.2f} |")
    print(f"\nHuman text messages (should be one per run): {dict(humans) or 'none'}")
    print(f"Commits by author: {dict(authors)}")
    top = max(seats.values(), key=lambda s: s["text"] + s["tool"], default=None)
    if top and (top["text"] + top["tool"]) / total > 0.6:
        print("WARN: one seat carries over 60% of activity; judges read that as undistributed work.")


if __name__ == "__main__":
    main()
