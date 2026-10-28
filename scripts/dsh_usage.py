import glob, json, os
import zstandard

root = os.path.expanduser("~/.dsh/sessions")
out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "dsh-usage.json")
CAP = 900
dctx = zstandard.ZstdDecompressor()

files = glob.glob(os.path.join(root, "**", "*.jsonl.zstd"), recursive=True)
total_active = 0.0
sessions = 0
prompts = 0
tin = tout = 0
models = {}
first = last = None

for path in files:
    try:
        with open(path, "rb") as fh:
            data = dctx.stream_reader(fh, read_across_frames=True).read()
    except Exception:
        continue
    times = []
    has = False
    for line in data.decode("utf-8", "replace").splitlines():
        if '"user/message"' not in line and '"assistant/message"' not in line and '"time"' not in line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        tm = o.get("time")
        if isinstance(tm, (int, float)):
            times.append(float(tm) / 1000.0)
        if t == "user/message":
            prompts += 1
        elif t == "assistant/message":
            d = o.get("data") or {}
            usage = d.get("usage") or (d.get("message") or {}).get("usage") or {}
            tin += int(usage.get("inputTokens") or 0)
            tout += int(usage.get("outputTokens") or 0)
            has = True
        elif t == "request/header":
            cfg = ((o.get("data") or {}).get("header") or {}).get("config") or {}
            m = cfg.get("model")
            if m:
                models[m] = models.get(m, 0) + 1
    if not has or len(times) < 2:
        continue
    sessions += 1
    times.sort()
    if first is None or times[0] < first:
        first = times[0]
    if last is None or times[-1] > last:
        last = times[-1]
    for a, b in zip(times, times[1:]):
        d = b - a
        if d > 0:
            total_active += min(d, CAP)

out = {
    "sessions": sessions,
    "prompts": prompts,
    "tokens": {"in": tin, "out": tout},
    "active_seconds": int(total_active),
    "models": models,
}
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
print("dsh:", out)
