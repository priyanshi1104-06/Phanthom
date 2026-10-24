// Phantom website: the intro, sections that glide in as you scroll, and the pointer light. The page is
// complete without this file (no JavaScript, or "reduce motion" set): it only adds movement.
(() => {
  const root = document.documentElement;
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  root.classList.add('js');

  // The intro plays once per visit; a click, a key or a scroll skips it.
  const intro = document.querySelector('.intro');
  const ready = () => {
    root.classList.add('ready');
    if (intro) {
      intro.remove();
    }
  };
  if (!intro) {
    ready();
  } else {
    let seen = false;
    try {
      seen = sessionStorage.getItem('phantom-intro') === 'seen';
      sessionStorage.setItem('phantom-intro', 'seen');
    } catch {
      // storage blocked: just play it
    }
    if (seen || still) {
      ready();
    } else {
      intro.addEventListener('animationend', (event) => {
        if (event.target === intro) {
          ready();
        }
      });
      const skip = () => intro.classList.add('skip');
      for (const type of ['click', 'keydown', 'wheel', 'touchstart']) {
        addEventListener(type, skip, { once: true, passive: true });
      }
    }
  }

  // Sections glide in as they enter the screen.
  const items = document.querySelectorAll('.reveal');
  if (still || !('IntersectionObserver' in window)) {
    items.forEach((item) => item.classList.add('in'));
  } else {
    const watch = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add('in');
          watch.unobserve(entry.target);
        }
      }
    }, { rootMargin: '0px 0px -12% 0px' });
    items.forEach((item) => watch.observe(item));
  }

  // A soft light follows the pointer.
  // The demo recording: with "reduce motion" set it waits, with controls, until it's asked to play.
  const demo = document.querySelector('.demo video');
  if (demo && still) {
    demo.pause();
    demo.removeAttribute('autoplay');
    demo.controls = true;
  }

  if (!still && matchMedia('(pointer: fine)').matches) {
    let frame = 0;
    let x = 0;
    let y = 0;
    addEventListener('pointermove', (event) => {
      x = event.clientX;
      y = event.clientY;
      if (!frame) {
        frame = requestAnimationFrame(() => {
          frame = 0;
          root.style.setProperty('--mx', `${x}px`);
          root.style.setProperty('--my', `${y}px`);
        });
      }
    }, { passive: true });
  }
})();
