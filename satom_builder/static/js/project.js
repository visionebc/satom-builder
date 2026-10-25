/* Project overview: validate, rehearse, import, generate with AI.
 * Talks only to the internal JSON API (contract section 11) via SB.api. */
(function () {
  'use strict';
  var root = document.getElementById('sb-project');
  if (!root) { return; }
  var pid = root.getAttribute('data-pid');
  var base = '/api/projects/' + pid;
  var el = SB.el;
  var needsSecrets = [];
  var lastJobProject = null;

  function $(id) { return document.getElementById(id); }

  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

  function busy(btn, on) {
    if (!btn) { return; }
    btn.disabled = on;
    if (on) { btn.setAttribute('data-label', btn.innerHTML); btn.textContent = 'Working...'; }
    else if (btn.getAttribute('data-label')) { btn.innerHTML = btn.getAttribute('data-label'); }
  }

  function problemList(list) {
    var ul = el('ul', {'class': 'sb-problems mb-0'});
    (list || []).forEach(function (p) {
      ul.appendChild(el('li', {}, [el('span', {'class': 'sb-where', text: p.where || ''}),
                                   ' ' + (p.message || '')]));
    });
    return ul;
  }

  function showError(target, data, status) {
    clear(target);
    var msg = (data && data.message) ? data.message : ('Request failed (HTTP ' + status + ').');
    target.appendChild(el('div', {'class': 'fw-alert fw-alert-danger', text: msg}));
  }

  function badge(ok, text) {
    return el('span', {'class': 'fw-badge ' + (ok ? 'fw-badge-success' : 'fw-badge-danger'),
                       text: text || (ok ? 'ok' : 'problems')});
  }

  function renderSecrets() {
    var box = $('sb-secrets');
    if (!box) { return; }
    clear(box);
    if (!needsSecrets.length) {
      box.appendChild(el('p', {'class': 'sb-help mb-1',
        text: 'The last validation reported no connection that needs a secret.'}));
      return;
    }
    box.appendChild(el('p', {'class': 'sb-help mb-1',
      text: 'Connections this import creates that need a secret (stored by SATOM only):'}));
    needsSecrets.forEach(function (slug) {
      var id = 'sb-secret-' + slug;
      var row = el('div', {'class': 'input-group input-group-sm mb-1'}, [
        el('span', {'class': 'input-group-text sb-mono', text: slug}),
        el('input', {'class': 'form-control', type: 'password', id: id, autocomplete: 'off',
                     'data-slug': slug, placeholder: 'secret for ' + slug})]);
      box.appendChild(row);
    });
  }

  function renderResult(title, data) {
    var out = $('sb-result');
    clear(out);
    var head = el('div', {'class': 'mb-1'}, [el('strong', {text: title + ': '})]);
    if (data && typeof data.ok === 'boolean') { head.appendChild(badge(data.ok)); }
    if (data && data.status) { head.appendChild(el('span', {'class': 'ms-2 sb-mono', text: 'status ' + data.status})); }
    out.appendChild(head);
    if (data && data.problems && data.problems.length) { out.appendChild(problemList(data.problems)); }
    if (data && data.warnings && data.warnings.length) {
      out.appendChild(el('div', {'class': 'sb-help mt-2', text: 'Warnings'}));
      out.appendChild(problemList(data.warnings));
    }
    if (data && data.summary) {
      var s = data.summary;
      if (Array.isArray(s.needs_secrets)) { needsSecrets = s.needs_secrets.slice(); renderSecrets(); }
      var line = [];
      if (s.lands_in) { line.push('lands in ' + s.lands_in); }
      if (s.signed_by) { line.push('signed by ' + s.signed_by); }
      if (s.creates && s.creates.length) { line.push('creates ' + s.creates.join(', ')); }
      if (s.keeps && s.keeps.length) { line.push('keeps ' + s.keeps.join(', ')); }
      if (line.length) { out.appendChild(el('p', {'class': 'sb-help mt-2 mb-1', text: line.join(' · ')})); }
    }
    var det = el('details', {'class': 'mt-2'}, [el('summary', {'class': 'sb-help', text: 'Full answer'}),
      el('pre', {'class': 'sb-pre', text: JSON.stringify(data, null, 1)})]);
    out.appendChild(det);
  }

  function run(btn, method, url, body, title) {
    busy(btn, true);
    return SB.api(method, url, body).then(function (r) {
      busy(btn, false);
      if (!r.ok) { showError($('sb-result'), r.data, r.status); return; }
      renderResult(title, r.data);
    });
  }

  var actions = {
    'check': function (b) { return run(b, 'POST', base + '/check', {}, 'Local validation'); },
    'validate-remote': function (b) { return run(b, 'POST', base + '/validate-remote', {}, 'Validation on SATOM'); },
    'rehearse': function (b) {
      var inputs;
      try { inputs = JSON.parse($('sb-inputs').value || '{}'); }
      catch (e) { showError($('sb-result'), {message: 'Inputs are not valid JSON: ' + e.message}); return; }
      return run(b, 'POST', base + '/rehearse', {inputs: inputs, answers: $('sb-answers').value}, 'Rehearsal');
    },
    'import': function (b) {
      if (!window.confirm('Import this package into SATOM now?')) { return; }
      var secrets = {};
      document.querySelectorAll('#sb-secrets input[data-slug]').forEach(function (i) {
        if (i.value) { secrets[i.getAttribute('data-slug')] = i.value; }
      });
      var p = run(b, 'POST', base + '/import', {allow_downgrade: $('sb-downgrade').checked, secrets: secrets}, 'Import');
      document.querySelectorAll('#sb-secrets input[data-slug]').forEach(function (i) { i.value = ''; });
      return p;
    },
    'preview': function (b) {
      busy(b, true);
      SB.api('POST', base + '/generate/preview', {profile_id: $('sb-profile').value, prompt: $('sb-prompt').value})
        .then(function (r) {
          busy(b, false);
          var out = $('sb-preview');
          if (!r.ok) { showError(out, r.data, r.status); return; }
          clear(out);
          var d = r.data;
          out.appendChild(el('p', {'class': 'sb-help mb-1', text: 'Sends: ' + d.sends.join(', ') +
            '. Never sends: ' + d.never_sends.join(', ') + '. ' + d.bytes + ' bytes to ' +
            d.profile.name + ' (' + d.profile.model + ').'}));
          out.appendChild(el('details', {}, [el('summary', {'class': 'sb-help', text: 'System (fixed part: author guide + output contract)'}),
                                             el('pre', {'class': 'sb-pre', text: d.system})]));
          out.appendChild(el('details', {open: 'open'}, [el('summary', {'class': 'sb-help', text: 'User message'}),
                                             el('pre', {'class': 'sb-pre', text: d.user})]));
        });
    },
    'generate': function (b) {
      busy(b, true);
      var out = $('sb-rounds-out');
      clear(out);
      SB.api('POST', base + '/generate', {profile_id: $('sb-profile').value, prompt: $('sb-prompt').value,
                                          max_rounds: parseInt($('sb-rounds').value, 10) || 3})
        .then(function (r) {
          if (!r.ok) { busy(b, false); showError(out, r.data, r.status); return; }
          out.appendChild(el('p', {'class': 'sb-help', text: 'Generating (job ' + r.data.job_id.slice(0, 8) + ')...'}));
          poll(r.data.job_id, b);
        });
    }
  };

  function renderRounds(job) {
    var out = $('sb-rounds-out');
    clear(out);
    var status = el('p', {'class': 'mb-2'}, [el('strong', {text: 'Status: '}), job.status]);
    out.appendChild(status);
    (job.rounds || []).forEach(function (rd) {
      var d = el('div', {'class': 'sb-round ' + (rd.ok ? 'ok' : 'bad')}, [
        el('div', {}, [el('strong', {text: 'Round ' + rd.n + ' '}), badge(rd.ok, rd.ok ? 'passes the rules' : (rd.problems || []).length + ' problem(s)')])]);
      if (rd.problems && rd.problems.length) { d.appendChild(problemList(rd.problems)); }
      out.appendChild(d);
    });
    if (job.error) { out.appendChild(el('div', {'class': 'fw-alert fw-alert-danger', text: job.error})); }
    if (job.status === 'done' && job.project) {
      lastJobProject = job.project;
      var msg = job.ok ? 'The generated draft passes SATOM\'s rules.' :
        'Problems remain after the last round: the draft is shown with its problems. Applying it keeps them.';
      out.appendChild(el('p', {'class': 'sb-help', text: msg}));
      var apply = el('button', {'class': 'btn btn-sm btn-fw-primary', type: 'button', text: 'Apply this draft to the project'});
      apply.addEventListener('click', function () {
        var body = {};
        ['name', 'slug', 'version', 'icon', 'description', 'files', 'datasets', 'params', 'app'].forEach(function (k) {
          if (lastJobProject[k] !== undefined) { body[k] = lastJobProject[k]; }
        });
        SB.api('PUT', base, body).then(function (r) {
          if (!r.ok) { showError(out, r.data, r.status); return; }
          window.location.reload();
        });
      });
      out.appendChild(apply);
      out.appendChild(el('details', {'class': 'mt-2'}, [el('summary', {'class': 'sb-help', text: 'Generated draft'}),
        el('pre', {'class': 'sb-pre', text: JSON.stringify(job.project, null, 1)})]));
    }
  }

  function poll(jobId, btn) {
    SB.api('GET', '/api/jobs/' + jobId).then(function (r) {
      if (!r.ok) { busy(btn, false); showError($('sb-rounds-out'), r.data, r.status); return; }
      renderRounds(r.data);
      if (r.data.status === 'running') { window.setTimeout(function () { poll(jobId, btn); }, 2000); }
      else { busy(btn, false); }
    });
  }

  document.querySelectorAll('[data-sb-action]').forEach(function (b) {
    b.addEventListener('click', function () { actions[b.getAttribute('data-sb-action')](b); });
  });
})();
