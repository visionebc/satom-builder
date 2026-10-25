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

  // sidebar collapse, remembered per browser
  var KEY = 'satom-builder.sidebar-collapsed';
  function setCollapsed(on, persist) {
    var sb = document.getElementById('fw-sidebar');
    var main = document.getElementById('fw-main');
    var foot = document.querySelector('.sb-footer');
    if (!sb) { return; }
    sb.classList.toggle('collapsed', on);
    if (main) { main.style.marginLeft = on ? '0' : ''; }
    if (foot) { foot.style.marginLeft = on ? '0' : ''; }
    if (persist) { try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (x) { /* private mode */ } }
  }

  document.addEventListener('DOMContentLoaded', function () {
    var stored = false;
    try { stored = localStorage.getItem(KEY) === '1'; } catch (x) { stored = false; }
    setCollapsed(stored, false);
    var btn = document.getElementById('fw-sidebar-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        var sb = document.getElementById('fw-sidebar');
        setCollapsed(!(sb && sb.classList.contains('collapsed')), true);
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
