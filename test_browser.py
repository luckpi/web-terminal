#!/usr/bin/env python3
"""Browser regression tests. See README for Playwright setup."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright


class TerminalBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cls.url = f'http://127.0.0.1:{port}'
        cls.password = 'review-测试-password'
        cls.log = tempfile.TemporaryFile()
        cls.server = subprocess.Popen(
            [sys.executable, 'server.py'], cwd=Path(__file__).resolve().parent,
            env={**os.environ, 'HOST': '127.0.0.1', 'PORT': str(port),
                 'TOKEN': cls.password, 'SHELL': '/bin/bash', 'MAX_SESSIONS': '2'},
            stdout=cls.log, stderr=cls.log,
        )
        try:
            for _ in range(100):
                if cls.server.poll() is not None:
                    raise RuntimeError('Test server exited')
                try:
                    urllib.request.urlopen(cls.url + '/login', timeout=.2).close()
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(.05)
            else:
                raise RuntimeError('Test server did not start')
            cls.playwright = sync_playwright().start()
            options = {'headless': True, 'args': ['--no-sandbox']}
            if os.environ.get('CHROMIUM_PATH'):
                options['executable_path'] = os.environ['CHROMIUM_PATH']
            cls.browser = cls.playwright.chromium.launch(**options)
        except Exception:
            cls.server.terminate()
            cls.server.wait(timeout=10)
            if hasattr(cls, 'playwright'):
                cls.playwright.stop()
            cls.log.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.log.close()

    def setUp(self):
        self.context = self.browser.new_context(
            viewport={'width': 390, 'height': 844}, has_touch=True,
            is_mobile=True, locale='zh-CN')
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.page.locator('input[name=password]').fill(self.password)
        self.page.locator('button[type=submit]').click()
        self.page.wait_for_url(self.url + '/')
        self.page.locator('#empty-state.visible').wait_for()

    def tearDown(self):
        sessions = self.context.request.get(self.url + '/api/sessions').json()
        for session in sessions:
            self.context.request.delete(self.url + '/api/sessions/' + session['id'])
        self.context.close()
        self.assertEqual(self.errors, [])

    def create_terminal(self):
        self.page.evaluate('newTerminal()')
        self.page.wait_for_function("tabs.find(t => t.id === activeTabId).state === 'connected'")

    def test_unicode_password_and_session_limit(self):
        self.create_terminal()
        self.create_terminal()
        self.page.evaluate('newTerminal()')
        self.page.wait_for_function("tabs.find(t => t.id === activeTabId).errorKey === 'maxSessions'")
        self.assertIn('会话数量已达上限', self.page.locator('#connection-text').inner_text())
        self.assertTrue(self.page.locator('#reconnect-btn').is_visible())
        self.assertEqual(len(self.context.request.get(self.url + '/api/sessions').json()), 2)

    def test_reconnect_always_syncs_pty_size(self):
        self.create_terminal()
        self.page.wait_for_timeout(200)
        dimensions = self.page.evaluate('({rows:tabs[0].term.rows,cols:tabs[0].term.cols})')
        self.assertNotEqual(dimensions, {'rows': 24, 'cols': 80})
        self.page.evaluate("tabs[0].ws.send(JSON.stringify({type:'resize',rows:24,cols:80})); sendToActiveTerminal('stty size\\n')")
        last_size = """() => {
          const buffer=tabs[0].term.buffer.active;
          const lines=Array.from({length:buffer.length},(_,i)=>buffer.getLine(i).translateToString().trim());
          return lines.filter(line=>/^\\d+ \\d+$/.test(line)).pop();
        }"""
        self.page.wait_for_function(f'({last_size})() === "24 80"')
        self.page.evaluate('reconnectTab(tabs[0])')
        self.page.wait_for_function("tabs[0].state === 'connected'")
        self.page.evaluate("sendToActiveTerminal('stty size\\n')")
        expected = f"{dimensions['rows']} {dimensions['cols']}"
        self.page.wait_for_function(f'({last_size})() === {json.dumps(expected)}')

    def test_manual_and_automatic_reconnection(self):
        self.create_terminal()
        self.page.evaluate('currentSettings.autoReconnect=false; tabs[0].ws.close()')
        self.page.wait_for_function("tabs[0].state === 'disconnected'")
        self.assertTrue(self.page.locator('#reconnect-btn').is_visible())
        self.page.locator('#reconnect-btn').click()
        self.page.wait_for_function("tabs[0].state === 'connected'")
        self.page.evaluate('window.previousSocket=tabs[0].ws; currentSettings.autoReconnect=true; currentSettings.reconnectInterval=50; tabs[0].ws.close()')
        self.page.wait_for_function("tabs[0].ws !== window.previousSocket && tabs[0].state === 'connected'")
        self.assertEqual(self.page.evaluate('tabs[0].reconnectAttempts'), 0)

    def test_session_list_failure_has_reload_action(self):
        self.page.route('**/api/sessions', lambda route: route.fulfill(status=503, body='Unavailable'))
        self.page.goto(self.url)
        self.page.wait_for_function('loadError')
        self.assertIn('无法加载会话列表', self.page.locator('#connection-text').inner_text())
        self.assertEqual(self.page.locator('#reconnect-btn').inner_text(), '重新加载')
        self.page.unroute('**/api/sessions')
        self.page.locator('#reconnect-btn').click()
        self.page.locator('#empty-state.visible').wait_for()
        self.assertFalse(self.page.locator('#reconnect-btn').is_visible())

    def test_clipboard_uses_bracketed_paste(self):
        self.create_terminal()
        messages = self.page.evaluate('''async () => {
          const tab=tabs[0];
          await new Promise(resolve=>tab.term.write('\\x1b[?2004h',resolve));
          Object.defineProperty(navigator,'clipboard',{configurable:true,value:{readText:async()=>"echo one\\necho two\\n"}});
          const sent=[], original=tab.ws.send.bind(tab.ws);
          tab.ws.send=message=>sent.push(JSON.parse(message));
          try { await pasteFromClipboard(); } finally { tab.ws.send=original; }
          return sent;
        }''')
        self.assertEqual(messages, [{'type': 'input', 'data': '\x1b[200~echo one\recho two\r\x1b[201~'}])

    def test_exit_has_recovery_action(self):
        self.create_terminal()
        self.page.evaluate("sendToActiveTerminal('exit\\n')")
        self.page.wait_for_function("tabs[0].state === 'ended'")
        self.assertIn('会话已结束', self.page.locator('#connection-text').inner_text())
        self.page.locator('#reconnect-btn').click()
        self.page.wait_for_function("tabs.length === 2 && tabs[1].state === 'connected'")
        self.assertNotEqual(self.page.evaluate('tabs[0].id'), self.page.evaluate('tabs[1].id'))

    def test_termination_confirmation_and_failed_delete(self):
        self.create_terminal()
        self.page.once('dialog', lambda dialog: dialog.dismiss())
        self.page.locator('.tab .close').click()
        self.assertEqual(self.page.evaluate('tabs.length'), 1)
        self.page.once('dialog', lambda dialog: dialog.dismiss())
        self.page.evaluate('closeAllTabs()')
        self.assertEqual(len(self.context.request.get(self.url + '/api/sessions').json()), 1)
        self.page.route('**/api/sessions/*', lambda route: route.fulfill(status=503, body='Unavailable'))
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.locator('.tab .close').click()
        self.page.locator('#notice').wait_for(state='visible')
        self.assertIn('终止失败', self.page.locator('#notice').inner_text())
        self.assertEqual(self.page.evaluate('tabs.length'), 1)
        self.page.unroute('**/api/sessions/*')
        self.create_terminal()
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.evaluate('closeAllTabs()')
        self.assertEqual(self.page.evaluate('tabs.length'), 0)
        self.assertEqual(self.context.request.get(self.url + '/api/sessions').json(), [])

    def test_mobile_modifiers_and_touch_targets(self):
        self.create_terminal()
        messages = self.page.evaluate('''() => {
          const tab=tabs[0], sent=[], original=tab.ws.send.bind(tab.ws);
          tab.ws.send=message=>sent.push(JSON.parse(message));
          document.querySelector('[data-modifier=ctrl]').click();
          [...document.querySelectorAll('#shortcut-bar button')].find(b=>b.textContent==='←').click();
          const reset=!ctrlArmed;
          document.querySelector('[data-modifier=alt]').click();
          document.querySelector('[data-modifier=shift]').click();
          [...document.querySelectorAll('#shortcut-bar button')].find(b=>b.textContent==='→').click();
          document.querySelector('[data-modifier=ctrl]').click();
          [...document.querySelectorAll('#shortcut-bar button')].find(b=>b.textContent==='Ctrl+C').click();
          tab.ws.send=original;
          return {sent,reset,armed:ctrlArmed||altArmed||shiftArmed};
        }''')
        self.assertEqual([m['data'] for m in messages['sent']], ['\x1b[1;5D', '\x1b[1;4C', '\x03'])
        self.assertTrue(messages['reset'])
        self.assertFalse(messages['armed'])
        for selector in ['.tab .close', '#shortcut-bar button']:
            bounds = self.page.locator(selector).first.bounding_box()
            self.assertGreaterEqual(bounds['width'], 44)
            self.assertGreaterEqual(bounds['height'], 44)

    def test_settings_focus_and_sticky_actions(self):
        self.create_terminal()
        self.page.locator('#settings-btn').click()
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'setting-language')
        actions = self.page.locator('.modal-actions').bounding_box()
        self.assertLessEqual(actions['y'] + actions['height'], 844)
        self.page.locator('.modal-header button').focus()
        self.page.keyboard.press('Shift+Tab')
        self.assertEqual(self.page.evaluate('document.activeElement.textContent'), '应用')
        self.page.locator('.modal-actions button').last.focus()
        self.page.keyboard.press('Tab')
        self.assertEqual(self.page.evaluate('document.activeElement.getAttribute("aria-label")'), 'Close settings')
        self.page.keyboard.press('Escape')
        self.assertFalse(self.page.locator('#settings-modal').is_visible())
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'settings-btn')
        self.page.locator('#settings-btn').click()
        self.page.locator('[data-i18n=reset]').click()
        self.page.keyboard.press('Escape')
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'settings-btn')


if __name__ == '__main__':
    unittest.main(verbosity=2)
