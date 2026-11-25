// Offline startup test: no browser, page observation, or network access is used.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { html, source, meta, data } = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

class Node {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.hidden = false; this.classList = { toggle() {} };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap((child) => child instanceof Node ? [child, ...child.querySelectorAll('*')] : []);
    if (selector === '*') return descendants;
    return descendants.filter((node) => node.tagName === 'input' && (!selector.includes(':checked') || node.checked));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  showModal() { this.open = true; }
  set textContent(value) { this.valueText = String(value); this.children = []; }
  get textContent() { return this.valueText || this.children.map((n) => n.textContent || String(n)).join(' '); }
}

(async () => {
  for (const failure of ['map-library-missing', 'map-initialization-error', 'map-working', 'source-refresh-running']) {
    let refreshing = failure === 'source-refresh-running';
    const intervals = new Map();
    const requests = [];
    const nodes = new Map([...html.matchAll(/\bid="([\w-]+)"/g)].map((match) => [match[1], new Node()]));
    nodes.get('initial-data').textContent = JSON.stringify({ data, meta: { ...meta, refreshing } });
    nodes.get('distance').value = '30';
    nodes.get('search').value = '';
    nodes.get('sort').value = 'newest';
    const tabs = ['all', 'mapped', 'unlocated'].map((view) => { const tab = new Node('button'); tab.dataset.view = view; return tab; });
    const document = {
      querySelector: (selector) => nodes.get(selector.slice(1)) || null,
      querySelectorAll: (selector) => selector === '[data-view]' ? tabs : [],
      createElement: (tag) => new Node(tag),
      createTextNode: (text) => ({ textContent: text }),
    };
    const mapOptions = [];
    const markerOptions = [];
    const layer = { addTo() { return this; }, on() { return this; }, bindPopup() { return this; }, setRadius() {}, setStyle() {}, clearLayers() {} };
    const library = failure === 'map-initialization-error' ? { map() { throw new Error('Simulated unavailable map renderer'); } } : failure === 'map-working' ? {
      map(id, options) { mapOptions.push(options); return { setView() { return this; }, fitBounds() {} }; },
      tileLayer() { return { ...layer }; }, marker(coords, options) { markerOptions.push(options); return { ...layer }; },
      circle() { return { ...layer }; }, layerGroup() { return { ...layer }; }, divIcon(options) { return options; },
      latLngBounds() { return { pad() { return this; } }; },
    } : undefined;
    const context = vm.createContext({
      document, window: { L: library }, L: library, URL, URLSearchParams, AbortController, Intl,
      setTimeout, clearTimeout,
      setInterval: (callback) => { const id = intervals.size + 1; intervals.set(id, callback); return id; },
      clearInterval: (id) => intervals.delete(id),
      FormData: class { *[Symbol.iterator]() {} },
      console: { error() {}, log() {} },
      fetch: async (url) => {
        requests.push(url);
        return { ok: true, json: async () => url.startsWith('/api/opportunities') ? data : ({ ...meta, refreshing }) };
      },
    });
    vm.runInContext(source, context, { timeout: 3000 });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(nodes.get('results').children.length, data.items.length, failure);
    assert.ok(nodes.get('results').textContent.includes(data.items[0].title), failure);
    if (failure === 'map-working') {
      assert.equal(mapOptions[0].scrollWheelZoom, true);
      assert.equal(mapOptions[0].touchZoom, true);
      assert.equal(mapOptions[0].zoomControl, true);
      assert.ok(markerOptions.length > 1, 'Available worksite markers must render');
    } else assert.ok(nodes.get('map-message').textContent.includes('map'), failure);
    assert.equal(nodes.get('startup-status').hidden, true, failure);
    assert.equal(nodes.get('current-count').textContent, String(meta.counts.current_count));
    assert.equal(nodes.get('category-options').children.length, meta.categories.length, 'Career disciplines hydrate on normal startup');
    assert.ok(nodes.get('category-options').textContent.includes(meta.categories[0].label));
    if (failure === 'source-refresh-running') {
      const cards = [...nodes.get('results').children];
      const firstCategory = nodes.get('category-options').querySelector('input');
      firstCategory.checked = true;
      const poll = [...intervals.values()][0];
      assert.ok(poll, 'An active source scan should poll its status');
      for (let i = 0; i < 3; i++) await poll();
      refreshing = false;
      await poll();
      assert.deepEqual(nodes.get('results').children, cards, 'Background checks must preserve the exact existing job cards');
      assert.equal(requests.filter((url) => url.startsWith('/api/opportunities')).length, 0, 'Source polling must never reload jobs');
      assert.equal(firstCategory.checked, true, 'Polling preserves chosen career filters');
      assert.equal(nodes.get('listing-updates').hidden, false);
      assert.ok(nodes.get('listing-update-message').textContent.includes('finished'));
      assert.equal(intervals.size, 0, 'Status polling stops when source checks finish');
      nodes.get('show-latest-jobs').listeners.click();
      assert.deepEqual(nodes.get('results').children, cards, 'Requested updates keep existing cards visible while waiting');
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(requests.filter((url) => url.startsWith('/api/opportunities')).length, 1, 'New jobs load only after the user asks');
      assert.equal(nodes.get('results')['aria-busy'], 'false');
    }
    vm.runInContext("showDetails({...state.items[0], archived: true, archive_reason: 'The deadline has passed'})", context);
    const descendants = (node) => [node, ...node.children.flatMap((child) => child instanceof Node ? descendants(child) : [])];
    assert.equal(descendants(nodes.get('job-detail')).filter((node) => node.tagName === 'a').length, 0, 'Historical records must not offer application links');
  }
  console.log('Career filters populate; background collection preserves jobs and selected filters; maps survive startup failures.');
})().catch((error) => { console.error(error); process.exit(1); });
