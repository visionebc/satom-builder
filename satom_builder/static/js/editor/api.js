/* The editor's only way to the backend: the internal JSON API of contract
 * section 11 (plus GET /api/ai/profiles, see docs/editor.md). Session cookie
 * + X-CSRF-Token from <meta name="csrf-token">. Errors arrive as
 * {"error": code, "message": text} and are thrown as ApiError. */

export class ApiError extends Error {
  constructor(message, code, status, body) {
    super(message);
    this.code = code || "";
    this.status = status || 0;
    this.body = body || null;
  }
}

export class Api {
  constructor(csrf, base) {
    this.csrf = csrf || "";
    this.base = base || "";
  }

  async req(method, path, body, opts) {
    const o = opts || {};
    const headers = { Accept: "application/json", "X-CSRF-Token": this.csrf };
    let payload;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
    let r;
    try {
      r = await fetch(this.base + path, { method, credentials: "same-origin", headers, body: payload, signal: o.signal });
    } catch (e) {
      throw new ApiError("The Builder server cannot be reached: " + e.message, "network", 0);
    }
    let data = null;
    const text = await r.text();
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = null; }
    }
    if (!r.ok) {
      const msg = (data && data.message) || `HTTP ${r.status} from ${path}`;
      throw new ApiError(msg, (data && data.error) || "http_" + r.status, r.status, data);
    }
    if (data === null && text) throw new ApiError(`${path} did not answer JSON`, "bad_json", r.status);
    return data;
  }

  project(id) { return this.req("GET", `/api/projects/${id}`); }
  save(id, payload) { return this.req("PUT", `/api/projects/${id}`, payload); }
  check(id) { return this.req("POST", `/api/projects/${id}/check`, {}); }
  validateRemote(id) { return this.req("POST", `/api/projects/${id}/validate-remote`, {}); }
  rehearse(id, inputs, answers) { return this.req("POST", `/api/projects/${id}/rehearse`, { inputs: inputs || {}, answers: answers || "yes" }); }
  importPackage(id, allowDowngrade, secrets) {
    return this.req("POST", `/api/projects/${id}/import`, { allow_downgrade: !!allowDowngrade, secrets: secrets || {} });
  }
  packageUrl(id) { return this.base + `/api/projects/${id}/package`; }
  uploadAsset(id, file) {
    const fd = new FormData();
    fd.append("file", file);
    return this.req("POST", `/api/projects/${id}/assets`, fd);
  }
  schema(targetId) { return this.req("GET", `/api/targets/${targetId}/schema`); }
  library(targetId, refresh) { return this.req("GET", `/api/targets/${targetId}/library${refresh ? "?refresh=1" : ""}`); }
  libraryCatalog(targetId, product, version) { return this.req("GET", `/api/targets/${targetId}/library/fortinet/${encodeURIComponent(product)}/${encodeURIComponent(version)}/catalog`); }
  snapshot(targetId, refresh) { return this.req("GET", `/api/targets/${targetId}/snapshot${refresh ? "?refresh=1" : ""}`); }
  aiProfiles() { return this.req("GET", "/api/ai/profiles"); }
  generatePreview(id, profileId, prompt, thread) { return this.req("POST", `/api/projects/${id}/generate/preview`, { profile_id: profileId, prompt, thread: thread || undefined }); }
  generate(id, profileId, prompt, maxRounds, thread) {
    return this.req("POST", `/api/projects/${id}/generate`, { profile_id: profileId, prompt, max_rounds: maxRounds, thread: thread || undefined });
  }
  aiHistory(id) { return this.req("GET", `/api/projects/${id}/ai-history`); }
  job(jobId) { return this.req("GET", `/api/jobs/${encodeURIComponent(jobId)}`); }
  testdata(id) { return this.req("GET", `/api/projects/${id}/testdata`); }
  saveTestdata(id, data) { return this.req("PUT", `/api/projects/${id}/testdata`, { data }); }
  generateTestdata(id, opts) { return this.req("POST", `/api/projects/${id}/testdata/generate`, opts || {}); }
  importTestdata(id, opts) { return this.req("POST", `/api/projects/${id}/testdata/import`, opts || {}); }
  testdataSchema(id) { return this.req("GET", `/api/projects/${id}/testdata/schema`); }
  testdataSql(id, sql) { return this.req("POST", `/api/projects/${id}/testdata/sql`, { sql }); }
  testRun(id, body) { return this.req("POST", `/api/projects/${id}/test-runs`, body); }
  testRuns(id) { return this.req("GET", `/api/projects/${id}/test-runs`); }
  testRunGet(runId) { return this.req("GET", `/api/test-runs/${encodeURIComponent(runId)}`); }
  keepRun(runId) { return this.req("POST", `/api/test-runs/${encodeURIComponent(runId)}/keep`, {}); }
}
