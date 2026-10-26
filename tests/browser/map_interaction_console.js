// Manual smoothness check for a real (e.g. Windows) Chrome: open the app, select the Korea OSM grid,
// paste this file into the DevTools console, then zoom and drag the map by hand for 10 seconds.
// Prints frame-interval percentiles, FPS and Long Tasks measured in the page. Nothing is sent anywhere.
(() => {
  const seconds = 10, frames = [], longTasks = [];
  const observer = new PerformanceObserver((list) => list.getEntries().forEach((e) => longTasks.push(e.duration)));
  observer.observe({ type: "longtask" });
  const start = performance.now();
  const loop = (t) => {
    frames.push(t);
    if (t - start < seconds * 1000) { requestAnimationFrame(loop); return; }
    observer.disconnect();
    const intervals = frames.slice(1).map((v, i) => v - frames[i]).sort((a, b) => a - b);
    const pct = (q) => intervals[Math.min(intervals.length - 1, Math.round(q * (intervals.length - 1)))].toFixed(1);
    console.table({
      fps: (1000 * intervals.length / (frames.at(-1) - frames[0])).toFixed(1),
      frame_p50_ms: pct(0.5), frame_p95_ms: pct(0.95), frame_max_ms: pct(1),
      frames_over_33ms: intervals.filter((v) => v > 33.4).length,
      long_tasks: longTasks.length, long_task_max_ms: Math.max(0, ...longTasks).toFixed(0),
      device_pixel_ratio: devicePixelRatio, viewport: `${innerWidth}x${innerHeight}`, user_agent: navigator.userAgent,
    });
  };
  console.log(`Recording for ${seconds} s: zoom with the wheel and drag the Korea map now.`);
  requestAnimationFrame(loop);
})();
