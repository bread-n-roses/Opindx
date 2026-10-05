import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../site/section-nav.js', import.meta.url), 'utf8');
for (const page of [false, true]) {
  const events = () => ({listeners: new Map(),
    addEventListener(type, fn) { this.listeners.set(type, fn); },
    removeEventListener(type, fn) { if (this.listeners.get(type) === fn) this.listeners.delete(type); },
    emit(type) { this.listeners.get(type)?.(); },
  });
  let pending, cancelled = false, observer, resizeBottom = 60;
  const box = Object.assign(events(), {scrollTop: 0, scrollHeight: 1800, clientHeight: 500, clientTop: 0,
    getBoundingClientRect: () => ({top: 0})});
  const win = Object.assign(events(), {innerHeight: 500,
    requestAnimationFrame(fn) { pending = fn; return 1; },
    cancelAnimationFrame() { cancelled = true; pending = null; },
  });
  const strip = {scrollLeft: 0, scrollWidth: 300, clientWidth: 180, getBoundingClientRect: () => ({left: 0, right: 180})};
  const positions = [80, 600, 1650];
  const headings = positions.map((_, i) => ({getBoundingClientRect: () => ({top: positions[i] - box.scrollTop})}));
  const links = headings.map((_, i) => ({parentElement: strip, attrs: {href: `#s${i}`},
    getAttribute(name) { return this.attrs[name]; },
    setAttribute(name, value) { this.attrs[name] = value; },
    removeAttribute(name) { delete this.attrs[name]; },
    getBoundingClientRect() { return {left: i * 100 - strip.scrollLeft, right: (i + 1) * 100 - strip.scrollLeft}; },
  }));
  const nav = {querySelectorAll: () => links, getBoundingClientRect: () => ({bottom: resizeBottom})};
  const content = {};
  const doc = {body: content, scrollingElement: box, getElementById: id => headings[Number(id.slice(1))]};
  const context = {window: win, document: doc, ResizeObserver: class {
    constructor(fn) { this.fn = fn; this.observed = []; observer = this; }
    observe(target) { this.observed.push(target); }
    disconnect() { this.disconnected = true; }
  }};
  vm.createContext(context);
  vm.runInContext(source, context);
  const scroller = page ? win : box;
  const stop = context.OpindxSections.trackSections(nav, {scroller, content});
  const active = () => links.flatMap((link, i) => link.attrs['aria-current'] === 'location' ? [i] : []);
  const flush = () => { const fn = pending; pending = null; fn?.(); };
  const scroll = top => { box.scrollTop = top; scroller.emit('scroll'); flush(); };
  assert.deepEqual(active(), [0]);
  scroll(540); assert.deepEqual(active(), [1]);
  scroll(0); assert.deepEqual(active(), [0]);
  // The last short section is highlighted at the bottom even below the reading edge.
  scroll(1300); assert.deepEqual(active(), [2]);
  assert.equal(strip.scrollLeft, 120);
  scroll(0); assert.deepEqual(active(), [0]); assert.equal(strip.scrollLeft, 0);
  // Content loading and a wrapped navigation bar both change the reading edge.
  positions[1] = 300;
  scroll(200); assert.deepEqual(active(), [0]);
  resizeBottom = 90;
  win.emit('resize'); flush(); assert.deepEqual(active(), [1]);
  positions[1] = 700;
  observer.fn(); flush(); assert.deepEqual(active(), [0]);
  assert.deepEqual(observer.observed, [nav, content]);
  scroller.emit('scroll');
  stop();
  assert.equal(cancelled, true);
  assert.equal(observer.disconnected, true);
  assert.equal(scroller.listeners.has('scroll'), false);
  assert.equal(win.listeners.has('resize'), false);
  console.log(`PASS Section highlight follows ${page ? 'page' : 'dialog'} scrolling, layout changes, mobile links and cleanup`);
}
