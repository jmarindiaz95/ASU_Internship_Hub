'use strict';

const $ = (selector) => document.querySelector(selector);
const state = { view: 'all', items: [], features: [], total: 0, selected: null, request: 0, controller: null, markers: new Map(), poll: null, pollBusy: false, categorySignature: null, awaitingUpdates: false };
const labels = { internship: 'Internship', job: 'Other employment', program: 'Career program', unknown: 'Not stated', full_time: 'Full-time', part_time: 'Part-time', on_site: 'On-site', hybrid: 'Hybrid', remote: 'Remote', early: 'Early career', mid: 'Mid career', senior: 'Senior', paid: 'Paid', unpaid: 'Unpaid' };
const campus = [33.4177504, -111.9343818];
let map, radiusCircle, markerLayer;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(text, className, handler) {
  const node = element('button', className, text);
  node.type = 'button';
  node.addEventListener('click', handler);
  return node;
}
function formatDate(value) {
  if (!value) return 'Not stated';
  const parsed = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(parsed.getTime()) ? 'Not stated' : new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'America/Phoenix' }).format(parsed);
}
function formatDateTime(value) {
  if (!value) return 'Not scheduled';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Not stated' : new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: 'America/Phoenix', timeZoneName: 'short' }).format(parsed);
}
function money(item) {
  if (item.salary_min == null) return item.paid_status === 'paid' ? 'Paid · amount not stated' : item.paid_status === 'unpaid' ? 'Unpaid' : 'Pay not stated';
  const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: item.salary_period === 'hour' ? 2 : 0 });
  const maximum = item.salary_max != null && item.salary_max !== item.salary_min ? `–${currency.format(item.salary_max)}` : '';
  return `${currency.format(item.salary_min)}${maximum}${item.salary_period === 'hour' ? '/hr' : item.salary_period === 'year' ? '/yr' : ''}`;
}
function externalLink(label, value, className) {
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    const link = element('a', className, label);
    link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
    return link;
  } catch { return null; }
}
function mapMessage(text) {
  $('#map-message').textContent = text || '';
  $('#map-message').hidden = !text;
}
function initializeMap() {
  if (!window.L) {
    mapMessage('The map could not load. You can still search and open listings.');
    return;
  }
  map = L.map('map', {
    scrollWheelZoom: true,
    touchZoom: true,
    doubleClickZoom: true,
    dragging: true,
    keyboard: true,
    zoomControl: true,
  }).setView(campus, 11);
  const tiles = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(map);
  let tileErrors = 0;
  tiles.on('tileerror', () => { if (++tileErrors === 3) mapMessage('Basemap tiles are unavailable. Location markers and the listing list remain usable.'); });
  L.marker(campus, { title: 'ASU Memorial Union', icon: L.divIcon({ className: 'hub-marker', html: '<span class="campus-star">★</span>', iconSize: [35, 35], iconAnchor: [17, 17] }) }).addTo(map).bindPopup('ASU Memorial Union · campus reference point');
  radiusCircle = L.circle(campus, { radius: 30 * 1609.344, color: '#65152a', weight: 1, dashArray: '5 6', fillOpacity: 0.025, interactive: false }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
}
function drawMap() {
  if (!map || !markerLayer || !radiusCircle) return;
  try { renderMap(); }
  catch (error) {
    console.error('Map rendering failed:', error);
    map = null;
    mapMessage('The map is unavailable. You can still search and open the listings.');
  }
}
function renderMap() {
  markerLayer.clearLayers(); state.markers.clear();
  radiusCircle.setRadius(Number($('#distance').value) * 1609.344);
  radiusCircle.setStyle({ opacity: state.view === 'unlocated' ? 0 : 1, fillOpacity: state.view === 'unlocated' ? 0 : 0.025 });
  const items = new Map(state.items.map((item) => [item.id, item]));
  const groups = new Map();
  for (const feature of state.features) {
    const key = feature.geometry.coordinates.join(',');
    if (!groups.has(key)) groups.set(key, { coordinates: feature.geometry.coordinates, items: new Map() });
    const item = items.get(feature.properties.opportunity_id);
    if (item) groups.get(key).items.set(item.id, item);
  }
  for (const group of groups.values()) {
    const jobs = [...group.items.values()];
    const reference = jobs.some((item) => item.location_basis && item.location_basis !== 'worksite');
    const internship = jobs.every((item) => item.opportunity_type === 'internship');
    const marker = L.marker([group.coordinates[1], group.coordinates[0]], { title: jobs.map((j) => j.title).join('; '), icon: L.divIcon({ className: 'hub-marker', html: `<span class="marker-dot ${internship ? 'internship' : ''} ${reference ? 'company-reference' : ''}">${jobs.length > 1 ? jobs.length : ''}</span>`, iconSize: [28, 28], iconAnchor: [14, 14] }) }).addTo(markerLayer);
    const popup = element('div');
    popup.append(element('h3', 'popup-title', `${jobs.length} position${jobs.length === 1 ? '' : 's'} at this ${reference ? 'company reference' : 'worksite'}`));
    if (reference) popup.append(element('p', '', 'Company address reference; exact job worksites are not confirmed.'));
    for (const item of jobs) {
      popup.append(button(item.title, 'popup-item', () => { selectItem(item.id, false); showDetails(item); }));
      if (!state.markers.has(item.id)) state.markers.set(item.id, []);
      state.markers.get(item.id).push(marker);
    }
    marker.bindPopup(popup);
    marker.on('click', () => selectItem(jobs[0].id, false));
  }
  mapMessage(state.view === 'unlocated' ? 'These jobs are available, but their locations could not be clearly resolved.' : !state.features.length ? 'No mapped addresses match these filters. Jobs with unclear locations remain in the list.' : '');
}
function fitMap() {
  if (!map) return;
  const coordinates = state.features.map((feature) => [feature.geometry.coordinates[1], feature.geometry.coordinates[0]]);
  map.fitBounds(L.latLngBounds([campus, ...coordinates]).pad(0.3), { maxZoom: coordinates.length ? 13 : 12 });
}
function selectItem(id, openPopup = true) {
  state.selected = id;
  document.querySelectorAll('.job-card').forEach((card) => card.classList.toggle('selected', card.dataset.id === id));
  const card = [...document.querySelectorAll('.job-card')].find((node) => node.dataset.id === id);
  if (card) card.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest' });
  const selectedMarkers = new Set(state.markers.get(id) || []);
  for (const markers of state.markers.values()) {
    for (const marker of markers) marker.getElement()?.querySelector('.marker-dot')?.classList.toggle('selected', selectedMarkers.has(marker));
  }
  const marker = state.markers.get(id)?.[0];
  if (marker && openPopup) { marker.openPopup(); map.panTo(marker.getLatLng()); }
}
function basisLabel(value) {
  return ({ worksite: 'Job worksite', company_hq: 'Company headquarters · worksite unconfirmed', company_office: 'Company office · worksite unconfirmed' })[value] || 'Location unclear';
}
function locationLabel(item) {
  const sourceLocation = item.location_resolution?.source_location;
  return `${basisLabel(item.location_basis)}${sourceLocation ? ' · source: ' + sourceLocation : ''}`;
}
function jobCard(item) {
  const card = element('article', 'job-card'); card.dataset.id = item.id;
  const top = element('div', 'card-topline');
  top.append(element('span', `type-badge ${item.opportunity_type === 'internship' ? 'is-internship' : ''}`, labels[item.opportunity_type] || 'Not stated'));
  if (item.distance_miles != null) top.append(element('span', 'distance-badge', `${item.distance_miles.toFixed(1)} mi · ${item.location_basis === 'worksite' ? 'worksite' : 'company reference'}`));
  card.append(top, button(item.title, 'job-title', () => { selectItem(item.id); showDetails(item); }));
  if (item.archived) card.append(element('span', 'archive-badge', 'Historical record'));
  const cities = [...new Set(item.worksites.map((w) => w.city).filter(Boolean))].join(' / ');
  card.append(element('p', 'company-name', `${item.company}${cities ? ' · ' + cities + ', AZ' : ''}`));
  const facts = element('div', 'job-facts');
  facts.append(element('span', '', money(item)), element('span', '', labels[item.commitment]), element('span', '', labels[item.arrangement]));
  card.append(facts);
  for (const tag of item.schedule_tags) card.append(element('span', 'schedule-badge', `✓ ${tag.label}`));
  card.append(element('p', 'location-note', locationLabel(item)));
  const bottom = element('div', 'card-bottom');
  bottom.append(element('span', '', `Checked ${formatDate(item.source_checked_at)}`), button('View details ↗', 'details-button', () => { selectItem(item.id); showDetails(item); }));
  card.append(bottom);
  return card;
}
function showDetails(item) {
  const container = $('#job-detail'); container.replaceChildren();
  const title = element('h2', '', item.title); title.id = 'job-dialog-title';
  container.append(element('p', 'eyebrow', item.category), title, element('p', 'detail-company', item.company));
  if (item.archived) container.append(element('p', 'archive-warning', `Historical record: ${item.archive_reason}. Application links are disabled.`));
  container.append(element('p', 'location-note', item.location_note || locationLabel(item)));
  const grid = element('dl', 'detail-grid');
  for (const [label, value] of [['Opportunity', labels[item.opportunity_type]], ['Career stage', labels[item.career_stage]], ['Working hours', labels[item.commitment]], ['Work arrangement', labels[item.arrangement]], ['Pay', money(item)], ['Weekly hours', item.hours_per_week ?? 'Not stated'], ['Application deadline', formatDate(item.deadline)], ['Last source check', formatDate(item.source_checked_at)], ['Posted by source', formatDate(item.posting_date)], ['First found on the web', formatDate(item.first_found_at)]]) {
    const pair = element('div'); pair.append(element('dt', '', label), element('dd', '', String(value))); grid.append(pair);
  }
  container.append(grid);
  container.append(element('h3', '', 'Location evidence'));
  if (item.worksites.length) {
    for (const worksite of item.worksites) {
      container.append(element('p', '', `${basisLabel(worksite.basis)}: ${[worksite.address, worksite.city, worksite.state].filter(Boolean).join(', ')}`));
      const evidence = externalLink('Address source ↗', worksite.evidence_url); if (evidence) container.append(evidence);
    }
  } else container.append(element('p', '', 'Exact street address is unclear. You can still open and apply to this listing.'));
  container.append(element('h3', '', 'Schedule evidence'));
  if (item.schedule_tags.length) {
    for (const tag of item.schedule_tags) container.append(element('blockquote', 'source-quote', `${tag.label}: “${tag.evidence}”`));
  } else container.append(element('p', '', 'Schedule flexibility is not stated in the available evidence.'));
  if (item.program_term) container.append(element('h3', '', 'Program term'), element('p', '', item.program_term));
  if (item.requirements) container.append(element('h3', '', 'Requirements'), element('div', 'detail-description', item.requirements));
  container.append(element('h3', '', 'Listing details'), element('div', 'detail-description', item.description || 'Not stated'));
  if (!item.archived && item.application_method) container.append(element('h3', '', 'How to apply'), element('p', '', item.application_method));
  if (!item.archived && item.application_email) container.append(element('p', '', `Recruiting email: ${item.application_email}`));
  const actions = element('div', 'detail-actions');
  if (!item.archived) {
    const apply = externalLink(state.view === 'unlocated' ? 'Open original listing ↗' : 'Application details ↗', item.application_url, 'primary-button');
    const source = externalLink('View source', item.source_url);
    if (apply) actions.append(apply); if (source) actions.append(source);
    container.append(actions, element('p', 'filter-help', 'Confirm current requirements and availability with the employer before applying.'));
  }
  if (!$('#job-dialog').open) $('#job-dialog').showModal();
}
function emptyState(title, message, retry = false) {
  const box = element('div', `empty-state ${retry ? 'error-state' : ''}`);
  box.append(element('div', 'empty-symbol', retry ? '!' : '⌕'), element('h3', '', title), element('p', '', message));
  if (retry) box.append(button('Try again', 'secondary-button retry-button', () => loadResults()));
  else box.append(button('Reset filters', 'secondary-button', resetFilters));
  $('#results').replaceChildren(box);
}
function filterParams(offset = 0) {
  const params = new URLSearchParams();
  for (const [name, value] of new FormData($('#filters'))) if (value !== '' && !(name === 'radius' && state.view === 'unlocated')) params.append(name, value);
  if (!params.get('pay_period')) params.delete('min_pay');
  params.set('view', state.view); params.set('q', $('#search').value.trim()); params.set('sort', $('#sort').value); params.set('offset', offset); params.set('limit', 200);
  return params;
}
async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The service is temporarily unavailable.');
  return result;
}
async function loadResults(append = false) {
  const requestId = ++state.request;
  state.controller?.abort(); state.controller = new AbortController();
  const offset = append ? state.items.length : 0;
  $('#results').setAttribute('aria-busy', 'true'); $('#load-more').hidden = true;
  if (!append) {
    if (!state.items.length) $('#results').replaceChildren(element('div', 'loading-card'), element('div', 'loading-card'));
    $('#result-count').textContent = state.items.length ? 'Updating results…' : 'Finding opportunities…';
  }
  try {
    const data = await fetchJSON(`/api/opportunities?${filterParams(offset)}`, { signal: state.controller.signal });
    if (requestId !== state.request) return;
    state.items = append ? [...state.items, ...data.items] : data.items;
    state.features = append ? [...state.features, ...data.features.features] : data.features.features;
    state.total = data.total;
    $('#result-count').textContent = state.items.length < data.total ? `Showing ${state.items.length} of ${data.total} matches` : `${data.total} matching ${data.total === 1 ? 'opportunity' : 'opportunities'}`;
    if (state.items.length) $('#results').replaceChildren(...state.items.map(jobCard));
    else emptyState('No matching opportunities', 'Try a wider radius or fewer filters.');
    $('#load-more').hidden = !data.has_more;
    drawMap();
  } catch (error) {
    if (error.name === 'AbortError' || requestId !== state.request) return;
    if (state.items.length) {
      $('#result-count').textContent = 'Could not update. Your previous results are still shown; try again.';
    } else {
      $('#result-count').textContent = 'Unable to load';
      emptyState('Unable to load opportunities', error.message, true);
    }
  } finally {
    if (requestId === state.request) $('#results').setAttribute('aria-busy', 'false');
  }
}
function renderSources(meta) {
  $('#source-list').replaceChildren();
  const statusLabels = { ok: 'Connected', error: 'Connection failed', partial: 'Connected · some pages failed', pending: 'Not checked yet', imported: 'Historical research' };
  for (const source of meta.sources) {
    const card = element('article', 'source-card'); const top = element('div', 'source-top');
    top.append(element('h3', '', source.name), element('span', `source-status ${source.last_status}`, statusLabels[source.last_status] || source.last_status));
    card.append(top, element('p', '', source.last_message || 'Waiting for the first source check.'));
    if (source.last_success_at) card.append(element('small', '', `Last successful check: ${formatDateTime(source.last_success_at)}`));
    if (source.enabled) card.append(element('small', '', meta.automatic_refresh ? `Checks every ${source.interval_hours} ${Number(source.interval_hours) === 1 ? 'hour' : 'hours'} while running. Next due: ${formatDateTime(source.next_refresh_at)}.` : 'Automatic checks are disabled.'));
    $('#source-list').append(card);
  }
  $('#source-alert').hidden = !meta.sources.some((source) => ['error', 'partial'].includes(source.last_status));
  $('#refresh-button').disabled = meta.refreshing;
  $('#refresh-button').textContent = meta.refreshing ? 'Checking sources…' : 'Check sources now';
  $('#refresh-status').textContent = meta.refreshing ? 'You can keep exploring while checks run.' : 'New listings appear automatically. No manual review is required.';
}
function renderCategories(categories) {
  if (!Array.isArray(categories) || !categories.length) return;
  const signature = JSON.stringify(categories);
  if (signature === state.categorySignature) return;
  const selected = new Set([...$('#category-options').querySelectorAll('input:checked')].map((node) => node.value));
  const options = categories.map((category) => {
    const label = element('label'); const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.name = 'category'; checkbox.value = category.id;
    checkbox.checked = selected.has(category.id);
    label.append(checkbox, document.createTextNode(category.label)); return label;
  });
  $('#category-options').replaceChildren(...options);
  state.categorySignature = signature;
}
function renderMeta(meta) {
  $('#total-count').textContent = meta.counts.total_count; $('#current-count').textContent = meta.counts.current_count; $('#review-count').textContent = meta.counts.review_count;
  renderCategories(meta.categories);
  renderSources(meta);
  if (meta.refreshing) state.awaitingUpdates = true;
  if (state.awaitingUpdates) {
    $('#listing-updates').hidden = false;
    const message = meta.refreshing ? 'Checking sources in the background. Your list stays in place.' : 'Source checks finished. Load the latest jobs when you’re ready.';
    if ($('#listing-update-message').textContent !== message) $('#listing-update-message').textContent = message;
  }
}
async function loadMeta(initial = false) {
  try {
    const meta = await fetchJSON('/api/meta');
    renderMeta(meta);
    if (meta.refreshing && !state.poll) startPolling();
    return meta;
  } catch {
    $('#refresh-status').textContent = 'Source status is unavailable. Please try again.';
    if (initial && !$('#category-options').querySelector('input')) $('#category-options').replaceChildren(element('p', 'filter-help', 'Career areas are temporarily unavailable. Reopen Data sources to retry.'));
    return null;
  }
}
function startPolling() {
  if (state.poll) return;
  state.poll = setInterval(async () => {
    if (state.pollBusy) return;
    state.pollBusy = true;
    try {
      const meta = await loadMeta();
      if (meta && !meta.refreshing) { clearInterval(state.poll); state.poll = null; }
    } finally { state.pollBusy = false; }
  }, 10000);
}
function resetFilters() {
  $('#filters').reset(); $('#search').value = ''; $('#sort').value = 'newest'; $('#distance-value').textContent = '30 miles';
  loadResults();
}
let searchTimer;
$('#search-form').addEventListener('submit', (event) => { event.preventDefault(); clearTimeout(searchTimer); loadResults(); });
$('#search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadResults(), 300); });
$('#filters').addEventListener('submit', (event) => event.preventDefault());
$('#filters').addEventListener('change', () => {
  if ($('#sort').value === 'pay' && !$('#pay-period').value) $('#sort').value = 'newest';
  loadResults();
});
$('#distance').addEventListener('input', () => { $('#distance-value').textContent = `${$('#distance').value} miles`; radiusCircle?.setRadius(Number($('#distance').value) * 1609.344); });
$('#sort').addEventListener('change', () => {
  if ($('#sort').value === 'pay' && !$('#pay-period').value) {
    $('#pay-period').value = 'hour'; $('#pay-period').closest('details').open = true;
  }
  loadResults();
});
$('#reset-filters').addEventListener('click', resetFilters);
$('#fit-map').addEventListener('click', fitMap);
$('#load-more').addEventListener('click', () => loadResults(true));
$('#show-latest-jobs').addEventListener('click', () => {
  state.awaitingUpdates = false;
  $('#listing-updates').hidden = true;
  loadResults();
});
document.querySelectorAll('[data-view]').forEach((tab) => tab.addEventListener('click', () => {
  state.view = tab.dataset.view;
  document.querySelectorAll('[data-view]').forEach((node) => node.setAttribute('aria-pressed', String(node === tab)));
  $('#distance').disabled = state.view === 'unlocated'; $('#review-explanation').hidden = state.view !== 'unlocated';
  $('#radius-help').textContent = state.view === 'unlocated' ? 'Unclear locations stay available without a map point.' : 'Distance to the mapped address. Unclear locations remain listed.';
  loadResults();
}));
for (const name of ['sources', 'about']) $(`#${name}-button`).addEventListener('click', () => { $(`#${name}-dialog`).showModal(); if (name === 'sources') loadMeta(); });
document.querySelectorAll('.dialog-close').forEach((node) => node.addEventListener('click', () => node.closest('dialog').close()));
$('#refresh-button').addEventListener('click', async () => {
  $('#refresh-button').disabled = true; $('#refresh-status').textContent = 'Starting source checks…';
  try { await fetchJSON('/api/refresh', { method: 'POST', headers: { 'X-Hub-Request': '1' } }); await loadMeta(); startPolling(); }
  catch (error) { $('#refresh-status').textContent = error.message; $('#refresh-button').disabled = false; }
});
try { initializeMap(); }
catch (error) {
  console.error('Map initialization failed:', error);
  map = null;
  mapMessage('The map is unavailable. You can still search and open the listings.');
}
let initial;
try { initial = JSON.parse($('#initial-data')?.textContent || 'null'); }
catch (error) { console.error('Initial listing data could not be read:', error); }
if (initial?.data) {
  state.view = initial.data.view;
  state.items = initial.data.items;
  state.features = initial.data.features.features;
  state.total = initial.data.total;
  if (state.items.length) $('#results').replaceChildren(...state.items.map(jobCard));
  $('#load-more').hidden = !initial.data.has_more;
  if (initial.meta) renderMeta(initial.meta);
  drawMap();
  fitMap();
  $('#startup-status').hidden = true;
  loadMeta();
} else {
  Promise.all([loadMeta(true), loadResults()]).then(() => {
    fitMap();
    $('#startup-status').hidden = true;
  });
}
