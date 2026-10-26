/* Icon picker: every <input data-icon-picker> gets a preview, a button and a
 * searchable grid of the Bootstrap icons a plugin may use (GET
 * /projects/icons.json, the vendored font's names filtered by SATOM's icon
 * pattern). Picking writes the name into the input and fires "input", so the
 * editor's own listeners see it. The input stays editable by hand. */
(function () {
  'use strict';
  var MAX_SHOWN = 360;
  var listPromise = null;
  var pickers = [];       // each picker's preview refresh: a page script may set the value
  window.addEventListener('load', function () { pickers.forEach(function (f) { f(); }); });
  function icons() {
    if (!listPromise) {
      listPromise = fetch('/projects/icons.json', {credentials: 'same-origin'})
        .then(function (r) { return r.ok ? r.json() : []; })
        .catch(function () { return []; });
    }
    return listPromise;
  }
  function make(tag, cls, attrs) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  function enhance(input) {
    if (input.getAttribute('data-icon-picker-ready')) { return; }
    input.setAttribute('data-icon-picker-ready', '1');
    var wrap = make('div', 'input-group input-group-sm sb-icon-picker');
    var preview = make('span', 'input-group-text', {'aria-hidden': 'true'});
    var glyph = make('i', 'bi ' + (input.value || 'bi-puzzle'));
    preview.appendChild(glyph);
    var btn = make('button', 'btn btn-outline-secondary', {type: 'button', 'aria-expanded': 'false',
                                                         'aria-label': 'Pick an icon'});
    btn.innerHTML = '<i class="bi bi-grid-3x3-gap"></i>';
    input.parentNode.insertBefore(wrap, input);
    input.classList.remove('form-control-sm');
    wrap.appendChild(preview);
    wrap.appendChild(input);
    wrap.appendChild(btn);
    var panel = make('div', 'sb-icon-panel', {role: 'dialog', 'aria-label': 'Icons'});
    panel.hidden = true;
    var search = make('input', 'form-control form-control-sm mb-2',
                      {type: 'search', placeholder: 'Search icons (e.g. shield, globe, table)',
                       'aria-label': 'Search icons'});
    var grid = make('div', 'sb-icon-grid', {role: 'listbox', 'aria-label': 'Icons'});
    var note = make('div', 'sb-help mt-1');
    panel.appendChild(search);
    panel.appendChild(grid);
    panel.appendChild(note);
    wrap.parentNode.insertBefore(panel, wrap.nextSibling);

    function syncPreview() { glyph.className = 'bi ' + (/^bi-[a-z0-9-]+$/.test(input.value) ? input.value : 'bi-question-square'); }
    function render(all) {
      var q = search.value.trim().toLowerCase().replace(/^bi-/, '');
      var hits = q ? all.filter(function (n) { return n.indexOf(q) !== -1; }) : all;
      grid.innerHTML = '';
      hits.slice(0, MAX_SHOWN).forEach(function (n) {
        var b = make('button', 'sb-icon-cell' + (n === input.value ? ' is-selected' : ''),
                     {type: 'button', role: 'option', title: n, 'aria-label': n, 'data-icon': n,
                      'aria-selected': n === input.value ? 'true' : 'false'});
        b.appendChild(make('i', 'bi ' + n));
        b.addEventListener('click', function () {
          input.value = n;
          input.dispatchEvent(new Event('input', {bubbles: true}));
          input.dispatchEvent(new Event('change', {bubbles: true}));
          syncPreview();
          close();
          input.focus();
        });
        grid.appendChild(b);
      });
      note.textContent = hits.length > MAX_SHOWN
        ? hits.length + ' icons match: the first ' + MAX_SHOWN + ' are shown, type to narrow.'
        : hits.length + ' icon(s).';
    }
    function open() {
      syncPreview();
      panel.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      icons().then(function (all) { render(all); search.focus(); });
    }
    function close() { panel.hidden = true; btn.setAttribute('aria-expanded', 'false'); }
    btn.addEventListener('click', function () { if (panel.hidden) { open(); } else { close(); } });
    search.addEventListener('input', function () { icons().then(render); });
    panel.addEventListener('keydown', function (e) { if (e.key === 'Escape') { close(); btn.focus(); } });
    input.addEventListener('input', syncPreview);
    input.addEventListener('change', syncPreview);
    input.addEventListener('focus', syncPreview);
    pickers.push(syncPreview);
    document.addEventListener('click', function (e) {
      if (!panel.hidden && !panel.contains(e.target) && !wrap.contains(e.target)) { close(); }
    });
  }

  function init(root) {
    (root || document).querySelectorAll('input[data-icon-picker]').forEach(enhance);
  }
  window.SBIconPicker = {init: init};
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', function () { init(); }); }
  else { init(); }
})();
