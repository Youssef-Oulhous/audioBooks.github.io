// Hash router: #/library, #/new, #/new/<id>/chapters, #/new/<id>/voice, #/book/<id>, #/settings

import { reducedMotion } from './util.js';

const stack = []; // our view of the history stack (hashes)
let replacing = false;
let routes = [];
let outlet = null;
let unmount = null;
const scrollMemory = new Map();

export function currentPath() {
  return (location.hash || '').replace(/^#/, '') || '/library';
}

export function navigate(hash, { replace = false } = {}) {
  if (!hash.startsWith('#')) hash = '#' + hash;
  if (location.hash === hash) {
    render();
    return;
  }
  if (replace) {
    replacing = true;
    location.replace(hash);
  } else {
    location.hash = hash;
  }
}

/** In-app back button: use real history when it leads to the same place, else go up. */
export function goBack(fallback) {
  if (stack.length >= 2 && stack[stack.length - 2] === fallback) history.back();
  else navigate(fallback, { replace: true });
}

function track() {
  const h = location.hash || '#/library';
  if (replacing) {
    replacing = false;
    if (stack.length) stack[stack.length - 1] = h;
    else stack.push(h);
  } else if (stack.length >= 2 && stack[stack.length - 2] === h) {
    stack.pop();
  } else {
    stack.push(h);
  }
  if (stack.length > 50) stack.splice(0, stack.length - 50);
}

function match(path) {
  for (const r of routes) {
    const m = r.re.exec(path);
    if (m) return { route: r, params: r.params ? r.params(m) : {} };
  }
  return null;
}

let lastPath = null;

function render() {
  const path = currentPath();
  const found = match(path);
  if (!found) {
    navigate('#/library', { replace: true });
    return;
  }
  if (lastPath) scrollMemory.set(lastPath, window.scrollY);
  if (unmount) {
    try {
      unmount();
    } catch (err) {
      console.error(err);
    }
    unmount = null;
  }
  outlet.replaceChildren();
  outlet.className = 'view view-' + found.route.name;
  document.body.dataset.route = found.route.name;
  document.documentElement.classList.remove('scrolled');
  unmount = found.route.view.mount(outlet, found.params) || null;
  const remembered = found.route.keepScroll ? scrollMemory.get(path) : 0;
  window.scrollTo(0, remembered || 0);
  if (!reducedMotion()) {
    outlet.classList.remove('enter');
    void outlet.offsetWidth;
    outlet.classList.add('enter');
  }
  lastPath = path;
}

export function startRouter(routeList, el) {
  routes = routeList;
  outlet = el;
  window.addEventListener('hashchange', () => {
    track();
    render();
  });
  if (!location.hash) {
    replacing = true;
    history.replaceState(history.state, '', location.pathname + location.search + '#/library');
  }
  track();
  render();
}
