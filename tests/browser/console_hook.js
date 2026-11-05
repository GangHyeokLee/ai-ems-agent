/* Injected before the page scripts by tests/test_console_browser.py.
 * Records page errors and makes Math.random and Date deterministic, so the original Artifact and
 * /console/ run the same demo code paths and can be compared value by value. */
(() => {
  window.__probeErrors = [];
  addEventListener('error', (event) => {
    window.__probeErrors.push(['error', String(event.message), String(event.filename || '').split('/').pop(), event.lineno]);
  });
  addEventListener('unhandledrejection', (event) => {
    window.__probeErrors.push(['rejection', String((event.reason && event.reason.stack) || event.reason).slice(0, 400)]);
  });
  for (const level of ['error', 'warn']) {
    const original = console[level];
    console[level] = (...args) => {
      window.__probeErrors.push([`console.${level}`, args.map(String).join(' ').slice(0, 300)]);
      original.apply(console, args);
    };
  }
  // Listener registrations are logged only once a probe sets window.__listenerLog = [] (no behaviour change).
  const addListener = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, listener, options) {
    if (Array.isArray(window.__listenerLog)) {
      const target = this === window ? 'window' : this === document ? 'document' : (this.id || String(this.tagName || this.constructor.name).toLowerCase());
      window.__listenerLog.push([target, type]);
    }
    return addListener.call(this, type, listener, options);
  };
  let seed = 20261030;
  Math.random = () => {  // mulberry32
    seed = (seed + 0x6D2B79F5) | 0;
    let t = seed;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const RealDate = Date;
  const start = RealDate.UTC(2026, 9, 30, 1, 0, 0);  // 10:00 KST
  const origin = performance.now();
  // Parity compares a single inline Artifact against separately loaded console scripts.
  // CPU/network loading time must not change timestamp-derived snapshot IDs. The probe
  // advances this wall clock at matching user actions; performance/rAF clocks stay real.
  const parity = new URLSearchParams(location.search).get('probe') === 'parity';
  let elapsed = 0;
  if (parity) {
    window.__probeClock = Object.freeze({
      now: () => start + elapsed,
      advance: (milliseconds) => {
        if (!Number.isFinite(milliseconds) || milliseconds < 0) throw new Error('Invalid parity clock step');
        elapsed += milliseconds;
        return start + elapsed;
      },
    });
  }
  const now = () => parity ? start + elapsed : start + Math.floor(performance.now() - origin);
  class FixedDate extends RealDate {
    constructor(...args) {
      if (args.length) super(...args);
      else super(now());
    }
    static now() { return now(); }
  }
  window.Date = FixedDate;
})();
