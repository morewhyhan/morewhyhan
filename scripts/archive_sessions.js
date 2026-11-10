const fs = require('fs');
const os = require('os');
const path = require('path');
const zlib = require('zlib');
const crypto = require('crypto');
const readline = require('readline');

const LIVE = path.join(os.homedir(), '.codex', 'sessions');
const ARCHIVE = path.join(os.homedir(), '.codex', 'archived_sessions');
const OUTDIR = path.join(__dirname, '..', 'data', 'archive');
const INDEX = path.join(OUTDIR, 'sessions.json');
fs.mkdirSync(OUTDIR, { recursive: true });

function walk(dir, acc) {
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return acc; }
  for (const e of entries) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, acc);
    else if (e.name.endsWith('.jsonl') || e.name.endsWith('.jsonl.zst')) acc.push(p);
  }
  return acc;
}

function sha256(file) {
  return new Promise((resolve) => {
    const h = crypto.createHash('sha256');
    const s = fs.createReadStream(file);
    s.on('data', (c) => h.update(c));
    s.on('end', () => resolve(h.digest('hex')));
    s.on('error', () => resolve(''));
  });
}

function diffCount(diff) {
  let a = 0, d = 0;
  for (const line of String(diff || '').split('\n')) {
    if (line.startsWith('+') && !line.startsWith('+++')) a++;
    else if (line.startsWith('-') && !line.startsWith('---')) d++;
  }
  return [a, d];
}

async function analyze(file) {
  const size = fs.statSync(file).size;
  const digest = await sha256(file);
  const raw = fs.createReadStream(file);
  const input = file.endsWith('.zst') ? raw.pipe(zlib.createZstdDecompress()) : raw;
  const rl = readline.createInterface({ input, crlfDelay: Infinity });
  let model = 'unknown';
  let events = 0, first = 0, last = 0, added = 0, removed = 0, tin = 0, tout = 0;
  const models = {};
  const stamps = [];
  for await (const line of rl) {
    const pos = line.indexOf('"timestamp":"');
    if (pos === -1) continue;
    const start = pos + 13;
    const end = line.indexOf('"', start);
    if (end <= start) continue;
    const ts = Date.parse(line.slice(start, end));
    if (!Number.isFinite(ts)) continue;
    events += 1;
    if (!first || ts < first) first = ts;
    if (!last || ts > last) last = ts;
    stamps.push(ts);
    if (!line.includes('"FileChange"') && !line.includes('"thread_settings_applied"') && !line.includes('"UserMessage"') && !line.includes('"token_count"')) continue;
    let obj;
    try { obj = JSON.parse(line); } catch { continue; }
    const p = obj.payload;
    if (!p || typeof p !== 'object') continue;
    if (p.type === 'thread_settings_applied') {
      const m = p.thread_settings && p.thread_settings.model;
      if (m) model = m;
    } else if (p.type === 'item_completed') {
      const item = p.item || {};
      if (item.type === 'FileChange') {
        for (const ch of Object.values(item.changes || {})) {
          if (ch.type === 'add') {
            const n = String(ch.content || '').split('\n').filter((x) => x.trim()).length;
            added += n; models[model] = (models[model] || 0) + n;
          } else {
            const r = diffCount(ch.unified_diff);
            added += r[0]; removed += r[1];
            if (r[0] > 0) models[model] = (models[model] || 0) + r[0];
          }
        }
      }
    } else if (p.type === 'token_count') {
      const u = (p.info || {}).last_token_usage;
      if (u) { tin += Number(u.input_tokens || 0); tout += Number(u.output_tokens || 0); }
    }
  }
  stamps.sort((a, b) => a - b);
  const intervals = [];
  if (stamps.length) {
    let segStart = stamps[0];
    let prev = stamps[0];
    for (let i = 1; i < stamps.length; i += 1) {
      if (stamps[i] - prev > 900000) {
        intervals.push([Math.floor(segStart / 1000), Math.floor(prev / 1000)]);
        segStart = stamps[i];
      }
      prev = stamps[i];
    }
    intervals.push([Math.floor(segStart / 1000), Math.floor(prev / 1000)]);
  }
  const active = intervals.reduce((acc, pair) => acc + (pair[1] - pair[0]), 0);
  return {
    file: path.basename(file),
    origin: file.includes('archived_sessions') ? 'archive' : 'live',
    size,
    sha256: digest,
    events,
    start: first ? new Date(first).toISOString() : null,
    end: last ? new Date(last).toISOString() : null,
    active_seconds: Math.round(active),
    intervals,
    added,
    removed,
    tokens: { in: tin, out: tout },
    models,
  };
}

(async () => {
  let index = { generated_at: null, sessions: [] };
  if (!process.argv.includes('--rebuild')) {
    try { index = JSON.parse(fs.readFileSync(INDEX, 'utf8')); } catch {}
  }
  const known = new Set((index.sessions || []).map((s) => s.file));
  const files = walk(ARCHIVE, []).concat(walk(LIVE, []));
  const fresh = files.filter((f) => !known.has(path.basename(f)));
  console.log('files:', files.length, 'new:', fresh.length);
  for (const f of fresh) {
    try { index.sessions.push(await analyze(f)); } catch (e) { console.log('skip', path.basename(f), e.message); }
  }
  index.sessions.sort((a, b) => String(a.start).localeCompare(String(b.start)));
  const daily = {};
  const totals = { sessions: 0, active_seconds: 0, added: 0, removed: 0, tokens_in: 0, tokens_out: 0 };
  for (const s of index.sessions) {
    totals.sessions += 1;
    totals.active_seconds += s.active_seconds;
    totals.added += s.added;
    totals.removed += s.removed;
    totals.tokens_in += s.tokens.in;
    totals.tokens_out += s.tokens.out;
    const day = (s.start || '').slice(0, 10);
    if (!day) continue;
    if (!daily[day]) daily[day] = { sessions: 0, active_seconds: 0, added: 0, tokens_in: 0, tokens_out: 0 };
    daily[day].sessions += 1;
    daily[day].active_seconds += s.active_seconds;
    daily[day].added += s.added;
    daily[day].tokens_in += s.tokens.in;
    daily[day].tokens_out += s.tokens.out;
  }
  index.generated_at = new Date().toISOString();
  index.totals = totals;
  fs.writeFileSync(INDEX, JSON.stringify(index, null, 1) + '\n');
  fs.writeFileSync(path.join(OUTDIR, 'daily.json'), JSON.stringify(daily, null, 1) + '\n');
  console.log('DONE total sessions', totals.sessions, 'new', fresh.length);
})();
