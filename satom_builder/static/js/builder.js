/* SATOM Builder: shared page behaviour (no inline scripts: CSP script-src 'self').
 *
 * window.SB.api(method, url, body) -> Promise<{ok, status, data}>
 *   JSON call to the internal API (contract section 11) with the session
 *   cookie and the X-CSRF-Token header from <meta name="csrf-token">.
 */
(function () {
  'use strict';

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') : '';
  }

  function api(method, url, body) {
    var opts = {method: method, credentials: 'same-origin',
                headers: {'X-CSRF-Token': csrf(), 'Accept': 'application/json'}};
    if (body instanceof FormData) {
      opts.body = body;
    } else if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      var ct = r.headers.get('Content-Type') || '';
      if (ct.indexOf('application/json') === -1) {
        return {ok: r.ok, status: r.status, data: null, response: r};
      }
      return r.json().then(function (j) { return {ok: r.ok, status: r.status, data: j}; },
                           function () { return {ok: false, status: r.status, data: null}; });
    }, function (err) {
      return {ok: false, status: 0, data: {error: 'network', message: String(err)}};
    });
  }

  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') { e.textContent = attrs[k]; }
      else if (k === 'class') { e.className = attrs[k]; }
      else { e.setAttribute(k, attrs[k]); }
    });
    (children || []).forEach(function (c) {
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return e;
  }

  // Sidebar. Desktop: collapse, remembered per browser. Phone (<= 768px, where
  // fortiweb.css hides the sidebar): an overlay opened by the menu button and
  // closed by a tap outside or Escape; 'collapsed' (width 0) is never used
  // there, or the overlay opens with no width (SATOM main.js does the same).
  var KEY = 'satom-builder.sidebar-collapsed';
  var PHONE_MAX = 768;
  function isPhone() { return window.innerWidth <= PHONE_MAX; }
  function parts() {
    return {btn: document.getElementById('fw-sidebar-toggle'), sb: document.getElementById('fw-sidebar'),
            main: document.getElementById('fw-main'), foot: document.querySelector('.sb-footer')};
  }
  function setCollapsed(p, on, persist) {
    if (!p.sb) { return; }
    p.sb.classList.toggle('collapsed', on);
    if (p.main) { p.main.style.marginLeft = on ? '0' : ''; }
    if (p.foot) { p.foot.style.marginLeft = on ? '0' : ''; }
    if (persist) { try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (x) { /* private mode */ } }
  }
  function setPhoneOpen(p, open) {
    if (!p.sb) { return; }
    p.sb.classList.remove('collapsed');
    p.sb.classList.toggle('mobile-open', open);
    if (p.main) { p.main.style.marginLeft = ''; }
    if (p.foot) { p.foot.style.marginLeft = ''; }
    if (p.btn) { p.btn.setAttribute('aria-expanded', open ? 'true' : 'false'); }
    document.body.classList.toggle('fw-sidebar-open', open);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var stored = false;
    try { stored = localStorage.getItem(KEY) === '1'; } catch (x) { stored = false; }
    var p = parts();
    if (isPhone()) { setPhoneOpen(p, false); } else { setCollapsed(p, stored, false); }
    if (p.btn && p.sb) {
      p.btn.setAttribute('aria-controls', 'fw-sidebar');
      document.addEventListener('click', function (e) {
        if (p.btn.contains(e.target)) {
          if (isPhone()) { setPhoneOpen(p, !p.sb.classList.contains('mobile-open')); }
          else { setCollapsed(p, !p.sb.classList.contains('collapsed'), true); }
        } else if (isPhone() && p.sb.classList.contains('mobile-open') && !p.sb.contains(e.target)) {
          setPhoneOpen(p, false);
        }
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && p.sb.classList.contains('mobile-open')) { setPhoneOpen(p, false); }
      });
      // rotating a tablet across the breakpoint must not leave a stale state
      window.addEventListener('resize', function () {
        if (!isPhone() && p.sb.classList.contains('mobile-open')) { setPhoneOpen(p, false); }
      });
    }
    // <form data-confirm="question"> asks before submitting
    document.querySelectorAll('form[data-confirm]').forEach(function (f) {
      f.addEventListener('submit', function (ev) {
        if (!window.confirm(f.getAttribute('data-confirm'))) { ev.preventDefault(); }
      });
    });
    // <details data-sb-toggle> nothing to do: native. <button data-sb-copy="#id">
    document.querySelectorAll('[data-sb-copy]').forEach(function (b) {
      b.addEventListener('click', function () {
        var src = document.querySelector(b.getAttribute('data-sb-copy'));
        if (src && navigator.clipboard) {
          navigator.clipboard.writeText(src.textContent.trim()).then(function () {
            b.textContent = 'Copied';
          });
        }
      });
    });
  });

  window.SB = {api: api, csrf: csrf, el: el};
})();
