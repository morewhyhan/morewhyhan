import json, os
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCH = os.path.join(BASE, "data", "archive")
DAY_CAP = 86400


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def union_seconds(pairs):
    pairs = sorted(pairs)
    total = 0
    cur_start = None
    cur_end = None
    for a, b in pairs:
        if cur_start is None:
            cur_start, cur_end = a, b
        elif a <= cur_end:
            cur_end = max(cur_end, b)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = a, b
    if cur_start is not None:
        total += cur_end - cur_start
    return total


def compose_sessions(sessions):
    daily_pairs = {}
    totals = {"sessions": 0, "active_seconds": 0, "added": 0, "removed": 0, "tokens_in": 0, "tokens_out": 0, "prompts": 0}
    models = {}
    for s in sessions:
        totals["sessions"] += 1
        totals["added"] += int(s.get("added") or 0)
        totals["removed"] += int(s.get("removed") or 0)
        totals["prompts"] += int(s.get("prompts") or 0)
        tok = s.get("tokens") or {}
        totals["tokens_in"] += int(tok.get("in") or 0)
        totals["tokens_out"] += int(tok.get("out") or 0)
        for name, lines in (s.get("models") or {}).items():
            models[name] = models.get(name, 0) + int(lines or 0)
        for pair in s.get("intervals") or []:
            if len(pair) != 2:
                continue
            a, b = int(pair[0]), int(pair[1])
            if b <= a:
                continue
            day = datetime.fromtimestamp(a, timezone.utc).strftime("%Y-%m-%d")
            daily_pairs.setdefault(day, []).append((a, b))
    daily = {d: min(union_seconds(v), DAY_CAP) for d, v in daily_pairs.items()}
    totals["active_seconds"] = sum(daily.values())
    totals["models"] = models
    totals["daily"] = daily
    return totals


def main():
    codex = compose_sessions(load(os.path.join(ARCH, "sessions.json")).get("sessions") or [])
    dsh = compose_sessions(load(os.path.join(ARCH, "dsh-sessions.json")).get("sessions") or [])

    now = datetime.now(timezone.utc)
    week_start = now.strftime("%Y-%m-%d")
    week_cut = (now.timestamp() - 7 * 86400)

    def week_of(daily):
        total = 0
        for d, v in daily.items():
            try:
                ts = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            except Exception:
                continue
            if ts >= week_cut:
                total += v
        return total

    models_sorted = sorted(codex["models"].items(), key=lambda kv: -kv[1])[:5]
    codex_usage = {
        "generated_at": now.isoformat(),
        "all": {
            "sessions": codex["sessions"],
            "prompts": codex["prompts"],
            "avg_prompt_chars": 0,
            "added": codex["added"],
            "removed": codex["removed"],
            "models": [{"name": n, "lines": v} for n, v in models_sorted if n != "unknown"],
            "tokens": {"in": codex["tokens_in"], "out": codex["tokens_out"]},
            "active_seconds": codex["active_seconds"],
        },
        "week": {
            "sessions": 0,
            "prompts": 0,
            "avg_prompt_chars": 0,
            "added": 0,
            "removed": 0,
            "models": [],
            "tokens": {"in": 0, "out": 0},
            "active_seconds": week_of(codex["daily"]),
        },
    }
    dsh_usage = {
        "generated_at": now.isoformat(),
        "sessions": dsh["sessions"],
        "prompts": dsh["prompts"],
        "tokens": {"in": dsh["tokens_in"], "out": dsh["tokens_out"]},
        "active_seconds": dsh["active_seconds"],
        "week_active_seconds": week_of(dsh["daily"]),
        "models": {},
    }
    for name, data in (("codex-usage.json", codex_usage), ("dsh-usage.json", dsh_usage)):
        with open(os.path.join(BASE, "data", name), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    print("codex sessions", codex["sessions"], "h", round(codex["active_seconds"] / 3600, 1))
    print("dsh   sessions", dsh["sessions"], "h", round(dsh["active_seconds"] / 3600, 1))


if __name__ == "__main__":
    main()
