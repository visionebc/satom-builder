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
      var body = {profile_id: $('sb-profile').value, prompt: $('sb-prompt').value,
                  max_rounds: parseInt($('sb-rounds').value, 10) || 3};
      if ($('sb-thread') && $('sb-thread').value) { body.thread = $('sb-thread').value; }
      SB.api('POST', base + '/generate', body)
        .then(function (r) {
          if (!r.ok) { busy(b, false); showError(out, r.data, r.status); return; }
          out.appendChild(el('p', {'class': 'sb-help', text: 'Generating (job ' + r.data.job_id.slice(0, 8) + ')...'}));
          $('sb-prompt').value = '';
          loadHistory(r.data.thread);
          poll(r.data.job_id, b);
        });
    }
  };

  // ── the author's conversations with the AI on this project ─────────────
  // (GET .../ai-history answers this user's only). A Generate that still
  // works when the page opens is followed again: closing the page loses nothing.
  var threads = [];
  function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : ''; }
  function turnBadge(u) {
    var map = u.status === 'running' ? ['info', 'working...'] : u.status === 'failed' ? ['danger', 'failed'] :
      !u.has_draft ? ['secondary', 'no draft'] : u.ok ? ['success', 'passes the rules'] : ['warning', 'problems left'];
    return el('span', {'class': 'fw-badge fw-badge-' + map[0], text: map[1]});
  }
  function renderThreadList() {
    var box = $('sb-threads');
    if (!box) { return; }
    clear(box);
    var id = $('sb-thread').value;
    if (!threads.length) {
      box.appendChild(el('p', {'class': 'sb-help mb-0', text: 'No conversation yet. What you ask is kept here and in the editor, for you and this project only.'}));
      return;
    }
    threads.forEach(function (t) {
      var b = el('button', {type: 'button', role: 'listitem', 'class': 'sb-thread-item' + (t.thread === id ? ' is-selected' : ''),
                            'aria-current': t.thread === id ? 'true' : null, 'data-thread': t.thread}, [
        el('strong', {text: t.title || '(no text)'}),
        el('small', {'class': 'sb-help', text: ' ' + t.turns.length + ' request(s) \u00b7 ' + when(t.updated_at)}),
        t.running ? el('span', {'class': 'fw-badge fw-badge-info ms-1', text: 'working...'}) : null]);
      b.addEventListener('click', function () { $('sb-thread').value = t.thread; renderThreadView(); });
      box.appendChild(b);
    });
  }
  function renderThreadView() {
    var view = $('sb-thread-view');
    renderThreadList();
    if ($('sb-generate-text')) { $('sb-generate-text').textContent = $('sb-thread').value ? 'Continue' : 'Generate'; }
    if (!view) { return; }
    clear(view);
    var id = $('sb-thread').value;
    var t = threads.filter(function (x) { return x.thread === id; })[0];
    if (!t) {
      view.appendChild(el('p', {'class': 'sb-help mb-0', text: 'New conversation. It stays in the list: come back to it and continue it when you want.'}));
      return;
    }
    view.appendChild(el('p', {'class': 'sb-help'}, [el('strong', {text: t.title || '(no text)'}),
      ' -- continuing: the AI starts from the project as saved and gets these requests and what they came to.']));
    t.turns.forEach(function (u) {
      view.appendChild(el('div', {'class': 'sb-turn-you-box'}, [
        el('div', {'class': 'sb-help', text: 'You \u00b7 ' + when(u.created_at)}),
        el('div', {'class': 'sb-turn-you', text: u.prompt})]));
      var ai = el('div', {'class': 'sb-turn-ai-box', 'data-job': u.job_id}, [
        el('div', {'class': 'sb-help'}, ['AI' + (u.profile ? ' \u00b7 ' + u.profile : '') + ' ', turnBadge(u)]),
        el('div', {'class': 'sb-turn-ai', text: u.status === 'running' ? 'Working on it: follow it below.' : u.outcome})]);
      if (u.status !== 'running') {
        var act = el('div');
        var d = el('details', {'class': 'sb-turn-did'}, [el('summary', {'class': 'sb-help', text: 'What it did'}), act]);
        d.addEventListener('toggle', function () {
          if (!d.open || act.firstChild) { return; }
          SB.api('GET', '/api/jobs/' + u.job_id).then(function (r) {
            if (!r.ok) { showError(act, r.data, r.status); return; }
            if (window.SBActivity) { window.SBActivity.renderActivity(act, r.data.progress, {key: 'turn-' + u.job_id, running: false}); }
          });
        });
        ai.appendChild(d);
        if (u.has_draft) {
          var show = el('button', {'class': 'btn btn-sm btn-outline-secondary mt-1', type: 'button', 'data-show-job': u.job_id},
                        [el('i', {'class': 'bi bi-box-arrow-in-down me-1'}), 'Show this result (and apply it)']);
          show.addEventListener('click', function () {
            SB.api('GET', '/api/jobs/' + u.job_id).then(function (r) {
              if (!r.ok) { showError($('sb-rounds-out'), r.data, r.status); return; }
              renderRounds(r.data);
              $('sb-rounds-out').scrollIntoView({block: 'nearest'});
            });
          });
          ai.appendChild(show);
        }
      }
      view.appendChild(ai);
    });
    view.appendChild(el('a', {'class': 'btn btn-sm btn-outline-secondary mt-1', href: '/projects/' + pid + '/edit#ai=' + t.thread},
                             [el('i', {'class': 'bi bi-box-arrow-up-right me-1'}), 'Open this conversation in the editor']));
  }
  function loadHistory(select) {
    var sel = $('sb-thread');
    if (!sel) { return Promise.resolve([]); }
    return SB.api('GET', base + '/ai-history').then(function (r) {
      if (!r.ok) { return []; }
      threads = r.data.threads || [];
      var keep = select || sel.value;
      clear(sel);
      sel.appendChild(el('option', {value: '', text: 'New conversation'}));
      threads.forEach(function (t) {
        sel.appendChild(el('option', {value: t.thread, text: (t.running ? '\u23f3 ' : '') + (t.title || '(no text)') +
          ' \u2014 ' + t.turns.length + ' request(s), ' + when(t.updated_at)}));
      });
      sel.value = threads.some(function (t) { return t.thread === keep; }) ? keep : '';
      renderThreadView();
      return threads;
    });
  }
  if ($('sb-thread-new')) {
    $('sb-thread-new').addEventListener('click', function () { $('sb-thread').value = ''; renderThreadView(); $('sb-prompt').focus(); });
  }
  if ($('sb-thread')) {
    $('sb-thread').addEventListener('change', renderThreadView);
    loadHistory().then(function (list) {
      var t = (list || []).filter(function (x) { return x.running; })[0];
      if (!t) { return; }
      var turn = t.turns.filter(function (u) { return u.status === 'running'; })[0];
      $('sb-thread').value = t.thread;
      renderThreadView();
      var btn = document.querySelector('[data-sb-action="generate"]');
      busy(btn, true);
      poll(turn.job_id, btn);
    });
  }

  function renderRounds(job) {
    var out = $('sb-rounds-out');
    clear(out);
    // what the job is doing (editor/activity.js, as in the editor's dialog):
    // the current step next to the status, then the live line, what the
    // model thinks and writes, and every step
    var steps = (job.progress && job.progress.steps) || [];
    var current = job.status === 'running' && steps.length ? steps[steps.length - 1].text : '';
    var status = el('p', {'class': 'mb-2'}, [el('strong', {text: 'Status: '}), job.status + (current ? ' \u2014 ' + current : '')]);
    out.appendChild(status);
    if (window.SBActivity) {
      var act = el('div', {'id': 'sb-activity'});
      out.appendChild(act);
      window.SBActivity.renderActivity(act, job.progress, {key: 'job-' + job.id, running: job.status === 'running'});
    }
    (job.rounds || []).forEach(function (rd) {
      var d = el('div', {'class': 'sb-round ' + (rd.ok ? 'ok' : 'bad')}, [
        el('div', {}, [el('strong', {text: 'Round ' + rd.n + ' '}), badge(rd.ok, rd.ok ? 'passes the rules' : (rd.problems || []).length + ' problem(s)')])]);
      if (rd.problems && rd.problems.length) { d.appendChild(problemList(rd.problems)); }
      out.appendChild(d);
    });
    if (job.error) { out.appendChild(el('div', {'class': 'fw-alert fw-alert-danger', text: job.error})); }
    if (job.message) {
      out.appendChild(el('div', {'class': 'sb-help mb-2 sb-ai-note'}, [el('strong', {text: 'The AI says: '}), job.message]));
    }
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
      else { busy(btn, false); loadHistory(); }
    });
  }

  document.querySelectorAll('[data-sb-action]').forEach(function (b) {
    b.addEventListener('click', function () { actions[b.getAttribute('data-sb-action')](b); });
  });
})();
