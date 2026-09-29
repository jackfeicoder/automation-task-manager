importScripts('config.js');
const BASE = 'http://127.0.0.1:18765';
const STORE = 'agentrouter_job';
let ticking = false;
let timer;
let currentJob;

async function request(path, body) {
  const response = await fetch(BASE + path, {
    method: body ? 'POST' : 'GET', cache: 'no-store',
    headers: {Authorization: 'Bearer ' + BRIDGE_SECRET, 'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(2500)
  });
  if (!response.ok) throw new Error('bridge');
  return response.json();
}
async function save(job) { await chrome.storage.session.set({[STORE]: job}); }
async function progress(job) {
  if (job.stage !== job.reportedStage) {
    await request('/progress', {job: job.id, stage: job.stage}).catch(() => {});
    job.reportedStage = job.stage;
    await save(job);
  }
}
async function owned(job) {
  const ids = [...new Set([job.tab, ...(job.children || [])].filter(Number.isInteger))];
  return (await Promise.all(ids.map(id => chrome.tabs.get(id).catch(() => null)))).filter(Boolean);
}
async function cleanup(job, forget = true) {
  // Stored IDs come only from tabs.create or onCreated with a tracked opener.
  const tabs = await owned(job);
  if (tabs.length) await chrome.tabs.remove(tabs.map(t => t.id)).catch(() => {});
  if (forget) {
    if (currentJob === job) currentJob = null;
    await chrome.storage.session.remove(STORE);
  }
}
async function finish(job, status, loggedIn = false) {
  // Retain a pending result if the local response is lost, so retries do not log out again.
  job.result = {status, loggedIn};
  await save(job);
  await cleanup(job, false);
  await request('/result', {job: job.id, status, login_clicked: job.loginClicked === true,
    logged_in: loggedIn});
  await cleanup(job);
}
async function read(tab) {
  return chrome.tabs.sendMessage(tab.id, {action: 'state'}).catch(() => null);
}
async function act(tab, action) {
  if (action === 'github') return trustedGithubClick(tab);
  return chrome.tabs.sendMessage(tab.id, {action}).catch(() => ({clicked: false}));
}
async function trustedGithubClick(tab) {
  // Only the current task's Agent Router pages can receive mouse input.
  if (!currentJob || ![currentJob.tab, ...(currentJob.children || [])].includes(tab.id) ||
      !tab.url?.startsWith('https://agentrouter.org/')) return {clicked: false};
  const target = {tabId: tab.id};
  let attached = false;
  try {
    await chrome.debugger.attach(target, '1.3');
    attached = true;
    const point = await chrome.tabs.sendMessage(tab.id, {action: 'github_target'});
    if (!point?.found || !Number.isFinite(point.x) || !Number.isFinite(point.y) ||
        point.x < 0 || point.y < 0) return {clicked: false};
    await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent',
      {type: 'mouseMoved', x: point.x, y: point.y});
    await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent',
      {type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1});
    await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent',
      {type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1});
    return {clicked: true};
  } catch { return {clicked: false}; }
  finally { if (attached) await chrome.debugger.detach(target).catch(() => {}); }
}
async function tick() {
  if (ticking || !BRIDGE_SECRET) return;
  ticking = true;
  try {
    let job = (await chrome.storage.session.get(STORE))[STORE];
    currentJob = job;
    let response;
    try { response = await request('/job'); }
    catch {
      if (job && Date.now() > job.deadline) await cleanup(job);
      return;
    }
    if (job && job.id !== response.job) { await cleanup(job); job = null; }
    if (!response.job) return;
    if (job?.result) {
      await finish(job, job.result.status, job.result.loggedIn);
      return;
    }
    if (!job) {
      job = {id: response.job, stage: 'initial', children: [],
        deadline: Date.now() + Math.min(125, response.remaining_seconds - 5) * 1000};
      currentJob = job;
      await save(job);
      const tab = await chrome.tabs.create({url: 'https://agentrouter.org/console/token', active: true});
      job.tab = tab.id;
      await save(job);
      await progress(job);
    }
    if (Date.now() >= job.deadline) {
      await finish(job, job.loginClicked ? 'needs_login' : 'needs_attention');
      return;
    }
    const tabs = await owned(job);
    if (!tabs.length) { await finish(job, 'needs_attention'); return; }
    for (const tab of tabs) {
      const s = await read(tab);
      if (!s) continue; // GitHub/OAuth pages: no injection or permission clicks.
      if (job.loginClicked && s.logged_in && s.console_page) {
        await finish(job, 'success', true); return;
      }
      if (job.stage === 'initial') {
        if (s.logged_in) {
          const clicked = await act(tab, 'menu');
          if (clicked.clicked) job.stage = 'menu';
        } else if (s.login_available) job.stage = 'login';
      } else if (job.stage === 'menu' && s.logout_available) {
        const clicked = await act(tab, 'logout');
        if (clicked.clicked) job.stage = 'logout';
      } else if (job.stage === 'logout' && s.login_available && !s.logged_in) {
        job.stage = 'login';
      } else if (job.stage === 'login' && s.login_available) {
        // Record once before clicking, even when OAuth replaces the document immediately.
        job.loginClicked = true;
        job.stage = 'oauth';
        await save(job);
        const clicked = await act(tab, 'github');
        if (!clicked.clicked) { job.loginClicked = false; job.stage = 'login'; }
      }
      await save(job);
      await progress(job);
    }
  } catch {
    // No raw errors or URLs: OAuth URLs may contain authorization codes.
  } finally {
    ticking = false;
    // Idle extension wakes twice a minute; short polling is only for an active task.
    if (currentJob && !timer) timer = setInterval(tick, 2000);
    if (!currentJob && timer) { clearInterval(timer); timer = null; }
  }
}
chrome.tabs.onCreated.addListener(async tab => {
  // Share the same object with tick so later saves retain popup IDs.
  const job = currentJob;
  if (job && [job.tab, ...(job.children || [])].includes(tab.openerTabId)) {
    job.children = [...new Set([...(job.children || []), tab.id])];
    await save(job);
  }
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'agentrouter') start(); });
chrome.action.onClicked.addListener(start);
chrome.runtime.onInstalled.addListener(start);
chrome.runtime.onStartup.addListener(start);
function start() {
  chrome.alarms.create('agentrouter', {periodInMinutes: 0.5});
  tick();
}
start();
