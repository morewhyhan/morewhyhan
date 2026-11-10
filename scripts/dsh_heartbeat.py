import glob, json, os, time, urllib.parse, urllib.request

import zstandard

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(os.path.expanduser("~"), ".dsh", "sessions")
STATE = os.path.join(BASE, "data", "dsh-heartbeat-state.json")
MIN_GAP = 60
BATCH = 100
UA = "wakatime/v1.0.0 (windows-10.0.26200) go1.27.3 deepseek-harness/1.0"
dctx = zstandard.ZstdDecompressor()
EVENT_TYPES = {"user/message", "workspace/changes", "assistant/message", "tool/call"}


def api_key():
    cfg = os.path.join(os.path.expanduser("~"), ".wakatime.cfg")
    try:
        with open(cfg, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("api_key"):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def post(beats, key):
    url = "https://api.wakatime.com/api/v1/users/current/heartbeats.bulk?api_key=" + urllib.parse.quote(key)
    body = json.dumps(beats).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, resp.read().decode()[:160]


def activity_times(path):
    with open(path, "rb") as fh:
        data = dctx.stream_reader(fh, read_across_frames=True).read()
    out = []
    for line in data.decode("utf-8", "replace").splitlines():
        if '"user/message"' not in line and '"workspace/changes"' not in line and '"assistant/message"' not in line and '"tool/call"' not in line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") not in EVENT_TYPES:
            continue
        tm = o.get("time")
        if isinstance(tm, (int, float)):
            out.append(int(tm / 1000))
    out.sort()
    kept = []
    last = -10 ** 9
    for t in out:
        if t - last >= MIN_GAP:
            kept.append(t)
            last = t
    return kept


def main():
    key = api_key()
    if not key:
        print("no api key")
        return
    state = {"last_sent": 0}
    try:
        with open(STATE, encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        state["last_sent"] = int(time.time()) - 600
    since = int(state.get("last_sent") or 0)
    now = int(time.time())

    beats = []
    files = glob.glob(os.path.join(ROOT, "**", "*.jsonl.zstd"), recursive=True)
    for path in files:
        try:
            if os.path.getmtime(path) < since - 86400:
                continue
            for t in activity_times(path):
                if since < t <= now:
                    beats.append({
                        "entity": "DeepSeek Harness",
                        "type": "app",
                        "time": t,
                        "category": "ai coding",
                        "project": "deepseek-harness",
                        "is_write": False,
                        "user_agent": UA,
                    })
        except Exception as exc:
            print("skip", os.path.basename(path), exc)
    beats.sort(key=lambda x: x["time"])
    if not beats:
        print("nothing new")
        return
    sent = 0
    for i in range(0, len(beats), BATCH):
        chunk = beats[i:i + BATCH]
        try:
            status, body = post(chunk, key)
            sent += len(chunk)
            print("batch", i // BATCH + 1, "status", status, body[:70])
        except Exception as exc:
            print("batch failed", exc)
            break
    if sent:
        state["last_sent"] = beats[sent - 1]["time"]
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    print("sent", sent)


if __name__ == "__main__":
    main()
