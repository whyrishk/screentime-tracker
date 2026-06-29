const API = '';
const PING_INTERVAL = 5; // seconds between server pings while active

const siteInput = document.getElementById('siteInput');
const toggleBtn = document.getElementById('toggleBtn');
const ticker = document.getElementById('ticker');
const statusEl = document.getElementById('status');
const hint = document.getElementById('hint');
const wallClock = document.getElementById('wallClock');
const resetBtn = document.getElementById('resetBtn');

let tracking = false;
let site = '';
let sessionSeconds = 0;     // total seconds since clock-in
let unsynced = 0;           // seconds accumulated but not yet sent to server
let tickHandle = null;

// ---- colors per site (deterministic) ----
const PALETTE = ['#e8a33d', '#5fbe8a', '#7aa7e8', '#d97171', '#c08fe0', '#5fc9be', '#e0b95f'];
function colorFor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function fmt(seconds) {
  const h = Math.floor(seconds / 3600).toString().padStart(2, '0');
  const m = Math.floor((seconds % 3600) / 60).toString().padStart(2, '0');
  const s = Math.floor(seconds % 60).toString().padStart(2, '0');
  return `${h}:${m}:${s}`;
}
function fmtShort(seconds) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// ---- wall clock ----
function updateWallClock() {
  wallClock.textContent = new Date().toLocaleTimeString();
}
setInterval(updateWallClock, 1000);
updateWallClock();

// ---- core tick: only counts while document is visible/focused ----
function isActiveDoc() {
  return document.visibilityState === 'visible' && document.hasFocus();
}

function tick() {
  if (!tracking) return;
  if (isActiveDoc()) {
    sessionSeconds += 1;
    unsynced += 1;
    ticker.textContent = fmt(sessionSeconds);
    setLive(true);
  } else {
    setLive(false);
  }
  if (unsynced >= PING_INTERVAL) flushToServer();
}

function setLive(isLive) {
  statusEl.classList.toggle('live', isLive);
  statusEl.innerHTML = `<i class="dot"></i> ${isLive ? 'tracking' : 'paused (tab not focused)'}`;
}

async function flushToServer() {
  if (unsynced <= 0 || !site) return;
  const seconds = unsynced;
  unsynced = 0;
  try {
    await fetch(`${API}/api/log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ site, seconds })
    });
    loadStats();
  } catch (e) {
    unsynced += seconds; // retry next tick
  }
}

function startTracking() {
  const val = siteInput.value.trim();
  if (!val) { siteInput.focus(); return; }
  site = val.toLowerCase();
  tracking = true;
  sessionSeconds = 0;
  unsynced = 0;
  siteInput.disabled = true;
  toggleBtn.textContent = 'Clock out';
  toggleBtn.classList.add('active');
  hint.textContent = `Logging time to "${site}" while this tab is open and focused.`;
  tickHandle = setInterval(tick, 1000);
  setLive(isActiveDoc());
}

function stopTracking() {
  tracking = false;
  clearInterval(tickHandle);
  flushToServer();
  siteInput.disabled = false;
  toggleBtn.textContent = 'Clock in';
  toggleBtn.classList.remove('active');
  statusEl.classList.remove('live');
  statusEl.innerHTML = `<i class="dot"></i> idle`;
  ticker.textContent = '00:00:00';
  hint.textContent = "Type what you're working on, then clock in. Time logs automatically while this tab stays open and focused.";
  sessionSeconds = 0;
}

toggleBtn.addEventListener('click', () => {
  tracking ? stopTracking() : startTracking();
});
siteInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !tracking) startTracking();
});

// flush time when the user leaves/closes the tab
window.addEventListener('beforeunload', () => {
  if (unsynced > 0 && site) {
    navigator.sendBeacon(`${API}/api/log`, JSON.stringify({ site, seconds: unsynced }));
  }
});

// ---- stats / rendering ----
const todayDate = document.getElementById('todayDate');
const todayRows = document.getElementById('todayRows');
const allTimeRows = document.getElementById('allTimeRows');
const weekChart = document.getElementById('weekChart');

function renderTodayRows(today) {
  if (!today.length) {
    todayRows.innerHTML = `<p class="empty">Nothing logged yet today. Clock in above to start the first entry.</p>`;
    return;
  }
  const total = today.reduce((a, s) => a + s.seconds, 0) || 1;
  todayRows.innerHTML = today.map(s => `
    <div class="ledger-row">
      <span class="site-name"><span class="swatch" style="background:${colorFor(s.site)}"></span>${escapeHtml(s.site)}</span>
      <span>${fmtShort(s.seconds)}</span>
      <span>${Math.round((s.seconds / total) * 100)}%</span>
    </div>
  `).join('');
}

function renderAllTime(allTime) {
  if (!allTime.length) {
    allTimeRows.innerHTML = `<p class="empty">No history yet.</p>`;
    return;
  }
  allTimeRows.innerHTML = allTime.slice(0, 8).map(s => `
    <div class="ledger-row">
      <span class="site-name"><span class="swatch" style="background:${colorFor(s.site)}"></span>${escapeHtml(s.site)}</span>
      <span>${fmtShort(s.seconds)}</span>
    </div>
  `).join('');
}

function renderWeek(days) {
  if (!days.length) {
    weekChart.innerHTML = `<p class="empty">No history yet.</p>`;
    return;
  }
  const max = Math.max(...days.map(d => d[1]), 1);
  weekChart.innerHTML = days.map(([day, seconds]) => {
    const h = Math.max(4, Math.round((seconds / max) * 110));
    const label = new Date(day + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short' });
    return `
      <div class="bar-col">
        <span class="bar-value">${fmtShort(seconds)}</span>
        <div class="bar" style="height:${h}px"></div>
        <span class="bar-label">${label}</span>
      </div>`;
  }).join('');
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function loadStats() {
  try {
    const res = await fetch(`${API}/api/stats`);
    const data = await res.json();
    todayDate.textContent = new Date(data.day + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
    renderTodayRows(data.today);
    renderAllTime(data.allTime);
    renderWeek(data.days);
  } catch (e) {
    console.error('Could not reach backend at', API || window.location.origin, e);
  }
}

resetBtn.addEventListener('click', async () => {
  if (!confirm('Clear all logged history? This cannot be undone.')) return;
  await fetch(`${API}/api/reset`, { method: 'DELETE' });
  loadStats();
});

// poll stats periodically so multi-tab usage stays in sync
loadStats();
setInterval(loadStats, 10000);
const express = require("express");
const app = express();

// routes
app.get("/", (req, res) => {
  res.send("Hello World");
});

// 👇 ADD THIS AT THE VERY END
app.listen(5000, "0.0.0.0", () => {
  console.log("Server running on port 5000");
});