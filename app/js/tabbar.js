// Floating tab bar: Library, Stats, Settings. Hidden on the new-book flow.

import { h } from './util.js';
import { icon } from './icons.js';
import { currentPath } from './router.js';

const TABS = [
  { href: '#/library', label: 'Library', icon: 'books', match: /^\/(library|book)(\/|$)/ },
  { href: '#/stats', label: 'Stats', icon: 'stats', match: /^\/stats(\/|$)/ },
  { href: '#/settings', label: 'Settings', icon: 'settings', match: /^\/settings(\/|$)/ },
];

export function mountTabBar(root) {
  const links = TABS.map((t) => {
    const a = h('a', { class: 'tab', href: t.href }, icon(t.icon, 26), h('span', { class: 'tab-label', text: t.label }));
    a.addEventListener('click', (e) => {
      // Re-tapping the active tab scrolls back to the top, like iOS.
      if (a.getAttribute('aria-current') === 'page') {
        e.preventDefault();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    });
    return { t, a };
  });
  const nav = h('nav', { class: 'tabbar', 'aria-label': 'Main' }, links.map((l) => l.a));
  root.append(nav);

  function update() {
    const path = currentPath();
    const show = !/^\/new(\/|$)/.test(path);
    document.body.classList.toggle('has-tabbar', show);
    nav.hidden = !show;
    for (const { t, a } of links) {
      if (t.match.test(path)) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    }
  }
  window.addEventListener('hashchange', update);
  update();
  return { update };
}
