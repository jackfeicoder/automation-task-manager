const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../../tasks/agentrouter/chrome-extension/background.js'), 'utf8');

function fixture() {
  let state = {logged_in: true, console_page: true};
  let store = {};
  let nextId = 10;
  let createdHandler;
  let available = true;
  let command = {job: 'fixture-job', remaining_seconds: 160};
  let failResultOnce = false;
  let targetTab;
  const attached = [];
  const detached = [];
  const tabs = new Map([[1, {id: 1, user: true}]]);
  const removed = [];
  const actions = [];
  const results = [];
  const context = vm.createContext({
    console, AbortSignal,
    importScripts: () => {}, BRIDGE_SECRET: 'fixture-pairing-only',
    setInterval: () => 1, clearInterval: () => {},
    fetch: async (url, options) => {
      if (!available) throw new Error('offline');
      if (url.endsWith('/result')) {
        if (failResultOnce) {failResultOnce = false; throw new Error('lost response');}
        results.push(JSON.parse(options.body));
      }
      return {ok: true, json: async () => url.endsWith('/job') ? command : {accepted: true}};
    },
    chrome: {
      storage: {session: {
        get: async () => structuredClone(store),
        set: async data => {store = {...store, ...structuredClone(data)};},
        remove: async key => {delete store[key];}
      }},
      tabs: {
        create: async () => { const tab = {id: nextId++, url: 'https://agentrouter.org/console/token'}; tabs.set(tab.id, tab); return tab; },
        get: async id => {if (!tabs.has(id)) throw new Error('missing'); return tabs.get(id);},
        remove: async ids => {for (const id of ids) {removed.push(id); tabs.delete(id);}},
        sendMessage: async (id, message) => {
          if (id === 1) throw new Error('user tab touched');
          if (message.action === 'state') return structuredClone(state);
          actions.push(message.action);
          if (message.action === 'menu') state.logout_available = true;
          if (message.action === 'logout') state = {login_available: true, logged_in: false};
          if (message.action === 'github_target') {targetTab = id; return {found: true, x: 40, y: 80};}
          return {clicked: true};
        },
        onCreated: {addListener: fn => {createdHandler = fn;}}
      },
      debugger: {
        attach: async target => {assert.notEqual(target.tabId, 1); attached.push(target.tabId);},
        detach: async target => {detached.push(target.tabId);},
        sendCommand: async (target, command, event) => {
          assert.equal(command, 'Input.dispatchMouseEvent');
          assert.equal(target.tabId, targetTab);
          if (event.type === 'mouseReleased') {
            actions.push('github');
            const popup = {id: nextId++, openerTabId: target.tabId, url: 'https://agentrouter.org/console/token'};
            tabs.set(popup.id, popup); await createdHandler(popup);
            state = {logged_in: true, console_page: true};
          }
        }
      },
      alarms: {create: () => {}, onAlarm: {addListener: () => {}}},
      action: {onClicked: {addListener: () => {}}},
      runtime: {onInstalled: {addListener: () => {}}, onStartup: {addListener: () => {}}}
    }
  });
  // Suppress automatic start to drive each state transition deterministically.
  vm.runInContext(source.replace(/start\(\);\s*$/, ''), context);
  return {tabs, removed, actions, results, attached, detached,
    tick: () => vm.runInContext('tick()', context),
    setState: value => {state = value;},
    disconnect: () => {available = false;},
    expire: () => {store.agentrouter_job.deadline = 0;},
    cancel: () => {command = {job: null};},
    loseResult: () => {failResultOnce = true;},
    job: () => store.agentrouter_job};
}

test('logout, GitHub popup return, and cleanup of owned tabs only', async () => {
  const f = fixture();
  for (let i = 0; i < 7 && !f.results.length; i++) await f.tick();
  assert.deepEqual(f.actions, ['menu', 'logout', 'github_target', 'github']);
  assert.equal(f.results[0].status, 'success');
  assert.equal(f.results[0].login_clicked, true);
  assert.equal(f.results[0].logged_in, true);
  assert.deepEqual([...f.tabs.keys()], [1]);
  assert.ok(f.removed.length === 2);
  assert.equal(f.job(), undefined);
  assert.deepEqual(f.attached, f.detached);
});

test('an expired session starts directly with GitHub login', async () => {
  const f = fixture(); f.setState({login_available: true, logged_in: false});
  for (let i = 0; i < 5 && !f.results.length; i++) await f.tick();
  assert.deepEqual(f.actions, ['github_target', 'github']);
  assert.equal(f.results[0].status, 'success');
});

test('an existing logged-in page alone never reports success', async () => {
  const f = fixture(); await f.tick();
  assert.equal(f.results.length, 0);
  f.expire(); await f.tick();
  assert.equal(f.results[0].status, 'needs_attention');
  assert.deepEqual([...f.tabs.keys()], [1]);
});

test('cancelled worker closes its pages without result or new login', async () => {
  const f = fixture(); await f.tick(); f.cancel(); await f.tick();
  assert.equal(f.results.length, 0);
  assert.deepEqual([...f.tabs.keys()], [1]);
});

test('worker disconnection eventually releases browser pages', async () => {
  const f = fixture(); await f.tick(); f.disconnect(); f.expire(); await f.tick();
  assert.deepEqual([...f.tabs.keys()], [1]);
  assert.equal(f.job(), undefined);
});

test('lost result response retries the result without repeating logout', async () => {
  const f = fixture(); f.loseResult();
  for (let i = 0; i < 9 && !f.results.length; i++) await f.tick();
  assert.equal(f.results[0].status, 'success');
  assert.equal(f.actions.filter(a => a === 'logout').length, 1);
  assert.equal(f.actions.filter(a => a === 'github').length, 1);
});

test('visible account menu hover exposes logout without reading token controls', () => {
  let listener;
  let open = false;
  let loggedIn = true;
  const element = (text, active, click, event = () => {}) => ({
    innerText: text, getClientRects: () => active() ? [{}] : [],
    click, dispatchEvent: event, scrollIntoView: () => {},
    getBoundingClientRect: () => ({x: 10, y: 20, width: 80, height: 40})
  });
  const account = element('G\ngithub_fixture', () => loggedIn, () => {}, e => {
    if (e.type === 'mouseover') open = true;
  });
  const logout = element('退出', () => open, () => {loggedIn = false; open = false;});
  const login = element('使用 GitHub 继续', () => !loggedIn, () => {loggedIn = true;});
  const header = {querySelectorAll: () => [account]};
  const context = vm.createContext({
    document: {
      querySelector: selector => selector === 'header' ? header : null,
      querySelectorAll: selector => {
        if (selector === 'button') return [login];
        if (selector === '[role="menuitem"],.semi-dropdown-item') return [logout];
        throw new Error('Unexpected DOM access');
      }
    },
    getComputedStyle: () => ({visibility: 'visible'}),
    MouseEvent: class {constructor(type) {this.type = type;}},
    location: {pathname: '/console/token'},
    chrome: {runtime: {id: 'fixture-extension', onMessage: {addListener: fn => {listener = fn;}}}}
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../../tasks/agentrouter/chrome-extension/content.js'), 'utf8'), context);
  function send(action, sender = 'fixture-extension') {
    let response; listener({action}, {id: sender}, r => {response = r;}); return response;
  }
  assert.equal(send('state').logout_available, false);
  assert.equal(send('menu').clicked, true);
  assert.equal(send('state').logout_available, true);
  assert.equal(send('logout').clicked, true);
  assert.equal(send('state').login_available, true);
  assert.equal(send('github_target').found, true);
  assert.equal(send('state').logged_in, false);
  assert.equal(send('logout', 'other-extension'), undefined);
});
