// Offline checks for opening the local app from a static Live Server page.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');

(async () => {
  for (const running of [true, false]) {
    const status = { textContent: '' };
    const link = { href: '' };
    const redirects = [];
    const location = { search: '?q=finance', hash: '#results', replace: (url) => redirects.push(url) };
    const context = vm.createContext({
      URL, AbortController, setTimeout, clearTimeout,
      window: { location },
      document: { querySelector: (selector) => selector === '#open-hub' ? link : status },
      fetch: async (url, options) => {
        assert.equal(url.href, 'http://127.0.0.1:8080/api/health');
        assert.equal(options.credentials, 'omit');
        if (!running) throw new Error('Server stopped');
        return {};
      },
    });
    await vm.runInContext(source, context);
    assert.equal(link.href, 'http://127.0.0.1:8080/?q=finance#results');
    if (running) assert.deepEqual(redirects, [link.href]);
    else {
      assert.equal(redirects.length, 0);
      assert.ok(status.textContent.includes('Start Hub.command'));
    }
  }
  console.log('Static entry handles running and stopped app servers.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
