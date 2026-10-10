'use strict';
// Offline event/race checks, not a browser substitute. No network or serial API exists here.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');

class Element {
  constructor(id = '') {
    this.id = id; this.listeners = {}; this.children = []; this.options = [];
    this.dataset = {}; this.style = {}; this.attributes = {}; this.value = '';
    this.validity = {valid: true}; this.checked = false; this.hidden = false;
    this.disabled = false; this.textContent = ''; this.capture = new Set();
    const classes = new Set();
    this.classList = {contains: name => classes.has(name), toggle: (name, on) => {
      const enabled = on === undefined ? !classes.has(name) : on;
      if (enabled) classes.add(name); else classes.delete(name);
      return enabled;
    }};
  }
  addEventListener(name, fn) {(this.listeners[name] ||= []).push(fn);}
  fire(name, event = {}) {
    const e = {button: 0, buttons: 1, isPrimary: true, pointerId: 7,
      preventDefault() {}, ...event};
    return Promise.all((this.listeners[name] || []).map(fn => fn(e)));
  }
  setAttribute(name, value) {this.attributes[name] = value;}
  querySelectorAll() {return this.children;}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children = children; this.options = children;}
  get firstChild() {return this.children[0];}
  reportValidity() {return this.validity.valid;}
  setPointerCapture(id) {this.capture.add(id);}
  hasPointerCapture(id) {return this.capture.has(id);}
  releasePointerCapture(id) {this.capture.delete(id); this.fire('lostpointercapture', {pointerId: id});}
  getBoundingClientRect() {return {left: 0, top: 0, width: 238, height: 238};}
}

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const settle = async () => {for (let i = 0; i < 40; i++) await Promise.resolve();};

async function setup() {
  const nodes = new Map();
  const get = id => {if (!nodes.has(id)) nodes.set(id, new Element(id)); return nodes.get(id);};
  get('xy-feed').value = '300'; get('z-feed').value = '60';
  const jogs = ['X', 'Y', 'Z'].flatMap(axis => [1, -1].map(dir => {
    const el = get(`${axis}${dir}`); el.dataset = {axis, dir: String(dir)}; return el;
  }));
  const document = new Element();
  document.hidden = false; document.body = new Element();
  document.getElementById = get;
  document.createElement = tag => new Element(tag);
  document.querySelectorAll = selector => selector === '.jog' ? jogs : [];
  document.querySelector = selector => selector.startsWith('meta') ? {content: 'test-token'} : get(selector);
  const window = new Element();
  let uuid = 0, now = 100;
  const intervals = [], timeouts = new Map(), requests = [], beacons = [], waiting = new Map();
  const remote = {connected: true, ready: true, fresh: true, armed: false, owner: '',
    state: 'Idle', busy: false, simulation: true, device: 'SIMULADOR',
    control_epoch: 'test-epoch', holding: false, hold_gesture: '', settings: {'13': '0', '20': '0', '21': '0'},
    position: [0, 0, 0], message: '', logs: [], feed: 0, rpm: 0, pins: ''};
  const failures = new Map();
  const context = vm.createContext({document, window, performance: {now: () => now},
    crypto: {randomUUID: () => `test-uuid-${++uuid}`}, Blob, URL,
    AbortSignal: {timeout: () => undefined},
    navigator: {sendBeacon: (url, body) => {beacons.push({url, body}); return true;}},
    setInterval: (fn, ms) => {intervals.push({fn, ms}); return intervals.length;},
    setTimeout: (fn, ms) => {const id = Symbol(); timeouts.set(id, {fn, ms}); return id;},
    clearTimeout: id => timeouts.delete(id),
    fetch: async (url, options) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      requests.push({url, body});
      if (waiting.has(url)) return await waiting.get(url).promise;
      if (failures.has(url)) return response({error: failures.get(url)}, 503);
      if (url === '/api/ports') return response([{device: 'SIMULADOR'}]);
      if (url === '/api/state') return response(JSON.parse(JSON.stringify(remote)));
      if (url === '/api/cancel') {remote.armed = false; remote.holding = false; remote.busy = false;}
      if (url === '/api/hold/start') {remote.holding = true; remote.hold_gesture = body.gesture;}
      if (url === '/api/hold/stop') {remote.holding = false; remote.busy = false;}
      return response({ok: true, command: {ignored: false}});
    }});
  vm.runInContext(source, context, {filename: 'app.js'});
  await settle();
  remote.owner = vm.runInContext('client', context); remote.armed = true;
  context.fixture = remote;
  vm.runInContext("state = JSON.parse(JSON.stringify(fixture)); movementMode = 'hold'; safetyBlocked = false; lastStateRequest = 100; render();", context);
  const evaluate = code => vm.runInContext(code, context);
  return {get, window, document, remote, requests, beacons, failures, evaluate,
    tick: async ms => {now += ms; for (const item of intervals.filter(item => item.ms === ms)) await item.fn(); await settle();},
    pause: url => {
      let resolve;
      const promise = new Promise(done => resolve = done);
      waiting.set(url, {promise, resolve});
    },
    reply: (url, data = {ok: true, command: {ignored: false}}) => {
      const pending = waiting.get(url); waiting.delete(url); pending.resolve(response(data));
    },
    matching: url => requests.filter(request => request.url === url)};
}

test('release before start reply sends higher-sequence stop and cannot resume', async () => {
  const h = await setup(); h.pause('/api/hold/start');
  await h.get('X1').fire('pointerdown');
  const start = h.matching('/api/hold/start')[0].body;
  await h.document.fire('pointerup'); await settle();
  const stop = h.matching('/api/hold/stop')[0].body;
  assert.equal(stop.gesture, start.gesture); assert.ok(stop.seq > start.seq);
  h.reply('/api/hold/start'); await settle(); await h.tick(150);
  assert.equal(h.matching('/api/hold/pulse').length, 0);
  assert.equal(h.evaluate('activePress'), null);
});

test('lost pointer capture stops the held gesture exactly once', async () => {
  const h = await setup(); await h.get('Z1').fire('pointerdown'); await settle();
  await h.get('Z1').fire('lostpointercapture'); await settle();
  await h.document.fire('pointerup');
  assert.equal(h.matching('/api/hold/stop').length, 1);
  assert.equal(h.evaluate('activePress'), null);
});

test('joystick is inert at center and center return latches until full release', async () => {
  const h = await setup(), joystick = h.get('joystick');
  await joystick.fire('pointerdown', {clientX: 119, clientY: 119});
  assert.equal(h.matching('/api/hold/start').length, 0);
  await joystick.fire('pointermove', {clientX: 205, clientY: 110}); await settle();
  assert.equal(h.matching('/api/hold/start')[0].body.axis, 'X');
  await joystick.fire('pointermove', {clientX: 119, clientY: 119}); await settle();
  assert.equal(h.evaluate('activePress.latched'), true);
  assert.equal(h.matching('/api/hold/stop').length, 1);
  await joystick.fire('pointermove', {clientX: 119, clientY: 20}); await h.tick(150);
  assert.equal(h.matching('/api/hold/start').length, 1);
  assert.equal(h.matching('/api/hold/pulse').length, 0);
  await h.document.fire('pointerup');
  assert.equal(h.evaluate('activePress'), null);
});

test('window blur sends stop and global cancel, disarms locally, and ends pulses', async () => {
  const h = await setup(); await h.get('Y1').fire('pointerdown'); await settle();
  await h.window.fire('blur'); await h.tick(150);
  assert.deepEqual(h.beacons.map(item => item.url), ['/api/hold/stop', '/api/cancel']);
  const stop = JSON.parse(await h.beacons[0].body.text());
  assert.ok(stop.seq > h.matching('/api/hold/start')[0].body.seq);
  assert.equal(h.evaluate('safetyBlocked'), true);
  assert.equal(h.evaluate('state.armed'), false);
  assert.equal(h.matching('/api/hold/pulse').length, 0);
});

test('failed cancel stays blocked despite a fresh server report saying armed', async () => {
  const h = await setup(); h.failures.set('/api/cancel', 'simulated loss');
  assert.equal(await h.evaluate('cancel()'), false);
  await h.evaluate('poll()'); await settle();
  assert.equal(h.evaluate('state.armed'), true);
  assert.equal(h.evaluate('safetyBlocked'), true);
  assert.equal(h.evaluate('controlsReady()'), false);
  assert.equal(h.get('arm').textContent, 'Confirmar bloqueio');
  await h.tick(450);
  assert.equal(h.matching('/api/heartbeat').length, 0);
});

test('mode change is not committed when cancellation cannot be confirmed', async () => {
  const h = await setup(); h.failures.set('/api/cancel', 'simulated loss');
  await h.evaluate("changeMode('step')");
  assert.equal(h.evaluate('movementMode'), 'hold');
  assert.equal(h.evaluate('modeChanging'), false);
  assert.equal(h.evaluate('safetyBlocked'), true);
});

test('late successful arm reply after blur cannot clear local safety block', async () => {
  const h = await setup(); h.remote.armed = false;
  h.evaluate('state.armed = false; safetyBlocked = true;');
  h.get('confirm').checked = true; h.pause('/api/arm');
  const armDone = h.get('arm').fire('click'); await settle();
  await h.window.fire('blur');
  h.reply('/api/arm'); await armDone; await settle();
  assert.equal(h.evaluate('safetyBlocked'), true);
  assert.equal(h.evaluate('controlsReady()'), false);
  assert.ok(h.matching('/api/cancel').length >= 1);
});

test('held keyboard key repeats do not create a queue and keyup stops', async () => {
  const h = await setup();
  await h.get('X-1').fire('keydown', {key: ' ', repeat: false}); await settle();
  await h.get('X-1').fire('keydown', {key: ' ', repeat: true});
  await h.get('Y1').fire('pointerdown');
  assert.equal(h.matching('/api/hold/start').length, 1);
  await h.document.fire('keyup', {key: ' '}); await settle();
  assert.equal(h.matching('/api/hold/stop').length, 1);
  assert.equal(h.evaluate('activePress'), null);
});
