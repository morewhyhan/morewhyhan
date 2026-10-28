#!/usr/bin/env node
// Aggregate Codex session logs into data/codex-usage.json
const fs = require('fs');
const os = require('os');
const path = require('path');
const readline = require('readline');

const ROOT = path.join(os.homedir(), '.codex', 'sessions');
const OUT = path.join(__dirname, '..', 'data', 'codex-usage.json');
const WEEK_MS = 7 * 24 * 3600 * 1000;
const ALL_MS = 90 * 24 * 3600 * 1000;
const now = Date.now();

function walk(dir, acc) {
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return acc; }
  for (const e of entries) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, acc);
    else if (e.name.endsWith('.jsonl')) acc.push(p);
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

async function parseFile(file, acc) {
  const rl = readline.createInterface({ input: fs.createReadStream(file), crlfDelay: Infinity });
  let model = 'unknown';
  let inWeek = false;
  let inAll = false;
  for await (const line of rl) {
    if (!line.includes('"FileChange"') && !line.includes('"thread_settings_applied"') && !line.includes('"UserMessage"')) continue;
    let obj;
    try { obj = JSON.parse(line); } catch { continue; }
    const p = obj.payload;
    if (!p || typeof p !== 'object') continue;
    const ts = obj.timestamp ? Date.parse(obj.timestamp) : NaN;
    if (!Number.isFinite(ts)) continue;
    if (p.type === 'thread_settings_applied') {
      const m = p.thread_settings && p.thread_settings.model;
      if (m) model = m;
    } else if (p.type === 'item_completed') {
      const item = p.item || {};
      if (item.type === 'FileChange') {
        let a = 0, d = 0;
        for (const ch of Object.values(item.changes || {})) {
          if (ch.type === 'add') {
            a += String(ch.content || '').split('\n').filter((x) => x.trim()).length;
          } else {
            const [da, dd] = diffCount(ch.unified_diff);
            a += da; d += dd;
          }
        }
        add(acc, ts, model, a, d, 0);
      } else if (item.type === 'UserMessage') {
        const text = (item.content || []).map((c) => c.text || '').join('');
        add(acc, ts, model, 0, 0, text.length);
      }
      if (ts >= now - ALL_MS) inAll = true;
      if (ts >= now - WEEK_MS) inWeek = true;
    }
  }
  if (inAll) acc.all.sessions += 1;
  if (inWeek) acc.week.sessions += 1;
}

const empty = () => ({ sessions: 0, prompts: 0, promptChars: 0, added: 0, removed: 0, models: {} });

function add(acc, ts, model, a, d, promptLen) {
  if (ts >= now - ALL_MS) bump(acc.all, model, a, d, promptLen);
  if (ts >= now - WEEK_MS) bump(acc.week, model, a, d, promptLen);
}

function bump(bucket, model, a, d, promptLen) {
  bucket.added += a;
  bucket.removed += d;
  if (promptLen > 0) { bucket.prompts += 1; bucket.promptChars += promptLen; }
  if (a > 0) bucket.models[model] = (bucket.models[model] || 0) + a;
}

function finalize(b) {
  const models = Object.entries(b.models)
    .filter(([, v]) => v > 0)
    .sort((x, y) => y[1] - x[1])
    .slice(0, 6)
    .map(([name, lines]) => ({ name, lines }));
  return {
    sessions: b.sessions,
    prompts: b.prompts,
    avg_prompt_chars: b.prompts ? Math.round(b.promptChars / b.prompts) : 0,
    added: b.added,
    removed: b.removed,
    models,
  };
}

(async () => {
  const files = walk(ROOT, []);
  const acc = { sessions: 0, week: empty(), all: empty() };
  for (const f of files) {
    try {
      const st = fs.statSync(f);
      if (now - st.mtimeMs > ALL_MS) continue;
      await parseFile(f, acc);
    } catch {}
  }
  const out = {
    generated_at: new Date().toISOString(),
    source: 'codex-sessions',
    week: finalize(acc.week),
    all: finalize(acc.all),
  };
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(out, null, 2) + '\n');
  console.log('wrote', OUT, '| week', out.week.added, '| all', out.all.added);
})();
