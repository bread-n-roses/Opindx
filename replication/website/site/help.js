// Keep anchor headings clear of the sticky navigation at every screen width.
const nav = document.getElementById('help-sections');
const updateOffset = () => document.documentElement.style.setProperty('--help-nav-height', `${nav.getBoundingClientRect().height}px`);
updateOffset();
new ResizeObserver(updateOffset).observe(nav);
globalThis.OpindxSections.trackSections(nav);
document.getElementById('close-help').addEventListener('click', () => {
  window.close();
  // Browsers can refuse to close a tab that was opened manually.
  setTimeout(() => { if (!window.closed) window.location.assign('index.html'); }, 150);
});
