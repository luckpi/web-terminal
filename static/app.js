const tabsEl = document.getElementById('tabs');
const terminalArea = document.getElementById('terminal-area');
const tabs = [];
let activeTabId = null;
let ctrlArmed = false;
let altArmed = false;
let shiftArmed = false;
let ctrlBtn = null;
let altBtn = null;
let shiftBtn = null;

const i18n = {
  zh: {
    newTerminal: '新建终端',
    closeTerminal: '关闭终端',
    closeAll: '关闭所有',
    settings: '设置',
    terminalSettings: '终端设置',
    general: '常规',
    language: '语言',
    theme: '主题',
    themePreset: '主题预设',
    background: '背景色',
    foreground: '前景色',
    cursor: '光标颜色',
    cursorBlink: '光标闪烁',
    cursorStyle: '光标样式',
    block: '块状',
    bar: '竖线',
    underline: '下划线',
    font: '字体',
    fontSize: '字体大小',
    fontFamily: '字体',
    fontWeight: '字体粗细',
    lineHeight: '行高',
    letterSpacing: '字间距',
    tabWidth: 'Tab 宽度',
    scrollback: '滚动缓冲',
    input: '输入',
    copyOnSelect: '选中即复制',
    rightClickPaste: '右键粘贴',
    pasteHint: '右键粘贴需要 HTTPS 或浏览器剪贴板权限',
    connection: '连接',
    autoReconnect: '自动重连',
    reconnectInterval: '重连间隔 (ms)',
    keepaliveInterval: '保活间隔 (ms)',
    session: '新会话',
    sessionTerm: '终端类型',
    sessionColorterm: '彩色模式',
    autoFit: '自动适应窗口',
    termCols: '列数',
    termRows: '行数',
    sessionHint: '只影响新创建的会话',
    apply: '应用',
    cancel: '取消',
    reset: '重置',
    custom: '自定义',
    close: '关闭终端',
    renameTerminal: '重命名终端',
    search: '搜索'
  },
  en: {
    newTerminal: 'New Terminal',
    closeTerminal: 'Close Terminal',
    closeAll: 'Close All',
    settings: 'Settings',
    terminalSettings: 'Terminal Settings',
    general: 'General',
    language: 'Language',
    theme: 'Theme',
    themePreset: 'Theme preset',
    background: 'Background',
    foreground: 'Foreground',
    cursor: 'Cursor',
    cursorBlink: 'Cursor blink',
    cursorStyle: 'Cursor style',
    block: 'Block',
    bar: 'Bar',
    underline: 'Underline',
    font: 'Font',
    fontSize: 'Font size',
    fontFamily: 'Font family',
    fontWeight: 'Font weight',
    lineHeight: 'Line height',
    letterSpacing: 'Letter spacing',
    tabWidth: 'Tab width',
    scrollback: 'Scrollback',
    input: 'Input',
    copyOnSelect: 'Copy on select',
    rightClickPaste: 'Right-click paste',
    pasteHint: 'Right-click paste requires HTTPS or clipboard permission',
    connection: 'Connection',
    autoReconnect: 'Auto reconnect',
    reconnectInterval: 'Reconnect interval (ms)',
    keepaliveInterval: 'Keepalive interval (ms)',
    session: 'New session',
    sessionTerm: 'Terminal type',
    sessionColorterm: 'Color mode',
    autoFit: 'Auto fit to window',
    termCols: 'Columns',
    termRows: 'Rows',
    sessionHint: 'Only affects newly created sessions',
    apply: 'Apply',
    cancel: 'Cancel',
    reset: 'Reset',
    custom: 'Custom',
    close: 'Close terminal',
    renameTerminal: 'Rename terminal',
    search: 'Search'
  }
};

function t(key) {
  const lang = currentSettings.language || 'zh';
  if (i18n[lang] && i18n[lang][key]) return i18n[lang][key];
  if (i18n.en && i18n.en[key]) return i18n.en[key];
  return key;
}

function updateI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  const settingsBtn = document.getElementById('settings-btn');
  if (settingsBtn) settingsBtn.textContent = t('settings');
  if (plusEl) plusEl.title = t('newTerminal');
  tabs.forEach(tab => {
    const close = tab.tabEl.querySelector('.close');
    if (close) close.title = t('close');
  });
}

const themePresets = {
  Default: { background: '#1e1e1e', foreground: '#d4d4d4', cursor: '#d4d4d4', selectionBackground: '#264f78' },
  Dracula: { background: '#282a36', foreground: '#f8f8f2', cursor: '#f8f8f2', selectionBackground: '#44475a' },
  'Solarized Dark': { background: '#002b36', foreground: '#839496', cursor: '#93a1a1', selectionBackground: '#073642' },
  'Solarized Light': { background: '#fdf6e3', foreground: '#657b83', cursor: '#586e75', selectionBackground: '#eee8d5' },
  'One Dark': { background: '#282c34', foreground: '#abb2bf', cursor: '#abb2bf', selectionBackground: '#3e4451' },
  Monokai: { background: '#272822', foreground: '#f8f8f2', cursor: '#f8f8f2', selectionBackground: '#49483e' }
};

const defaultLanguage = (navigator.language || 'zh').toLowerCase().startsWith('zh') ? 'zh' : 'en';

const defaultSettings = {
  language: defaultLanguage,
  theme: 'Default',
  fontSize: 14,
  fontFamily: 'Menlo, Monaco, "Courier New", monospace, Consolas, "Liberation Mono"',
  fontWeight: 'normal',
  lineHeight: 1,
  letterSpacing: 0,
  tabStopWidth: 8,
  background: '#1e1e1e',
  foreground: '#d4d4d4',
  cursor: '#d4d4d4',
  selectionBackground: '#264f78',
  cursorStyle: 'block',
  cursorBlink: true,
  scrollback: 10000,
  copyOnSelect: true,
  rightClickPaste: false,
  autoReconnect: true,
  reconnectInterval: 3000,
  keepaliveInterval: 30000,
  term: 'xterm-256color',
  colorterm: 'truecolor',
  autoFit: true,
  termCols: 80,
  termRows: 24
};

function isTouchMobile() {
  return window.matchMedia('(pointer: coarse)').matches;
}

function getSettingsKey() {
  return isTouchMobile() ? 'webterm-settings-mobile' : 'webterm-settings';
}

const mobileDefaults = {
  fontSize: 13,
  scrollback: 5000
};

function loadSettings() {
  const base = isTouchMobile() ? { ...defaultSettings, ...mobileDefaults } : { ...defaultSettings };
  try {
    const saved = JSON.parse(localStorage.getItem(getSettingsKey()));
    if (saved) return { ...base, ...saved };
  } catch (e) {}
  return base;
}

let currentSettings = loadSettings();

function saveSettings() {
  localStorage.setItem(getSettingsKey(), JSON.stringify(currentSettings));
}

const titlesKey = 'webterm-titles';
function loadSavedTitles() {
  try {
    return JSON.parse(localStorage.getItem(titlesKey)) || {};
  } catch (e) {}
  return {};
}
let savedTitles = loadSavedTitles();
function saveSavedTitles() {
  localStorage.setItem(titlesKey, JSON.stringify(savedTitles));
}

function getTabTitle(sessionId) {
  const saved = savedTitles[sessionId];
  return (saved && saved.trim()) ? saved.trim() : sessionId.slice(0, 8);
}

function getThemeObject() {
  return {
    background: currentSettings.background,
    foreground: currentSettings.foreground,
    cursor: currentSettings.cursor,
    selectionBackground: currentSettings.selectionBackground
  };
}

function getTerminalOptions() {
  const cols = parseInt(currentSettings.termCols, 10) || 80;
  const rows = parseInt(currentSettings.termRows, 10) || 24;
  const opts = {
    fontSize: currentSettings.fontSize,
    fontFamily: currentSettings.fontFamily,
    fontWeight: currentSettings.fontWeight,
    lineHeight: currentSettings.lineHeight,
    letterSpacing: currentSettings.letterSpacing,
    tabStopWidth: currentSettings.tabStopWidth,
    theme: getThemeObject(),
    scrollback: currentSettings.scrollback
  };
  if (!currentSettings.autoFit) {
    if (cols > 0) opts.cols = cols;
    if (rows > 0) opts.rows = rows;
  }
  return opts;
}

function applyTerminalSettings() {
  tabs.forEach(t => {
    if (!t.term) return;  // lazily-created tabs have no terminal yet
    const opts = t.term.options;
    opts.cursorBlink = currentSettings.cursorBlink;
    opts.fontSize = currentSettings.fontSize;
    opts.fontFamily = currentSettings.fontFamily;
    opts.fontWeight = currentSettings.fontWeight;
    opts.lineHeight = currentSettings.lineHeight;
    opts.letterSpacing = currentSettings.letterSpacing;
    opts.tabStopWidth = currentSettings.tabStopWidth;
    opts.theme = getThemeObject();
    opts.cursorStyle = currentSettings.cursorStyle;
    opts.scrollback = currentSettings.scrollback;
    if (t.id === activeTabId) applyTabSize(t);
    try { t.term.refresh(0, t.term.rows - 1); } catch (e) {}
  });
  const active = tabs.find(t => t.id === activeTabId);
  if (active && active.term) {
    active.term.focus();
  }
}

function applyTabSize(tab) {
  if (!tab || !tab.term) return;
  if (currentSettings.autoFit) {
    try { tab.fitAddon.fit(); } catch (e) {}
  } else {
    const cols = parseInt(currentSettings.termCols, 10) || 80;
    const rows = parseInt(currentSettings.termRows, 10) || 24;
    if (tab.term.cols === cols && tab.term.rows === rows) return;
    try { tab.term.resize(cols, rows); } catch (e) {}
  }
}

function setupKeepalive(tab) {
  if (tab.keepaliveTimer) clearInterval(tab.keepaliveTimer);
  tab.keepaliveTimer = null;
  if (currentSettings.keepaliveInterval > 0) {
    tab.keepaliveTimer = setInterval(() => {
      if (tab.ws && tab.ws.readyState === WebSocket.OPEN) {
        tab.ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, currentSettings.keepaliveInterval);
  }
}

function applyConnectionSettings() {
  tabs.forEach(t => setupKeepalive(t));
}

function copyToClipboard(text) {
  if (!text) return;
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).catch(err => console.warn('copy failed', err));
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    ta.setAttribute('readonly', '');
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand('copy');
    } catch (e) {}
    document.body.removeChild(ta);
  }
}

async function pasteFromClipboard() {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        const tab = tabs.find(t => t.id === activeTabId);
        if (tab && tab.ws && tab.ws.readyState === WebSocket.OPEN) {
          tab.ws.send(JSON.stringify({ type: 'input', data: text }));
        }
      }
    } catch (e) {
      console.warn('paste failed', e);
    }
  } else {
    console.warn('right-click paste requires HTTPS or clipboard permission');
  }
}

const plusEl = document.createElement('div');
plusEl.className = 'tab-add';
plusEl.title = t('newTerminal');
plusEl.textContent = '+';
plusEl.onclick = (e) => {
  e.stopPropagation();
  newTerminal();
};
tabsEl.appendChild(plusEl);

function genSessionId() {
  const b = new Uint8Array(12);
  crypto.getRandomValues(b);
  return Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
}

function getQuerySession() {
  const m = /[?&]session=([^&]+)/.exec(location.search);
  return m ? decodeURIComponent(m[1]) : null;
}

function getToken() {
  const m = /[?&]token=([^&]+)/.exec(location.search);
  return m ? decodeURIComponent(m[1]) : null;
}

function appendToken(url) {
  const token = getToken();
  if (!token) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function getWsUrl(sessionId) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const term = encodeURIComponent(currentSettings.term || 'xterm-256color');
  const colorterm = currentSettings.colorterm ? encodeURIComponent(currentSettings.colorterm) : '';
  const rows = parseInt(currentSettings.termRows, 10) || 24;
  const cols = parseInt(currentSettings.termCols, 10) || 80;
  const url = `${proto}//${location.host}/ws?session=${encodeURIComponent(sessionId)}&term=${term}&colorterm=${colorterm}&rows=${rows}&cols=${cols}`;
  return appendToken(url);
}

function updateUrl(sessionId) {
  if (!sessionId) return;
  const params = new URLSearchParams(location.search);
  params.set('session', sessionId);
  history.replaceState({}, '', '?' + params.toString());
}

function clearSessionFromUrl() {
  const params = new URLSearchParams(location.search);
  params.delete('session');
  const qs = params.toString();
  history.replaceState({}, '', qs ? '?' + qs : location.pathname);
}

function newTerminal() {
  const sid = genSessionId();
  createTab(sid, true);
}

function updateEmptyState() {
  const empty = document.getElementById('empty-state');
  if (!empty) return;
  empty.classList.toggle('visible', tabs.length === 0);
}

function reconnectTab(tab) {
  if (tab.reconnectTimer) {
    clearTimeout(tab.reconnectTimer);
    tab.reconnectTimer = null;
  }
  if (tab.keepaliveTimer) {
    clearInterval(tab.keepaliveTimer);
    tab.keepaliveTimer = null;
  }
  if (tab.ws) {
    try { tab.ws.onclose = null; } catch (e) {}
    try { tab.ws.onerror = null; } catch (e) {}
    try { tab.ws.onopen = null; } catch (e) {}
    try { tab.ws.onmessage = null; } catch (e) {}
    try { tab.ws.close(); } catch (e) {}
  }
  const ws = new WebSocket(getWsUrl(tab.id));
  ws.binaryType = 'arraybuffer';
  tab.ws = ws;
  tab.initialSync = true;
  tab.intentionalClose = false;

  ws.onopen = () => {
    if (tab.intentionalClose) {
      if (tab.ws && tab.ws.readyState <= WebSocket.OPEN) tab.ws.close();
      return;
    }
    if (tab.tabEl && tab.tabEl.isConnected) tab.tabEl.style.opacity = '1';
    if (tab.id === activeTabId) {
      ensureTerm(tab);
      applyTabSize(tab);
      tab.term.focus();
    }
  };

  ws.onmessage = (e) => {
    if (typeof e.data === 'string') {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'session_closed' || msg.type === 'error') {
          tab.intentionalClose = true;
          if (tab.ws && (tab.ws.readyState === WebSocket.OPEN || tab.ws.readyState === WebSocket.CONNECTING)) {
            tab.ws.close();
          }
          // Expired/invalid auth: go back to the login page.
          if (msg.type === 'error' && /token/i.test(msg.message || '')) {
            location.href = '/login';
          }
        }
      } catch (err) {}
      return;
    }
    if (!tab.term) return;
    if (tab.initialSync) {
      // In-band RIS (Reset to Initial State) instead of term.reset().
      // This clears the buffer and resets the parser synchronously with
      // the incoming data, avoiding the race that can display xterm
      // device-attributes replies like "1;2c" / "1R" as plain text.
      tab.term.write('\x1bc');
      tab.initialSync = false;
    }
    if (tab.cancelInertia) tab.cancelInertia();
    tab.term.write(new Uint8Array(e.data));
  };

  ws.onclose = () => {
    if (tab.tabEl && tab.tabEl.isConnected) tab.tabEl.style.opacity = '0.6';
    if (tab.intentionalClose) return;
    if (tab.reconnectTimer) clearTimeout(tab.reconnectTimer);
    if (currentSettings.autoReconnect && currentSettings.reconnectInterval > 0) {
      tab.reconnectTimer = setTimeout(() => reconnectTab(tab), currentSettings.reconnectInterval);
    }
  };

  ws.onerror = () => {
    if (tab.tabEl && tab.tabEl.isConnected) tab.tabEl.style.opacity = '0.6';
  };

  setupKeepalive(tab);
}

function getRowHeight(host, terminal) {
  if (terminal.rows > 0 && host.clientHeight > 0) return host.clientHeight / terminal.rows;
  const canvas = host.querySelector('.xterm-screen canvas');
  if (canvas && terminal.rows > 0) return canvas.clientHeight / terminal.rows;
  return 20;
}

function setupTouchScroll(host, terminal, sessionId, tabObj) {
  if (!isTouchMobile()) return null;
  let startX = 0, startY = 0, startTime = 0;
  let lastX = 0, lastY = 0, lastMoveTime = 0;
  let scrollActive = false, swipeActive = false, ignore = false;
  let remainder = 0, rowHeight = 0;
  let velocity = 0, inertiaRaf = 0;
  let startViewportHeight = 0;
  const TAKEOVER_PX = 16;
  const SWIPE_THRESHOLD = 60;
  const TAP_TIME = 300;
  const TAP_DIST = 12;
  const FLING_MIN_V = 0.12;
  const STOP_V = 0.02;
  const FRICTION = 0.94;

  function scrollByPx(px) {
    const total = remainder + px;
    if (rowHeight <= 0) return;
    const lines = Math.trunc(total / rowHeight);
    remainder = total - lines * rowHeight;
    if (lines !== 0) terminal.scrollLines(-lines);
  }

  function cancelInertia() {
    if (inertiaRaf) {
      cancelAnimationFrame(inertiaRaf);
      inertiaRaf = 0;
    }
  }
  if (tabObj) tabObj.cancelInertia = cancelInertia;

  function inertiaStep(now) {
    const dt = Math.max(now - lastMoveTime, 1);
    lastMoveTime = now;
    velocity *= Math.pow(FRICTION, dt / 16.6667);
    if (Math.abs(velocity) < STOP_V) { inertiaRaf = 0; return; }
    scrollByPx(velocity * dt);
    inertiaRaf = requestAnimationFrame(inertiaStep);
  }

  function onTouchStart(e) {
    cancelInertia();
    scrollActive = false;
    swipeActive = false;
    ignore = e.touches.length !== 1;
    if (ignore) return;
    const t = e.touches[0];
    startX = lastX = t.clientX;
    startY = lastY = t.clientY;
    startTime = lastMoveTime = performance.now();
    startViewportHeight = window.visualViewport ? window.visualViewport.height : window.innerHeight;
    velocity = 0;
    remainder = 0;
  }

  function onTouchMove(e) {
    if (ignore) return;
    if (e.touches.length !== 1) { ignore = true; scrollActive = false; swipeActive = false; return; }
    const t = e.touches[0];
    const currentHeight = window.visualViewport ? window.visualViewport.height : window.innerHeight;
    if (currentHeight !== startViewportHeight) {
      // visualViewport changed mid-gesture (e.g. keyboard popped up);
      // reset the coordinate baseline to avoid a fake scroll jump.
      startViewportHeight = currentHeight;
      lastX = t.clientX;
      lastY = t.clientY;
      return;
    }
    const dx = t.clientX - startX;
    const dy = t.clientY - startY;
    const adx = Math.abs(dx);
    const ady = Math.abs(dy);

    if (!scrollActive && !swipeActive) {
      if (Math.max(adx, ady) <= TAKEOVER_PX) return;
      if (ady > adx) {
        scrollActive = true;
        rowHeight = getRowHeight(host, terminal);
      } else {
        swipeActive = true;
      }
    }

    if (scrollActive) {
      e.preventDefault();
      const now = performance.now();
      const moveDy = t.clientY - lastY;
      const dt = Math.max(now - lastMoveTime, 1);
      velocity = velocity * 0.2 + (moveDy / dt) * 0.8;
      lastMoveTime = now;
      lastY = t.clientY;
      scrollByPx(moveDy);
    } else if (swipeActive) {
      e.preventDefault();
    }
  }

  function onTouchEnd(e) {
    if (ignore) return;
    const now = performance.now();
    const t = e.changedTouches[0];
    const dx = t.clientX - startX;
    const dy = t.clientY - startY;
    if (swipeActive) {
      swipeActive = false;
      if (Math.abs(dx) > SWIPE_THRESHOLD) {
        const idx = tabs.findIndex(tt => tt.id === sessionId);
        if (idx !== -1) {
          if (dx < 0 && idx < tabs.length - 1) switchTab(tabs[idx + 1].id);
          else if (dx > 0 && idx > 0) switchTab(tabs[idx - 1].id);
        }
      }
      return;
    }
    if (scrollActive) {
      scrollActive = false;
      if (e.touches.length > 0) return;
      if (now - lastMoveTime > 60) return;
      if (Math.abs(velocity) < FLING_MIN_V) return;
      lastMoveTime = performance.now();
      inertiaRaf = requestAnimationFrame(inertiaStep);
      return;
    }
    if (now - startTime < TAP_TIME && Math.max(Math.abs(dx), Math.abs(dy)) < TAP_DIST) {
      try { terminal.focus(); } catch (err) {}
    }
  }

  function onTouchCancel() {
    scrollActive = false;
    swipeActive = false;
    cancelInertia();
  }

  host.addEventListener('touchstart', onTouchStart, { passive: true });
  host.addEventListener('touchmove', onTouchMove, { passive: false });
  host.addEventListener('touchend', onTouchEnd, { passive: true });
  host.addEventListener('touchcancel', onTouchCancel, { passive: true });

  return cancelInertia;
}

// xterm instances are created lazily: a freshly loaded page may list many
// persisted sessions, but only the tab the user actually opens gets a
// terminal (DOM-heavy) allocated.
function ensureTerm(tab) {
  if (tab.term) return;
  const container = tab.container;
  const sessionId = tab.id;

  const term = tab.term = new Terminal(getTerminalOptions());
  const fitAddon = tab.fitAddon = new FitAddon.FitAddon();
  const searchAddon = tab.searchAddon = new SearchAddon.SearchAddon();
  term.loadAddon(fitAddon);
  term.loadAddon(searchAddon);
  term.open(container);
  // Set cursor options only after the terminal is attached to the DOM
  // to work around xterm.js not picking them up from the constructor.
  term.options.cursorBlink = currentSettings.cursorBlink;
  term.options.cursorStyle = currentSettings.cursorStyle;

  term.onData((data) => {
    const tab = tabs.find(t => t.id === sessionId);
    if (!tab || !tab.ws || tab.ws.readyState !== WebSocket.OPEN) return;
    // Stop any fling scroll so user input (e.g. virtual keyboard) does
    // not fight the viewport.
    if (tab.cancelInertia) tab.cancelInertia();
    // xterm.js may respond to OSC 10/11/12 color queries by sending the
    // reply back through onData. Over a network PTY that reply can arrive
    // too late and be echoed as visible text. Suppress it here.
    if (data.startsWith('\x1b]10;') || data.startsWith('\x1b]11;') || data.startsWith('\x1b]12;')) {
      return;
    }
    if (ctrlArmed) {
      let out = data;
      if (data.length === 1) {
        const code = data.charCodeAt(0);
        if (code >= 97 && code <= 122) out = String.fromCharCode(code - 96);       // a-z
        else if (code >= 65 && code <= 90) out = String.fromCharCode(code - 64);    // A-Z
        else if (code >= 49 && code <= 57) out = String.fromCharCode(code - 48);    // 1-9
      }
      ctrlArmed = false;
      updateModifierButtons();
      tab.ws.send(JSON.stringify({ type: 'input', data: out }));
      return;
    }
    if (altArmed) {
      const out = data.length === 1 ? ('\x1b' + data) : data;
      altArmed = false;
      updateModifierButtons();
      tab.ws.send(JSON.stringify({ type: 'input', data: out }));
      return;
    }
    if (shiftArmed) {
      const shiftMap = { '1': '!', '2': '@', '3': '#', '4': '$', '5': '%', '6': '^', '7': '&', '8': '*', '9': '(', '0': ')', '-': '_', '=': '+', '[': '{', ']': '}', '\\': '|', ';': ':', "'": '"', ',': '<', '.': '>', '/': '?', '`': '~' };
      let out = data;
      if (data.length === 1) {
        if (shiftMap[data]) out = shiftMap[data];
        else out = data.toUpperCase();
      }
      shiftArmed = false;
      updateModifierButtons();
      tab.ws.send(JSON.stringify({ type: 'input', data: out }));
      return;
    }
    tab.ws.send(JSON.stringify({ type: 'input', data }));
  });

  term.onResize(({ cols, rows }) => {
    const tab = tabs.find(t => t.id === sessionId);
    if (!tab) return;
    if (tab.resizeTimer) clearTimeout(tab.resizeTimer);
    tab.resizeTimer = setTimeout(() => {
      if (tab.ws && tab.ws.readyState === WebSocket.OPEN) {
        tab.ws.send(JSON.stringify({ type: 'resize', cols, rows }));
      }
    }, 100);
  });

  term.onSelectionChange(() => {
    if (!currentSettings.copyOnSelect) return;
    const tab = tabs.find(t => t.id === sessionId);
    if (!tab) return;
    if (tab.copyTimer) clearTimeout(tab.copyTimer);
    tab.copyTimer = setTimeout(() => {
      const text = term.getSelection();
      if (text) copyToClipboard(text);
    }, 100);
  });

  term.element.addEventListener('contextmenu', (e) => {
    if (!currentSettings.rightClickPaste) return;
    if (!navigator.clipboard || !window.isSecureContext) return;
    e.preventDefault();
    pasteFromClipboard();
  });

  // On touch devices, handle terminal scroll with momentum and left/right
  // swipe to switch tabs. Tapping focuses the terminal.
  tab.cancelInertia = setupTouchScroll(container, term, sessionId, tab);
}

function createTab(sessionId, active = false) {
  const existing = tabs.find(t => t.id === sessionId);
  if (existing) {
    if (active) switchTab(sessionId);
    return existing;
  }

  const tabEl = document.createElement('div');
  tabEl.className = 'tab' + (active ? ' active' : '');
  const titleEl = document.createElement('span');
  titleEl.className = 'title';
  titleEl.textContent = getTabTitle(sessionId);
  const closeEl = document.createElement('span');
  closeEl.className = 'close';
  closeEl.title = t('close');
  closeEl.textContent = '×';
  tabEl.appendChild(titleEl);
  tabEl.appendChild(closeEl);
  tabEl.onclick = (e) => {
    if (e.target.classList.contains('close')) {
      closeTab(sessionId);
    } else if (e.target.isContentEditable || e.detail > 1) {
      // ignore clicks while editing title or during double-clicks
    } else {
      switchTab(sessionId);
    }
  };
  tabsEl.insertBefore(tabEl, plusEl);

  const container = document.createElement('div');
  container.className = 'term-container' + (active ? ' active' : '');
  terminalArea.appendChild(container);

  const tab = { id: sessionId, sessionId, tabEl, container, term: null, fitAddon: null, searchAddon: null, searchTerm: '', ws: null, initialSync: true, intentionalClose: false, resizeTimer: null, copyTimer: null, title: getTabTitle(sessionId), cancelInertia: null };
  tabs.push(tab);

  titleEl.addEventListener('dblclick', (e) => {
    e.stopPropagation();
    startInlineRename(titleEl, tab);
  });

  if (active) {
    ensureTerm(tab);
    reconnectTab(tab);
    switchTab(sessionId);
  }

  updateEmptyState();
  return tab;
}

function switchTab(sessionId) {
  ctrlArmed = false;
  altArmed = false;
  shiftArmed = false;
  updateModifierButtons();
  for (const t of tabs) {
    const isActive = t.id === sessionId;
    t.tabEl.classList.toggle('active', isActive);
    t.container.classList.toggle('active', isActive);
  }
  activeTabId = sessionId;
  const tab = tabs.find(t => t.id === sessionId);
  if (tab) {
    ensureTerm(tab);
    if (!tab.ws) {
      reconnectTab(tab);
    } else if (tab.ws.readyState === WebSocket.OPEN) {
      applyTabSize(tab);
    }
    tab.term.focus();
  }
  updateUrl(sessionId);
}

function startInlineRename(titleEl, tab) {
  if (titleEl.isContentEditable) return;
  titleEl.contentEditable = 'true';
  titleEl.classList.add('editing');
  titleEl.focus();
  try {
    const range = document.createRange();
    range.selectNodeContents(titleEl);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  } catch (e) {}

  const finish = (save) => {
    titleEl.contentEditable = 'false';
    titleEl.classList.remove('editing');
    titleEl.removeEventListener('blur', onBlur);
    titleEl.removeEventListener('keydown', onKeydown);
    if (save) {
      const newTitle = titleEl.textContent.trim();
      tab.title = newTitle || tab.sessionId.slice(0, 8);
      savedTitles[tab.id] = tab.title;
      saveSavedTitles();
    }
    titleEl.textContent = tab.title;
  };
  const onBlur = () => finish(true);
  const onKeydown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      titleEl.blur();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      finish(false);
    }
  };
  titleEl.addEventListener('blur', onBlur);
  titleEl.addEventListener('keydown', onKeydown);
}

function closeSession(sessionId) {
  const url = appendToken(`/api/sessions/${encodeURIComponent(sessionId)}`);
  return fetch(url, { method: 'DELETE' })
    .catch(err => console.warn('close session failed', err));
}

function closeTab(sessionId) {
  const idx = tabs.findIndex(t => t.id === sessionId);
  if (idx === -1) return;
  const tab = tabs[idx];

  tab.intentionalClose = true;
  if (tab.keepaliveTimer) clearInterval(tab.keepaliveTimer);
  if (tab.reconnectTimer) clearTimeout(tab.reconnectTimer);
  if (tab.resizeTimer) clearTimeout(tab.resizeTimer);
  if (tab.copyTimer) clearTimeout(tab.copyTimer);

  if (tab.ws) {
    if (tab.ws.readyState === WebSocket.OPEN) {
      tab.ws.send(JSON.stringify({ type: 'close' }));
    }
    if (tab.ws.readyState <= WebSocket.OPEN) {
      tab.ws.close();
    }
  }

  // Also use the HTTP endpoint to guarantee the session is removed,
  // especially when the WebSocket was not yet established or already gone.
  closeSession(sessionId);

  if (tab.cancelInertia) tab.cancelInertia();
  if (tab.term) {
    tab.term.dispose();
    tab.term = null;
  }
  tab.tabEl.remove();
  tab.container.remove();
  tabs.splice(idx, 1);
  delete savedTitles[sessionId];
  saveSavedTitles();

  if (activeTabId === sessionId) {
    if (tabs.length) {
      switchTab(tabs[0].id);
    } else {
      activeTabId = null;
      clearSessionFromUrl();
    }
  }
  updateEmptyState();
}

function closeAllTabs() {
  if (!tabs.length) return;
  activeTabId = null;
  clearSessionFromUrl();
  // Close from the end to keep array index simple
  for (let i = tabs.length - 1; i >= 0; i--) {
    closeTab(tabs[i].id);
  }
  updateEmptyState();
}

function updateLayout() {
  // Use visualViewport height when available so the terminal stays within
  // the visible area when the mobile virtual keyboard appears.
  const h = window.visualViewport ? window.visualViewport.height : window.innerHeight;
  document.body.style.height = h + 'px';
  if (activeTabId && currentSettings.autoFit) {
    const tab = tabs.find(t => t.id === activeTabId);
    applyTabSize(tab);
  }
}
window.addEventListener('resize', debounce(updateLayout, 150));
if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', debounce(updateLayout, 100));
  window.visualViewport.addEventListener('resize', () => {
    // Keyboard or address-bar changes can shift touch coordinates and
    // cause in-flight fling scrolls to feel jumpy; stop them.
    tabs.forEach(t => t.cancelInertia && t.cancelInertia());
  });
}

function sendToActiveTerminal(text) {
  const tab = tabs.find(t => t.id === activeTabId);
  if (tab && tab.ws && tab.ws.readyState === WebSocket.OPEN) {
    tab.ws.send(JSON.stringify({ type: 'input', data: text }));
  }
}

function updateModifierButtons() {
  if (ctrlBtn) ctrlBtn.classList.toggle('active', ctrlArmed);
  if (altBtn) altBtn.classList.toggle('active', altArmed);
  if (shiftBtn) shiftBtn.classList.toggle('active', shiftArmed);
}

function renderShortcutBar() {
  const bar = document.getElementById('shortcut-bar');
  if (!bar) return;
  const shortcuts = [
    { label: 'Ctrl', modifier: 'ctrl' },
    { label: 'Alt', modifier: 'alt' },
    { label: 'Shift', modifier: 'shift' },
    { label: 'Esc', text: '\x1B' },
    { label: 'Tab', text: '\t' },
    { label: '←', text: '\x1b[D' },
    { label: '→', text: '\x1b[C' },
    { label: '↑', text: '\x1b[A' },
    { label: '↓', text: '\x1b[B' },
    { label: 'Home', text: '\x1b[H' },
    { label: 'End', text: '\x1b[F' },
    { label: 'PgUp', text: '\x1b[5~' },
    { label: 'PgDn', text: '\x1b[6~' },
    { label: '|', text: '|' },
    { label: '~', text: '~' },
    { label: '_', text: '_' },
    { label: '`', text: '`' },
    { label: '!', text: '!' },
    { label: '$', text: '$' }
  ];
  bar.innerHTML = '';
  for (const s of shortcuts) {
    const btn = document.createElement('button');
    btn.textContent = s.label;
    if (s.modifier) {
      btn.dataset.modifier = s.modifier;
      if (s.modifier === 'ctrl') ctrlBtn = btn;
      if (s.modifier === 'alt') altBtn = btn;
      if (s.modifier === 'shift') shiftBtn = btn;
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        if (s.modifier === 'ctrl') {
          ctrlArmed = !ctrlArmed;
          if (ctrlArmed) { altArmed = false; shiftArmed = false; }
        } else if (s.modifier === 'alt') {
          altArmed = !altArmed;
          if (altArmed) { ctrlArmed = false; shiftArmed = false; }
        } else if (s.modifier === 'shift') {
          shiftArmed = !shiftArmed;
          if (shiftArmed) { ctrlArmed = false; altArmed = false; }
        }
        updateModifierButtons();
      });
    } else {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        sendToActiveTerminal(s.text);
      });
    }
    bar.appendChild(btn);
  }
  updateModifierButtons();
}

function disconnectAllTabs() {
  // Closing the browser page/tab must NOT kill the backend PTYs.
  tabs.forEach(t => {
    t.intentionalClose = true;
    if (t.keepaliveTimer) clearInterval(t.keepaliveTimer);
    if (t.reconnectTimer) clearTimeout(t.reconnectTimer);
    if (t.resizeTimer) clearTimeout(t.resizeTimer);
    if (t.copyTimer) clearTimeout(t.copyTimer);
    if (t.ws) t.ws.close();
  });
}
window.addEventListener('beforeunload', disconnectAllTabs);
window.addEventListener('pagehide', disconnectAllTabs);

function toggleColorRows(show) {
  document.querySelectorAll('.color-row').forEach(el => {
    el.style.display = show ? '' : 'none';
  });
}

function populateThemeSelect() {
  const sel = document.getElementById('setting-theme');
  sel.innerHTML = '';
  Object.keys(themePresets).forEach(name => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  });
  const custom = document.createElement('option');
  custom.value = 'Custom';
  custom.textContent = t('custom');
  sel.appendChild(custom);

  sel.onchange = () => {
    const name = sel.value;
    if (themePresets[name]) {
      const p = themePresets[name];
      document.getElementById('setting-bg').value = p.background;
      document.getElementById('setting-fg').value = p.foreground;
      document.getElementById('setting-cursor').value = p.cursor;
    }
    toggleColorRows(name === 'Custom');
  };

  ['setting-bg', 'setting-fg', 'setting-cursor'].forEach(id => {
    document.getElementById(id).oninput = () => {
      sel.value = 'Custom';
      toggleColorRows(true);
    };
  });
}

function populateSelects() {
  populateThemeSelect();

  const styleSel = document.getElementById('setting-cursor-style');
  styleSel.innerHTML = '';
  ['block', 'bar', 'underline'].forEach(k => {
    const opt = document.createElement('option');
    opt.value = k;
    opt.textContent = t(k);
    styleSel.appendChild(opt);
  });

  const weightSel = document.getElementById('setting-font-weight');
  weightSel.innerHTML = '';
  ['normal', 'bold', '100', '200', '300', '400', '500', '600', '700', '800', '900'].forEach(k => {
    const opt = document.createElement('option');
    opt.value = k;
    opt.textContent = k;
    weightSel.appendChild(opt);
  });
}

function toggleFixedSizeFields() {
  const autoFit = document.getElementById('setting-auto-fit').checked;
  const fixed = document.getElementById('fixed-size-fields');
  if (fixed) fixed.style.display = autoFit ? 'none' : '';
}

function openSettings() {
  populateSelects();
  document.getElementById('setting-language').value = currentSettings.language;

  const themeSel = document.getElementById('setting-theme');
  themeSel.value = currentSettings.theme || 'Custom';
  if (!themeSel.value) themeSel.value = 'Custom';

  document.getElementById('setting-bg').value = currentSettings.background;
  document.getElementById('setting-fg').value = currentSettings.foreground;
  document.getElementById('setting-cursor').value = currentSettings.cursor;
  toggleColorRows(themeSel.value === 'Custom');
  document.getElementById('setting-cursor-blink').checked = currentSettings.cursorBlink;
  document.getElementById('setting-cursor-style').value = currentSettings.cursorStyle;
  document.getElementById('setting-font-size').value = currentSettings.fontSize;
  document.getElementById('setting-font-family').value = currentSettings.fontFamily;
  document.getElementById('setting-font-weight').value = currentSettings.fontWeight;
  document.getElementById('setting-line-height').value = currentSettings.lineHeight;
  document.getElementById('setting-letter-spacing').value = currentSettings.letterSpacing;
  document.getElementById('setting-tab-width').value = currentSettings.tabStopWidth;
  document.getElementById('setting-scrollback').value = currentSettings.scrollback;
  document.getElementById('setting-copy-select').checked = currentSettings.copyOnSelect;
  document.getElementById('setting-right-click-paste').checked = currentSettings.rightClickPaste;
  document.getElementById('setting-auto-reconnect').checked = currentSettings.autoReconnect;
  document.getElementById('setting-reconnect-interval').value = currentSettings.reconnectInterval;
  document.getElementById('setting-keepalive-interval').value = currentSettings.keepaliveInterval;
  document.getElementById('setting-term').value = currentSettings.term;
  document.getElementById('setting-colorterm').value = currentSettings.colorterm;
  document.getElementById('setting-auto-fit').checked = currentSettings.autoFit;
  toggleFixedSizeFields();
  document.getElementById('setting-term-cols').value = currentSettings.termCols;
  document.getElementById('setting-term-rows').value = currentSettings.termRows;
  document.getElementById('settings-modal').classList.add('active');
}

function closeSettings() {
  document.getElementById('settings-modal').classList.remove('active');
}

function applySettings() {
  const newLang = document.getElementById('setting-language').value;
  const themeSel = document.getElementById('setting-theme');
  const themeName = themeSel.value;
  currentSettings.language = newLang;
  currentSettings.theme = themeName;

  const pickedTheme = themePresets[themeName];
  if (pickedTheme) {
    currentSettings.background = pickedTheme.background;
    currentSettings.foreground = pickedTheme.foreground;
    currentSettings.cursor = pickedTheme.cursor;
    currentSettings.selectionBackground = pickedTheme.selectionBackground;
  } else {
    currentSettings.background = document.getElementById('setting-bg').value;
    currentSettings.foreground = document.getElementById('setting-fg').value;
    currentSettings.cursor = document.getElementById('setting-cursor').value;
  }

  currentSettings.cursorBlink = document.getElementById('setting-cursor-blink').checked;
  currentSettings.cursorStyle = document.getElementById('setting-cursor-style').value;
  currentSettings.fontSize = parseInt(document.getElementById('setting-font-size').value, 10) || defaultSettings.fontSize;
  currentSettings.fontFamily = document.getElementById('setting-font-family').value || defaultSettings.fontFamily;
  currentSettings.fontWeight = document.getElementById('setting-font-weight').value;
  currentSettings.lineHeight = parseFloat(document.getElementById('setting-line-height').value) || defaultSettings.lineHeight;
  currentSettings.letterSpacing = parseFloat(document.getElementById('setting-letter-spacing').value) || defaultSettings.letterSpacing;
  currentSettings.tabStopWidth = parseInt(document.getElementById('setting-tab-width').value, 10) || defaultSettings.tabStopWidth;
  currentSettings.scrollback = parseInt(document.getElementById('setting-scrollback').value, 10);
  if (isNaN(currentSettings.scrollback)) currentSettings.scrollback = defaultSettings.scrollback;

  currentSettings.copyOnSelect = document.getElementById('setting-copy-select').checked;
  currentSettings.rightClickPaste = document.getElementById('setting-right-click-paste').checked;
  currentSettings.autoReconnect = document.getElementById('setting-auto-reconnect').checked;
  currentSettings.reconnectInterval = parseInt(document.getElementById('setting-reconnect-interval').value, 10) || defaultSettings.reconnectInterval;
  currentSettings.keepaliveInterval = parseInt(document.getElementById('setting-keepalive-interval').value, 10);
  if (isNaN(currentSettings.keepaliveInterval)) currentSettings.keepaliveInterval = defaultSettings.keepaliveInterval;

  currentSettings.term = document.getElementById('setting-term').value;
  currentSettings.colorterm = document.getElementById('setting-colorterm').value;
  currentSettings.autoFit = document.getElementById('setting-auto-fit').checked;
  currentSettings.termCols = parseInt(document.getElementById('setting-term-cols').value, 10) || defaultSettings.termCols;
  currentSettings.termRows = parseInt(document.getElementById('setting-term-rows').value, 10) || defaultSettings.termRows;

  saveSettings();
  updateI18n();
  applyTerminalSettings();
  applyConnectionSettings();
  closeSettings();
}

function resetSettings() {
  currentSettings = { ...defaultSettings };
  saveSettings();
  updateI18n();
  applyTerminalSettings();
  applyConnectionSettings();
  openSettings();
}

async function init() {
  // Drop ?token= from the address bar: the server has already issued an
  // auth cookie for this session, so the token does not need to stay in
  // browser history.
  const urlParams = new URLSearchParams(location.search);
  if (urlParams.has('token')) {
    urlParams.delete('token');
    const qs = urlParams.toString();
    history.replaceState({}, '', qs ? '?' + qs : location.pathname);
  }
  try {
    const resp = await fetch(appendToken('/api/sessions'));
    if (resp.status === 401 || resp.status === 403) {
      location.href = '/login';
      return;
    }
    const sessList = await resp.json();
    const querySid = getQuerySession();

    if (querySid && !sessList.find(s => s.id === querySid)) {
      sessList.push({ id: querySid });
    }

    const initialSid = querySid || (sessList[0] && sessList[0].id);
    sessList.forEach(s => createTab(s.id, s.id === initialSid));

    if (!activeTabId && tabs.length) {
      switchTab(tabs[0].id);
    }

    updateI18n();
  } catch (e) {
    console.error('init failed', e);
    updateI18n();
  }
  updateEmptyState();
}

// Capture phase: xterm consumes (stopPropagation) keys it handles on its
// textarea, so a bubble-phase listener never sees them while a terminal is
// focused. Handling at window-capture intercepts before that happens; keys
// we don't claim must continue propagating to xterm untouched.
window.addEventListener('keydown', (e) => {
  if (e.target && e.target.isContentEditable) return;
  const swallow = () => { e.preventDefault(); e.stopPropagation(); };
  if (e.key === 'F3') {
    const tab = tabs.find(t => t.id === activeTabId);
    if (tab && tab.searchAddon && tab.searchTerm) {
      swallow();
      if (e.shiftKey) tab.searchAddon.findPrevious(tab.searchTerm);
      else tab.searchAddon.findNext(tab.searchTerm);
    }
    return;
  }
  // Alt+N / Alt+W reach the page in every mainstream browser, unlike
  // Ctrl+Shift+T / Ctrl+Shift+W which Chrome/Firefox handle natively
  // (reopen closed tab / close the whole window) before the page sees them.
  if (e.altKey && !e.ctrlKey && !e.shiftKey) {
    if (e.code === 'KeyN') {
      swallow();
      newTerminal();
    } else if (e.code === 'KeyW') {
      swallow();
      if (activeTabId) closeTab(activeTabId);
    }
    return;
  }
  if (!e.ctrlKey || !e.shiftKey) return;
  // Fallback for contexts that do deliver Ctrl+Shift+T/W (e.g. installed PWA).
  if (e.key === 'T' || e.key === 't') {
    swallow();
    newTerminal();
  } else if (e.key === 'W' || e.key === 'w') {
    swallow();
    if (activeTabId) closeTab(activeTabId);
  } else if (e.key === 'F' || e.key === 'f') {
    swallow();
    const tab = tabs.find(t => t.id === activeTabId);
    if (!tab || !tab.searchAddon) return;
    const needle = prompt(t('search'), tab.searchTerm);
    if (needle === null) return;
    tab.searchTerm = needle.trim();
    if (tab.searchTerm) tab.searchAddon.findNext(tab.searchTerm);
  }
}, true);

init();
updateLayout();
renderShortcutBar();
