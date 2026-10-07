// Shared by the module-based journal app and classic scripts on the standalone help page.
(() => {
// Follow the section at the reading edge below a sticky navigation bar.
function trackSections(nav, {scroller = window, content = document.body} = {}) {
  const sections = [...nav.querySelectorAll('a[href^="#"]')]
    .map(link => ({link, heading: document.getElementById(link.getAttribute('href').slice(1))}))
    .filter(section => section.heading);
  if (!sections.length) return () => {};
  const page = scroller === window;
  let frame = 0, current = null;

  function update() {
    frame = 0;
    const readingEdge = nav.getBoundingClientRect().bottom + 24;
    let active = sections[0];
    for (const section of sections) {
      if (section.heading.getBoundingClientRect().top <= readingEdge) active = section;
    }
    // A short final section may never reach the sticky bar, even at the bottom.
    const scrollBox = page ? document.scrollingElement : scroller;
    const height = page ? window.innerHeight : scroller.clientHeight;
    const bottom = page ? height : scroller.getBoundingClientRect().top + scroller.clientTop + height;
    if (scrollBox.scrollTop > 0 && scrollBox.scrollTop + height >= scrollBox.scrollHeight - 2) {
      for (const section of sections) {
        if (section.heading.getBoundingClientRect().top < bottom) active = section;
      }
    }
    if (current === active) return;
    current = active;
    for (const {link} of sections) {
      if (link === active.link) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }
    // Keep the selected help link visible inside the mobile horizontal bar.
    const strip = active.link.parentElement;
    if (strip.scrollWidth > strip.clientWidth) {
      const bounds = strip.getBoundingClientRect(), linkBounds = active.link.getBoundingClientRect();
      if (linkBounds.left < bounds.left) strip.scrollLeft += linkBounds.left - bounds.left;
      else if (linkBounds.right > bounds.right) strip.scrollLeft += linkBounds.right - bounds.right;
    }
  }
  function schedule() {
    if (!frame) frame = window.requestAnimationFrame(update);
  }
  scroller.addEventListener('scroll', schedule, {passive: true});
  window.addEventListener('resize', schedule);
  const observer = new ResizeObserver(schedule);
  observer.observe(nav);
  observer.observe(content);
  update();
  return () => {
    scroller.removeEventListener('scroll', schedule);
    window.removeEventListener('resize', schedule);
    observer.disconnect();
    if (frame) window.cancelAnimationFrame(frame);
  };
}
globalThis.OpindxSections = {trackSections};
})();
