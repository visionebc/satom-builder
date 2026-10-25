/* AI profiles page (admin/ai.html): "Start from" fills the form from a preset
 * (profiles.PRESETS), and the Anthropic-only fields (effort, refusal
 * fallbacks) hide for an OpenAI-compatible profile. Nothing is saved here. */
(function () {
  'use strict';
  var PLACEHOLDER = 'your-ai-server';

  function byId(id) { return document.getElementById(id); }

  function showAnthropicOnly() {
    var adapter = byId('a-adapter');
    if (!adapter) { return; }
    var on = adapter.value === 'anthropic';
    document.querySelectorAll('.sb-anthropic-only').forEach(function (el) { el.hidden = !on; });
    var url = byId('a-url');
    if (url) {
      url.placeholder = on ? 'empty = https://api.anthropic.com' : 'http://your-ai-server:11434/v1';
    }
  }

  function applyPreset(opt) {
    var d = opt.dataset;
    byId('a-adapter').value = d.adapter;
    byId('a-url').value = d.url;
    byId('a-model').value = d.model;
    byId('a-max').value = d.max;
    var name = byId('a-name');
    if (!name.value.trim() && d.name) { name.value = d.name; }
    showAnthropicOnly();
    var url = byId('a-url');
    var at = d.url.indexOf(PLACEHOLDER);
    if (at >= 0) {
      // the next keystrokes replace the placeholder host
      url.focus();
      url.setSelectionRange(at, at + PLACEHOLDER.length);
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    var adapter = byId('a-adapter');
    if (!adapter) { return; }
    adapter.addEventListener('change', function () {
      var model = byId('a-model');
      // the Anthropic default model means nothing to another server
      if (adapter.value !== 'anthropic' && model.value === 'claude-opus-5-5') { model.value = ''; }
      showAnthropicOnly();
    });
    var preset = byId('a-preset');
    if (preset) {
      preset.addEventListener('change', function () {
        var opt = preset.options[preset.selectedIndex];
        if (opt && opt.value) { applyPreset(opt); }
      });
    }
    showAnthropicOnly();
  });
})();
