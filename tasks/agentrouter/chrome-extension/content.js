// Only visible header/login controls are inspected. Never read tokens or account storage.
function visible(el) {
  return !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
}
function find(selector, pattern, root = document) {
  return [...root.querySelectorAll(selector)].find(el => visible(el) && pattern.test(el.innerText.trim()));
}
function header() {
  const nav = document.querySelector('nav');
  // Current Agent Router wraps navigation and account controls in a common header.
  return document.querySelector('header') || nav?.parentElement;
}
function profile() {
  return header() && find('button', /github_/i, header());
}
function github() {
  return find('button', /^(使用\s*GitHub\s*继续|Continue with GitHub|Sign in with GitHub)$/i);
}
function state() {
  return {logged_in: !!profile(), login_available: !!github(),
    console_page: location.pathname.startsWith('/console'),
    logout_available: !!find('[role="menuitem"],.semi-dropdown-item', /^(退出|退出登录|Log out|Logout|Sign out)$/i)};
}
chrome.runtime.onMessage.addListener((request, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return;
  try {
    let el;
    switch (request.action) {
      case 'state': reply(state()); return;
      case 'menu': el = profile(); break;
      case 'logout': el = find('[role="menuitem"],.semi-dropdown-item', /^(退出|退出登录|Log out|Logout|Sign out)$/i); break;
      case 'github_target':
        el = github();
        if (!el || el.disabled) { reply({found: false}); return; }
        el.scrollIntoView({block: 'center', inline: 'center'});
        const box = el.getBoundingClientRect();
        reply({found: true, x: box.x + box.width / 2, y: box.y + box.height / 2});
        return;
      default: reply({clicked: false}); return;
    }
    if (el && !el.disabled) {
      // The account dropdown opens on hover; click alone does not expose its menu.
      if (request.action === 'menu') {
        el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
        el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: false}));
      }
      el.click(); reply({clicked: true});
    }
    else reply({clicked: false});
  } catch { reply({clicked: false}); }
});
