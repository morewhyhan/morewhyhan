const fs = require('fs');
const os = require('os');
const path = require('path');
const zlib = require('zlib');
const readline = require('readline');

const LIVE = path.join(os.homedir(), '.codex', 'sessions');
const ARCHIVE = path.join(os.homedir(), '.codex', 'archived_sessions');
const OUT = path.join(__dirname, '..', 'data', 'codex-usage.json');
const BASE = path.join(__dirname, '..', 'data', 'codex-baseline.json');
const WEEK = 7 * 24 * 3600 * 1000;
const now = Date.now();

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

function diffCount(diff) {
  let a = 0, d = 0;
  for (const line of String(diff || '').split('\n')) {
    if (line.startsWith('+') && !line.startsWith('+++')) a++;
    else if (line.startsWith('-') && !line.startsWith('---')) d++;
  }
  return [a, d];
}

const acc = {
  active: 0, added: 0, removed: 0, sessions: 0, prompts: 0, promptChars: 0,
  tin: 0, tout: 0, models: {}, weekActive: 0, weekAdded: 0, weekPrompts: 0, maxTs: 0,
};

async function processFile(file) {
  const raw = fs.createReadStream(file);
  const input = file.endsWith('.zst') ? raw.pipe(zlib.createZstdDecompress()) : raw;
  const rl = readline.createInterface({ input, crlfDelay: Infinity });
  let model = 'unknown';
  let has = false;
  let stamps = [];
  for await (const line of rl) {
    if (line.length < 20) continue;
    const pos = line.indexOf('"timestamp":"');
    let ts = NaN;
    if (pos !== -1) {
      const start = pos + 13;
      const end = line.indexOf('"', start);
      if (end > start) ts = Date.parse(line.slice(start, end));
    }
    if (!Number.isFinite(ts)) continue;
    if (!has) { has = true; acc.sessions += 1; }
    stamps.push(ts);
    if (ts > acc.maxTs) acc.maxTs = ts;
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
        let a = 0, d = 0;
        for (const ch of Object.values(item.changes || {})) {
          if (ch.type === 'add') a += String(ch.content || '').split('\n').filter((x) => x.trim()).length;
          else { const r = diffCount(ch.unified_diff); a += r[0]; d += r[1]; }
        }
        acc.added += a; acc.removed += d;
        if (ts >= now - WEEK) acc.weekAdded += a;
        if (a > 0) acc.models[model] = (acc.models[model] || 0) + a;
      } else if (item.type === 'UserMessage') {
        const text = (item.content || []).map((c) => c.text || '').join('');
        acc.prompts += 1; acc.promptChars += text.length;
        if (ts >= now - WEEK) acc.weekPrompts += 1;
      }
    } else if (p.type === 'token_count') {
      const u = (p.info || {}).last_token_usage;
      if (u) {
        acc.tin += Number(u.input_tokens || 0);
        acc.tout += Number(u.output_tokens || 0);
      }
    }
  }
  stamps.sort((a, b) => a - b);
  for (let i = 1; i < stamps.length; i += 1) {
    const gap = stamps[i] - stamps[i - 1];
    if (gap > 0) {
      const capped = Math.min(gap, 900000) / 1000;
      acc.active += capped;
      if (stamps[i] >= now - WEEK) acc.weekActive += capped;
    }
  }
}

(async () => {
  const files = walk(ARCHIVE, []).concat(walk(LIVE, []));
  console.log('files to parse:', files.length);
  let i = 0;
  for (const f of files) {
    try { await processFile(f); } catch (e) { console.log('skip', path.basename(f), e.message); }
    i += 1;
    if (i % 50 === 0) console.log('parsed', i);
  }
  const models = Object.entries(acc.models).filter(([n, v]) => v > 0 && n !== 'unknown').sort((a, b) => b[1] - a[1]).slice(0, 5).map(([name, lines]) => ({ name, lines }));
  const baseline = {
    active_seconds: Math.round(acc.active),
    added: acc.added, removed: acc.removed, sessions: acc.sessions,
    prompts: acc.prompts, promptChars: acc.promptChars,
    tokens: { in: acc.tin, out: acc.tout },
    models: acc.models, last_event_ms: acc.maxTs,
  };
  fs.writeFileSync(BASE, JSON.stringify(baseline, null, 2) + '\n');
  const out = {
    generated_at: new Date().toISOString(),
    all: {
      sessions: acc.sessions, prompts: acc.prompts,
      avg_prompt_chars: acc.prompts ? Math.round(acc.promptChars / acc.prompts) : 0,
      added: acc.added, removed: acc.removed, models,
      tokens: { in: acc.tin, out: acc.tout }, active_seconds: Math.round(acc.active),
    },
    week: {
      sessions: 0, prompts: acc.weekPrompts, avg_prompt_chars: 0,
      added: acc.weekAdded, removed: 0, models: [],
      tokens: { in: 0, out: 0 }, active_seconds: Math.round(acc.weekActive),
    },
  };
  fs.writeFileSync(OUT, JSON.stringify(out, null, 2) + '\n');
  console.log('DONE active_h=' + (acc.active / 3600).toFixed(1) + ' added=' + acc.added + ' sessions=' + acc.sessions + ' tokens=' + acc.tin + '/' + acc.tout);
})();
