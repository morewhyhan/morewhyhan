import glob, hashlib, json, os, sys
from datetime import datetime, timezone

import zstandard

HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, ".dsh", "sessions")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(BASE, "data", "archive", "dsh-sessions.json")
OUT = os.path.join(BASE, "data", "dsh-usage.json")
CAP = 900
dctx = zstandard.ZstdDecompressor()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def analyze(path):
    size = os.path.getsize(path)
    digest = sha256(path)
    with open(path, "rb") as fh:
        data = dctx.stream_reader(fh, read_across_frames=True).read()
    times = []
    events = 0
    prompts = 0
    tin = tout = 0
    models = {}
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        tm = o.get("time")
        if isinstance(tm, (int, float)):
            times.append(float(tm) / 1000.0)
            events += 1
        if t == "user/message":
            prompts += 1
        elif t == "assistant/message":
            d = o.get("data") or {}
            usage = d.get("usage") or (d.get("message") or {}).get("usage") or {}
            tin += int(usage.get("inputTokens") or 0)
            tout += int(usage.get("outputTokens") or 0)
        elif t == "request/header":
            cfg = ((o.get("data") or {}).get("header") or {}).get("config") or {}
            m = cfg.get("model")
            if m:
                models[m] = models.get(m, 0) + 1
    intervals = []
    if times:
        times.sort()
        seg_start = times[0]
        prev = times[0]
        for t in times[1:]:
            if t - prev > CAP:
                intervals.append([int(seg_start), int(prev)])
                seg_start = t
            prev = t
        intervals.append([int(seg_start), int(prev)])
    active = sum(b - a for a, b in intervals)
    return {
        "file": os.path.basename(os.path.dirname(path)),
        "path": os.path.relpath(path, ROOT).replace("\\", "/"),
        "size": size,
        "sha256": digest,
        "events": events,
        "prompts": prompts,
        "start": datetime.fromtimestamp(min(times), timezone.utc).isoformat() if times else None,
        "end": datetime.fromtimestamp(max(times), timezone.utc).isoformat() if times else None,
        "active_seconds": int(active),
        "intervals": intervals,
        "tokens": {"in": tin, "out": tout},
        "models": models,
    }


def main():
    files = glob.glob(os.path.join(ROOT, "**", "*.jsonl.zstd"), recursive=True)
    index = {"generated_at": None, "sessions": []}
    if "--rebuild" not in sys.argv:
        try:
            with open(ARCHIVE, encoding="utf-8") as fh:
                index = json.load(fh)
        except Exception:
            index = {"generated_at": None, "sessions": []}
    known = {s["path"] for s in index["sessions"]}
    fresh = 0
    for path in files:
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        if rel in known:
            continue
        try:
            index["sessions"].append(analyze(path))
            fresh += 1
        except Exception as exc:
            print("skip", rel, exc)
    index["sessions"].sort(key=lambda s: s.get("start") or "")
    daily = {}
    totals = {"sessions": 0, "prompts": 0, "active_seconds": 0, "tokens_in": 0, "tokens_out": 0}
    for s in index["sessions"]:
        totals["sessions"] += 1
        totals["prompts"] += s["prompts"]
        totals["active_seconds"] += s["active_seconds"]
        totals["tokens_in"] += s["tokens"]["in"]
        totals["tokens_out"] += s["tokens"]["out"]
        day = (s.get("start") or "")[:10]
        if not day:
            continue
        bucket = daily.setdefault(day, {"sessions": 0, "active_seconds": 0, "tokens_in": 0, "tokens_out": 0})
        bucket["sessions"] += 1
        bucket["active_seconds"] += s["active_seconds"]
        bucket["tokens_in"] += s["tokens"]["in"]
        bucket["tokens_out"] += s["tokens"]["out"]
    index["generated_at"] = datetime.now(timezone.utc).isoformat()
    index["totals"] = totals
    os.makedirs(os.path.dirname(ARCHIVE), exist_ok=True)
    with open(ARCHIVE, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    with open(os.path.join(BASE, "data", "archive", "dsh-daily.json"), "w", encoding="utf-8") as fh:
        json.dump(daily, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    usage = {
        "generated_at": index["generated_at"],
        "sessions": totals["sessions"],
        "prompts": totals["prompts"],
        "tokens": {"in": totals["tokens_in"], "out": totals["tokens_out"]},
        "active_seconds": totals["active_seconds"],
        "models": {},
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(usage, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("files", len(files), "new", fresh, "total sessions", totals["sessions"], "active_h", round(totals["active_seconds"] / 3600, 1))


if __name__ == "__main__":
    main()
