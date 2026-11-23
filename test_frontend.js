const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function setup() {
  const timers = new Map();
  const listeners = new Map();
  const storage = new Map();
  const requests = [];
  let nextTimer = 1;
  const element = () => {
    const classes = new Set();
    return {
      style: {}, dataset: {}, children: [], attributes: {}, hidden: false,
      classList: {
        add: name => classes.add(name),
        remove: name => classes.delete(name),
        contains: name => classes.has(name),
        toggle(name, enabled = !classes.has(name)) {
          if (enabled) classes.add(name);
          else classes.delete(name);
          return enabled;
        }
      },
      appendChild(child) { this.children.push(child); },
      insertBefore() {},
      setAttribute(name, value) { this.attributes[name] = value; },
      querySelector(selector) { return this.children.find(child => '.' + child.className === selector); },
      addEventListener() {},
      remove() {}
    };
  };
  const elements = new Map([
    'tabs', 'terminal-area', 'empty-state', 'notice', 'sidebar-toggle',
    'group-pty', 'group-tmux', 'group-all', 'close-all-btn', 'new-tmux-btn', 'terminal-title'
  ].map(id => [id, element()]));
  elements.get('notice').hidden = true;
  const listen = target => (name, callback) => listeners.set(`${target}:${name}`, callback);
  class Terminal {
    constructor() {
      this.options = {};
      this.rows = 24;
      this.cols = 80;
      this.element = element();
      this.refreshes = [];
      this.stale = true;
    }
    open() {}
    loadAddon() {}
    onData(callback) { this.dataReceived = callback; }
    write(data, callback) {
      this.writes ||= [];
      this.writes.push({ data, callback });
    }
    onResize(callback) { this.resized = callback; }
    onSelectionChange() {}
    onWriteParsed(callback) { this.parsed = callback; }
    onTitleChange(callback) { this.titleChanged = callback; }
    focus() {}
    refresh(start, end) {
      this.refreshes.push([start, end]);
      this.stale = false;
    }
    dispose() { this.disposed = true; }
  }
  const context = vm.createContext({
    console,
    Terminal,
    FitAddon: { FitAddon: class { fit() {} } },
    SearchAddon: { SearchAddon: class {} },
    navigator: { language: 'zh' },
    localStorage: {
      getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value)
    },
    location: { search: '', pathname: '/', protocol: 'http:', host: 'localhost' },
    history: { replaceState() {} },
    URLSearchParams,
    WebSocket: class {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      constructor() {
        this.readyState = 0;
        this.messages = [];
        this.url = arguments[0];
      }
      send(message) { this.messages.push(JSON.parse(message)); }
      close() { this.readyState = 3; }
    },
    fetch: (url, options) => {
      requests.push([url, options]);
      return options?.method === 'DELETE'
        ? Promise.resolve({ ok: true, status: 204 }) : new Promise(() => {});
    },
    confirm: () => true,
    crypto: require('node:crypto').webcrypto,
    document: {
      hidden: false,
      body: element(),
      createElement: element,
      getElementById: id => elements.get(id) || null,
      querySelectorAll: () => [],
      addEventListener: listen('document')
    },
    window: {
      innerHeight: 800,
      matchMedia: () => ({ matches: false }),
      addEventListener: listen('window')
    },
    setTimeout(callback, delay) {
      const id = nextTimer++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout: id => timers.delete(id),
    setInterval() { return nextTimer++; },
    clearInterval: id => timers.delete(id)
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'static/app.js'), 'utf8'), context);
  const run = code => vm.runInContext(code, context);
  run("createTab('first'); activeTabId = 'first'; ensureTerm(tabs[0]);");
  const tab = run('tabs[0]');
  return {
    context, run, tab, timers, listeners, elements, storage, requests,
    flush() {
      const pending = [...timers.values()];
      timers.clear();
      pending.forEach(timer => timer.callback());
    }
  };
}

test('parsed output repairs a stale viewport without scrolling', () => {
  const app = setup();
  assert.equal(typeof app.tab.term.parsed, 'function');
  app.tab.term.parsed();
  assert.equal(app.tab.term.stale, true);
  app.flush();
  assert.equal(app.tab.term.stale, false);
  assert.deepEqual(app.tab.term.refreshes, [[0, 23]]);
});

test('continuous parsed output is coalesced without postponing the refresh', () => {
  const app = setup();
  app.tab.term.parsed();
  const firstTimer = [...app.timers.keys()][0];
  for (let i = 0; i < 100; i++) app.tab.term.parsed();
  assert.deepEqual([...app.timers.keys()], [firstTimer]);
  assert.equal(app.timers.get(firstTimer).delay, 100);
  app.flush();
  app.tab.term.parsed();
  app.flush();
  assert.equal(app.tab.term.refreshes.length, 2);
});

test('background tabs and hidden pages do not schedule output refreshes', () => {
  const app = setup();
  app.run("activeTabId = 'other'");
  app.tab.term.parsed();
  assert.equal(app.timers.size, 0);
  app.run("activeTabId = 'first'; document.hidden = true");
  app.tab.term.parsed();
  assert.equal(app.timers.size, 0);
});

test('a queued refresh rechecks visibility and uses the latest row count', () => {
  const app = setup();
  app.tab.term.parsed();
  app.run('document.hidden = true');
  app.flush();
  assert.equal(app.tab.term.refreshes.length, 0);
  app.run('document.hidden = false');
  app.tab.term.parsed();
  app.tab.term.rows = 33;
  app.flush();
  assert.deepEqual(app.tab.term.refreshes, [[0, 32]]);
});

test('switching back redraws even with unchanged dimensions and a closed socket', () => {
  const app = setup();
  app.tab.ws = { readyState: 3 };
  app.run("switchTab('first')");
  app.flush();
  assert.equal(app.tab.term.stale, false);
});

test('returning to a visible page and focusing the window redraw the active terminal', () => {
  const app = setup();
  app.run('document.hidden = true');
  app.listeners.get('document:visibilitychange')();
  assert.equal(app.timers.size, 0);
  app.run('document.hidden = false');
  app.listeners.get('document:visibilitychange')();
  app.flush();
  app.listeners.get('window:focus')();
  app.flush();
  assert.equal(app.tab.term.refreshes.length, 2);
});

test('fixed-size layout updates redraw without resizing the terminal', () => {
  const app = setup();
  app.run('currentSettings.autoFit = false; updateLayout()');
  app.flush();
  assert.deepEqual(app.tab.term.refreshes, [[0, 23]]);
});

test('closing a tab cancels its pending refresh', async () => {
  const app = setup();
  app.tab.term.parsed();
  const term = app.tab.term;
  await app.run("closeTab('first')");
  assert.equal(app.timers.size, 0);
  app.flush();
  assert.equal(term.disposed, true);
  assert.equal(term.refreshes.length, 0);
});

test('leaving the page cancels pending refreshes without disposing terminals', () => {
  const app = setup();
  app.tab.term.parsed();
  app.run('disconnectAllTabs()');
  assert.equal(app.timers.size, 0);
  assert.equal(app.tab.term.disposed, undefined);
});

test('opening a connection resynchronizes unchanged dimensions and requests a full TUI redraw', () => {
  const app = setup();
  app.run('currentSettings.keepaliveInterval = 0; reconnectTab(tabs[0])');
  app.tab.ws.readyState = 1;
  app.tab.ws.onopen();
  assert.deepEqual(app.tab.ws.messages, [
    { type: 'resize', cols: 80, rows: 24, redraw: true }
  ]);
});

test('a resize that fires before connection is retried on open with current dimensions', () => {
  const app = setup();
  app.run('currentSettings.keepaliveInterval = 0; reconnectTab(tabs[0])');
  app.tab.term.cols = 123;
  app.tab.term.rows = 37;
  app.tab.term.resized({ cols: 123, rows: 37 });
  app.flush();
  assert.equal(app.tab.ws.messages.length, 0);
  app.tab.ws.readyState = 1;
  app.tab.ws.onopen();
  assert.deepEqual(app.tab.ws.messages, [
    { type: 'resize', cols: 123, rows: 37, redraw: true }
  ]);
});

test('connection sync cancels an obsolete deferred size update', () => {
  const app = setup();
  app.run('currentSettings.keepaliveInterval = 0; reconnectTab(tabs[0])');
  app.tab.term.resized({ cols: 80, rows: 24 });
  app.tab.ws.readyState = 1;
  app.tab.ws.onopen();
  assert.equal(app.tab.ws.messages.length, 1);
  app.flush();
  assert.equal(app.tab.ws.messages.length, 1);
});

test('returning to a connected tab requests a full TUI redraw', () => {
  const app = setup();
  app.run('currentSettings.keepaliveInterval = 0; reconnectTab(tabs[0])');
  app.tab.ws.readyState = 1;
  app.run("activeTabId = 'other'; switchTab('first')");
  assert.deepEqual(app.tab.ws.messages, [
    { type: 'resize', cols: 80, rows: 24, redraw: true },
    { type: 'ping' }
  ]);
});

test('group filtering switches only the UI and retains other terminals', () => {
  const app = setup();
  app.run("createTab('persistent', false, 'tmux'); setGroup('tmux')");
  assert.equal(app.run('activeTabId'), 'persistent');
  assert.equal(app.tab.tabEl.hidden, true);
  assert.equal(app.tab.term.disposed, undefined);
  assert.equal(app.run('tabs[1].tabEl.hidden'), false);
  assert.equal(app.elements.get('group-tmux').attributes['aria-selected'], 'true');
  assert.equal(app.storage.get('webterm-group'), 'tmux');
  app.run("setGroup('all')");
  assert.equal(app.tab.tabEl.hidden, false);
  assert.equal(app.elements.get('group-all').textContent, 'All (2)');
});

test('an empty group clears the active terminal without closing sessions', () => {
  const app = setup();
  app.run("setGroup('tmux')");
  assert.equal(app.run('activeTabId'), null);
  assert.equal(app.run('tabs.length'), 1);
  assert.equal(app.tab.term.disposed, undefined);
  assert.equal(app.elements.get('empty-state').classList.contains('visible'), true);
});

test('tmux websocket URLs preserve the selected backend', () => {
  const app = setup();
  app.run("createTab('persistent', false, 'tmux'); switchTab('persistent')");
  assert.equal(new URL(app.run('tabs[1].ws.url')).searchParams.get('backend'), 'tmux');
});

test('new sessions respect group and backend availability', () => {
  const app = setup();
  app.run("setGroup('tmux'); newTerminal()");
  assert.equal(app.run('tabs.length'), 1);
  assert.equal(app.elements.get('notice').hidden, false);
  app.run("tmuxAvailable = true; newTerminal()");
  assert.equal(app.run('tabs.length'), 2);
  assert.equal(app.run('tabs[1].backend'), 'tmux');
});

test('closing a tmux tab requires confirmation', async () => {
  const app = setup();
  app.run("createTab('persistent', false, 'tmux')");
  app.context.confirm = () => false;
  await app.run("closeTab('persistent')");
  assert.equal(app.run('tabs.length'), 2);
  assert.equal(app.requests.filter(([, options]) => options?.method === 'DELETE').length, 0);
});

test('close group ends only sessions in the selected group', async () => {
  const app = setup();
  app.run("createTab('persistent', false, 'tmux'); setGroup('tmux')");
  await app.run('closeAllTabs()');
  assert.equal(app.run('tabs.length'), 1);
  assert.equal(app.run('tabs[0].backend'), 'pty');
  assert.equal(app.tab.term.disposed, undefined);
  assert.equal(app.requests.filter(([, options]) => options?.method === 'DELETE').length, 1);
});

test('failed session deletion preserves the terminal and reports an error', async () => {
  const app = setup();
  app.context.fetch = async () => ({ ok: false, status: 503 });
  await app.run("closeTab('first')");
  assert.equal(app.run('tabs.length'), 1);
  assert.equal(app.tab.term.disposed, undefined);
  assert.equal(app.elements.get('notice').hidden, false);
  assert.equal(app.tab.closing, false);
});

test('session refresh discovers persistent tmux sessions and enables their creation', async () => {
  const app = setup();
  app.context.fetch = async url => ({
    ok: true, status: 200,
    json: async () => url.includes('backends') ? { pty: true, tmux: true }
      : [{ id: 'persistent', backend: 'tmux' }]
  });
  await app.run('refreshSessions()');
  assert.equal(app.run('tabs[1].backend'), 'tmux');
  assert.equal(app.run('tabs[1].term'), null);
  assert.equal(app.elements.get('new-tmux-btn').disabled, false);
});

test('PTY initialization still works while an older server has not yet been restarted', async () => {
  const app = setup();
  app.context.fetch = async url => url.includes('backends') ? { ok: false, status: 404 } : {
    ok: true, status: 200, json: async () => [{ id: 'first' }]
  };
  await app.run('refreshSessions()');
  assert.equal(app.tab.backend, 'pty');
  assert.equal(app.elements.get('new-tmux-btn').disabled, true);
  assert.equal(app.elements.get('notice').hidden, true);
});

test('a detached tmux client can be reattached by selecting its tab', () => {
  const app = setup();
  app.run("createTab('persistent', false, 'tmux'); switchTab('persistent')");
  const old = app.run('tabs[1].ws');
  old.readyState = 3;
  app.run("switchTab('persistent')");
  assert.notEqual(app.run('tabs[1].ws'), old);
  assert.equal(app.run('tabs[1].ws.readyState'), 0);
});

test('real tmux names replace the toolbar title and are shown on sidebar tabs', () => {
  const app = setup();
  app.run("createTab('native', false, 'tmux', {name:'server_box', managed:false}); switchTab('native')");
  assert.equal(app.run('tabs[1].titleEl.textContent'), 'server_box');
  assert.equal(app.elements.get('terminal-title').textContent, 'server_box');
  assert.equal(app.context.document.title, 'server_box — Web Terminal');
  app.run("createTab('native', false, 'tmux', {name:'开发终端', managed:false})");
  assert.equal(app.run('tabs.length'), 2);
  assert.equal(app.elements.get('terminal-title').textContent, '开发终端');
});

test('PTY OSC titles update the toolbar but never override custom names', () => {
  const app = setup();
  app.tab.term.titleChanged('Claude Code');
  assert.equal(app.elements.get('terminal-title').textContent, 'Claude Code');
  app.run("applySessionMetadata(tabs[0], {name:'我的工作'})");
  app.tab.term.titleChanged('different terminal title');
  assert.equal(app.elements.get('terminal-title').textContent, '我的工作');
});

test('tmux session names take priority over terminal OSC titles', () => {
  const app = setup();
  app.run("createTab('native', false, 'tmux', {name:'server_box', managed:false}); switchTab('native')");
  app.run("tabs[1].term.titleChanged('shell@host')");
  assert.equal(app.elements.get('terminal-title').textContent, 'server_box');
});

test('closing an external tmux tab only requests detach and does not ask to kill it', async () => {
  const app = setup();
  app.run("createTab('native', false, 'tmux', {name:'server_box', managed:false})");
  app.context.confirm = () => { throw new Error('external session must not offer a destructive close'); };
  await app.run("closeTab('native')");
  assert.equal(app.run('tabs.length'), 1);
});

test('rename saves to the server rather than browser-local storage', async () => {
  const app = setup();
  let request;
  app.context.fetch = async (url, options) => {
    request = { url, options };
    return { ok: true, json: async () => ({ id: 'first', name: '共享名称' }) };
  };
  await app.run("renameTab(tabs[0], '共享名称')");
  assert.equal(request.options.method, 'PATCH');
  assert.equal(JSON.parse(request.options.body).name, '共享名称');
  assert.equal(app.elements.get('terminal-title').textContent, '共享名称');
  assert.equal(app.storage.has('webterm-titles'), false);
});

test('rename broadcasts update the matching tab even through another tab socket', () => {
  const app = setup();
  app.run("createTab('second'); reconnectTab(tabs[0])");
  app.tab.ws.onmessage({ data: JSON.stringify({
    type: 'session_info', id: 'second', name: '另一台设备的名称', managed: true
  }) });
  assert.equal(app.run('tabs[1].title'), '另一台设备的名称');
  assert.notEqual(app.tab.title, '另一台设备的名称');
});

test('failed rename keeps the previous shared name', async () => {
  const app = setup();
  app.run("applySessionMetadata(tabs[0], {name:'original'})");
  app.context.fetch = async () => ({ ok: false, status: 409 });
  await assert.rejects(app.run("renameTab(tabs[0], 'duplicate')"));
  assert.equal(app.tab.title, 'original');
});

test('input is chunked without splitting Unicode characters', () => {
  const app = setup();
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1");
  app.context.largeInput = 'a'.repeat(16383) + '😀中文'.repeat(10000);
  app.run("sendTerminalInput(tabs[0], largeInput)");
  const messages = app.tab.ws.messages;
  assert.equal(messages.map(message => message.data).join(''), app.context.largeInput);
  assert.ok(messages.every(message => Buffer.byteLength(message.data) <= 65536));
  assert.ok(messages.every(message => !/[\uD800-\uDBFF]$/.test(message.data)));
});

test('overlarge paste is rejected before sending any part', () => {
  const app = setup();
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1; sendTerminalInput(tabs[0], 'x'.repeat(262145))");
  assert.equal(app.tab.ws.messages.length, 0);
  assert.equal(app.elements.get('notice').hidden, false);
});

test('request errors are visible without disconnecting the terminal', () => {
  const app = setup();
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1");
  app.tab.ws.onmessage({ data: JSON.stringify({ type: 'request_error', message: 'input congested' }) });
  assert.equal(app.elements.get('notice').textContent, 'input congested');
  assert.equal(app.tab.ws.readyState, 1);
  assert.equal(app.tab.intentionalClose, false);
});

test('replayed queries are suppressed at parser time, not WebSocket receipt time', () => {
  const app = setup();
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1");
  const ws = app.tab.ws;
  ws.onmessage({ data: JSON.stringify({ type: 'replay_begin' }) });
  ws.onmessage({ data: new Uint8Array([27, 91, 99]).buffer });
  ws.onmessage({ data: JSON.stringify({ type: 'replay_end' }) });
  assert.equal(app.tab.receivingReplay, false);
  assert.equal(app.tab.parsingReplay, undefined);
  app.tab.term.writes[0].callback();
  for (const reply of ['\x1b[?1;2c', '\x1b[>0;276;0c', '\x1b[5;7R', '\x1b[8;24;80t']) {
    app.tab.term.dataReceived(reply);
  }
  assert.equal(app.tab.ws.messages.length, 0);
  app.tab.term.dataReceived('normal typing');
  assert.equal(app.tab.ws.messages[0].data, 'normal typing');
});

test('live device queries still get replies after the replay finishes', () => {
  const app = setup();
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1");
  const ws = app.tab.ws;
  ws.onmessage({ data: JSON.stringify({ type: 'replay_begin' }) });
  ws.onmessage({ data: new Uint8Array([27, 91, 99]).buffer });
  ws.onmessage({ data: JSON.stringify({ type: 'replay_end' }) });
  ws.onmessage({ data: new Uint8Array([27, 91, 99]).buffer });
  const markers = app.tab.term.writes.filter(write => write.callback);
  markers[0].callback();
  app.tab.term.dataReceived('\x1b[?1;2c');
  markers[1].callback();
  app.run('altArmed = true');
  app.tab.term.dataReceived('\x1b[>0;276;0c');
  assert.equal(ws.messages.length, 1);
  assert.equal(ws.messages[0].data, '\x1b[>0;276;0c');
  assert.equal(app.run('altArmed'), true);
});

test('queued output from an old connection cannot inject replies into the new one', () => {
  const app = setup();
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1");
  app.tab.ws.onmessage({ data: new Uint8Array([27, 91, 99]).buffer });
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1");
  app.tab.term.writes[0].callback();
  app.tab.term.dataReceived('\x1b[?1;2c');
  assert.equal(app.tab.ws.messages.length, 0);
});

test('ordinary text resembling a device report is never filtered', () => {
  const app = setup();
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1; tabs[0].parsingReplay = true");
  app.tab.term.dataReceived('1;2c0;276;0c');
  app.tab.term.dataReceived('\x1b[A');
  assert.equal(app.tab.ws.messages[0].data, '1;2c0;276;0c');
  assert.equal(app.tab.ws.messages[1].data, '\x1b[A');
});

test('upstream session readiness and legacy metadata both recover connection state', () => {
  const app = setup();
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1");
  app.tab.ws.onmessage({ data: JSON.stringify({ type: 'session_ready' }) });
  assert.equal(app.tab.state, 'connected');
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1");
  assert.equal(app.tab.state, 'reconnecting');
  app.tab.ws.onmessage({ data: JSON.stringify({ type: 'session_info', id: 'first', name: 'shared' }) });
  assert.equal(app.tab.state, 'connected');
  assert.equal(app.tab.title, 'shared');
});

test('upstream combined modifiers still use the bounded input path', () => {
  const app = setup();
  app.run("reconnectTab(tabs[0]); tabs[0].ws.readyState = 1; ctrlArmed = true; altArmed = true");
  app.run("sendTerminalInput(tabs[0], '\\x1b[D')");
  assert.equal(app.tab.ws.messages[0].data, '\x1b[1;7D');
  assert.equal(app.run('ctrlArmed || altArmed'), false);
});
