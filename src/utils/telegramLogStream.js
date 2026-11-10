#!/usr/bin/env node
/**
 * Run a command and live-stream its logs to Telegram.
 *
 * Output is passed through to the console unchanged (GitHub Actions logs still work),
 * and one Telegram message is kept updated with the latest lines every few seconds.
 * When the command ends, the message shows ✅ passed / ❌ failed / ⛔ cancelled.
 *
 *   node src/utils/telegramLogStream.js "Statutory Report Download" -- pnpm auto --retries=0
 *
 * Exits with the command's exit code. Uses TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (and
 * optional RUN_URL); without them it just runs the command. Telegram errors never affect
 * the command.
 */
import { spawn } from "node:child_process";
import readline from "node:readline";

const sep = process.argv.indexOf("--");
if (sep === -1 || sep === process.argv.length - 1) {
  console.error('Usage: telegramLogStream.js "<title>" -- <command> [args...]');
  process.exit(2);
}
const TITLE = process.argv.slice(2, sep).join(" ") || "Workflow logs";
const [cmd, ...cmdArgs] = process.argv.slice(sep + 1);

const TOKEN = process.env.TELEGRAM_BOT_TOKEN?.trim();
const CHAT_ID = process.env.TELEGRAM_CHAT_ID?.trim();
const RUN_URL = process.env.RUN_URL || "";
const UPDATE_EVERY_MS = 2000; // Telegram allows ~1 edit/sec per chat; 2s leaves room for other messages
const MAX_TAIL_CHARS = 3000; // message limit is 4096 chars incl. header and HTML escaping

const enabled = Boolean(TOKEN && CHAT_ID);
const startedAt = Date.now();
const lines = [];
let messageId = null;
let lastSentText = "";
let state = { icon: "🟢 Running" };
let cancelled = false;
let sending = Promise.resolve();

const stripAnsi = (s) => s.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, "");
const escapeHtml = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function elapsed() {
  const sec = Math.round((Date.now() - startedAt) / 1000);
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

function buildText() {
  let tail = "";
  for (let i = lines.length - 1; i >= 0; i--) {
    const next = lines[i] + "\n" + tail;
    if (next.length > MAX_TAIL_CHARS) break;
    tail = next;
  }
  const link = RUN_URL ? ` · <a href="${RUN_URL}">run logs</a>` : "";
  return (
    `<b>${escapeHtml(TITLE)}</b>\n${state.icon} · ${elapsed()} · ${lines.length} lines${link}\n` +
    `<pre>${escapeHtml(tail.trimEnd() || "(no output yet)")}</pre>`
  );
}

async function telegram(method, body) {
  const res = await fetch(`https://api.telegram.org/bot${TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: CHAT_ID, parse_mode: "HTML", disable_web_page_preview: true, ...body }),
    signal: AbortSignal.timeout(10000),
  });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) {
    const err = new Error(data.description || `HTTP ${res.status}`);
    err.retryAfter = data.parameters?.retry_after; // seconds, set on HTTP 429
    throw err;
  }
  return data.result;
}

let pausedUntil = 0; // Telegram rate-limit backoff (from retry_after)
let flushQueued = false;

// Serialize sends so edits never race each other, and never queue more than one:
// each send always uses the latest lines, so a slow send never builds a backlog.
function flush() {
  if (!enabled || flushQueued) return sending;
  flushQueued = true;
  sending = sending.then(async () => {
    flushQueued = false;
    const wait = pausedUntil - Date.now();
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    const text = buildText();
    if (text === lastSentText) return;
    try {
      if (messageId === null) {
        messageId = (await telegram("sendMessage", { text })).message_id;
      } else {
        await telegram("editMessageText", { message_id: messageId, text });
      }
      lastSentText = text;
    } catch (err) {
      if (err.retryAfter) {
        pausedUntil = Date.now() + err.retryAfter * 1000;
        process.stderr.write(`[TelegramLogStream] rate limited, pausing ${err.retryAfter}s\n`);
      } else if (!String(err.message).includes("not modified")) {
        process.stderr.write(`[TelegramLogStream] ${err.message}\n`);
      }
    }
  });
  return sending;
}

const child = spawn(cmd, cmdArgs, { stdio: ["inherit", "pipe", "pipe"], env: process.env });

function collect(stream, out) {
  readline.createInterface({ input: stream, crlfDelay: Infinity }).on("line", (line) => {
    out.write(line + "\n");
    const clean = stripAnsi(line).trimEnd();
    if (clean) lines.push(clean);
    if (lines.length > 2000) lines.splice(0, lines.length - 2000);
  });
}
collect(child.stdout, process.stdout);
collect(child.stderr, process.stderr);

// GitHub sends SIGINT, then SIGTERM, when a run is cancelled: pass it on to the command
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    cancelled = true;
    state = { icon: "⛔ Cancelling…" };
    child.kill(sig);
    flush();
  });
}

const timer = enabled ? setInterval(flush, UPDATE_EVERY_MS) : null;
if (enabled) flush();

child.on("error", (err) => {
  lines.push(`Failed to start "${cmd}": ${err.message}`);
});

child.on("close", async (code, signal) => {
  if (timer) clearInterval(timer);
  if (cancelled || signal) state = { icon: "⛔ Cancelled" };
  else if (code === 0) state = { icon: "✅ Passed" };
  else state = { icon: `❌ Failed (exit code ${code})` };
  await flush();
  process.exit(code ?? 1);
});
