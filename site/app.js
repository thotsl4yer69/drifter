(() => {
  const header = document.querySelector('[data-header]');
  const nav = document.querySelector('[data-nav]');
  const toggle = document.querySelector('[data-nav-toggle]');

  const setNav = (open) => {
    if (!nav || !toggle) return;
    nav.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
    document.body.classList.toggle('nav-open', open);
  };

  toggle?.addEventListener('click', () => setNav(!nav.classList.contains('open')));
  nav?.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => setNav(false)));

  const onScroll = () => header?.classList.toggle('scrolled', window.scrollY > 18);
  onScroll();
  window.addEventListener('scroll', onScroll, { passive: true });

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const reveals = document.querySelectorAll('.reveal');
  if (reducedMotion || !('IntersectionObserver' in window)) {
    reveals.forEach((node) => node.classList.add('visible'));
  } else {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.13 });
    reveals.forEach((node) => observer.observe(node));
  }

  const demo = {
    adapter: {
      title: 'ONLINE',
      copy: 'The transport is reachable. DRIFTER keeps adapter state separate from ECU response and live PID flow.'
    },
    ecu: {
      title: 'WAITING',
      copy: 'The adapter can answer, but the vehicle ECU has not yet proved a standard OBD-II response.'
    },
    pid: {
      title: 'FLOWING',
      copy: 'Live standard PID traffic is present. Unsupported values should degrade explicitly instead of being fabricated.'
    }
  };

  const demoButtons = document.querySelectorAll('[data-demo-state]');
  const title = document.querySelector('[data-demo-title]');
  const copy = document.querySelector('[data-demo-copy]');

  demoButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const state = button.dataset.demoState;
      const content = demo[state];
      if (!content) return;
      demoButtons.forEach((item) => item.classList.toggle('active', item === button));
      if (title) title.textContent = content.title;
      if (copy) copy.textContent = content.copy;
    });
  });
})();