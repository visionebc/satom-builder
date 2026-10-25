"""HTTP client for SATOM's Builder API (contract section 1, prefix ``/api/v1/builder``).

Every call carries the logged-in author's own SATOM API token. Errors come
back in SATOM's shape ``{"error": code, "message": sentence}`` and are mapped
to :class:`SatomError` with a message an author can act on, e.g. "your SATOM
token is invalid or lacks scope write". The token never appears in a message.
"""
from __future__ import annotations

import json
import re
import ssl
from urllib.parse import quote, urlsplit

import httpx

from .secrets_store import redact

PREFIX = "/api/v1/builder"
MAX_PACKAGE = 10 * 1024 * 1024
MAX_RULES_PACK = 32 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024

#: scope SATOM requires per route (contract section 1)
SCOPES = {
    ("GET", "/target"): "read",
    ("GET", "/rules-pack"): "read",
    ("POST", "/validate"): "write",
    ("POST", "/rehearse"): "write",
    ("POST", "/import"): "admin",
    ("GET", "/publishers"): "read",
    ("POST", "/publishers"): "admin",
    ("GET", "/library"): "read",
    ("GET", "/data"): "read",
}
_LIB_PART = re.compile(r"^[0-9A-Za-z._-]{1,40}$")
EXTRA = {
    ("POST", "/rehearse"): "the token's owner must be allowed to author in Plugin Studio",
    ("POST", "/import"): "the token's owner must be a SATOM super-admin",
    ("POST", "/publishers"): "the token's owner must be a SATOM super-admin",
    ("GET", "/data"): "the token's owner must be allowed to author in Plugin Studio",
}


class Answer:
    """What SATOM sent back, read in full (bounded)."""
    __slots__ = ("status_code", "headers", "content")

    def __init__(self, status_code: int, headers, content: bytes):
        self.status_code = status_code
        self.headers = headers
        self.content = content


class SatomError(Exception):
    def __init__(self, message: str, code: str = "satom_error", status: int = 502,
                 satom_message: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.satom_message = satom_message

    def as_dict(self) -> dict:
        return {"error": self.code, "message": self.message}


def check_base_url(url: str) -> str:
    """Normalise a target base URL; https only, no credentials, query or fragment."""
    url = (url or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError("The SATOM URL must start with https://.")
    if not parts.hostname:
        raise ValueError("The SATOM URL has no host name.")
    if parts.username or parts.password:
        raise ValueError("The SATOM URL must not carry a user name or password.")
    if parts.query or parts.fragment:
        raise ValueError("The SATOM URL must not carry a query or fragment.")
    try:
        parts.port
    except ValueError:
        raise ValueError("The SATOM URL has an invalid port.")
    return url


def check_ca_pem(pem: str) -> str:
    pem = (pem or "").strip()
    if not pem:
        return ""
    from cryptography import x509
    try:
        certs = x509.load_pem_x509_certificates(pem.encode("ascii"))
    except Exception:
        raise ValueError("The CA bundle is not a valid PEM certificate list.")
    if not certs:
        raise ValueError("The CA bundle holds no certificate.")
    return pem + "\n"


def ssl_context(ca_pem: str = "", verify: bool = True):
    if not verify:
        return False
    if ca_pem:
        return ssl.create_default_context(cadata=ca_pem)
    return True


class SatomClient:
    def __init__(self, base_url: str, token: str, *, ca_pem: str = "",
                 verify_tls: bool = True, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = check_base_url(base_url)
        self._token = token or ""
        kw = {"timeout": httpx.Timeout(timeout, connect=10.0),
              "headers": {"Accept": "application/json",
                          "User-Agent": "satom-builder"},
              "follow_redirects": False}
        if transport is not None:
            kw["transport"] = transport
        else:
            kw["verify"] = ssl_context(ca_pem, verify_tls)
        self._http = httpx.Client(**kw)

    # -- plumbing ---------------------------------------------------------
    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _clean(self, text) -> str:
        return redact(text, extra=[self._token])

    def _request(self, method: str, route: str, *, files=None, data=None, json_body=None,
                 limit: int = MAX_JSON, params: dict | None = None) -> Answer:
        if not self._token:
            raise SatomError("You have no SATOM token for this target yet: add one "
                             "under My tokens.", "no_token", 409)
        headers = {"Authorization": "Bearer " + self._token}
        url = self.base_url + PREFIX + route
        try:
            with self._http.stream(method, url, headers=headers, files=files, data=data,
                                   json=json_body, params=params) as r:
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise SatomError("SATOM's answer is larger than the Builder "
                                         "accepts.", "too_large", 502)
                    chunks.append(chunk)
                resp = Answer(r.status_code, r.headers, b"".join(chunks))
        except SatomError:
            raise
        except httpx.ConnectError as exc:
            msg = str(exc)
            if "CERTIFICATE_VERIFY_FAILED" in msg or "certificate" in msg.lower():
                raise SatomError(
                    f"TLS verification of {self.host} failed: {self._clean(msg)}. Add the "
                    "CA that signed SATOM's certificate to the target (Targets page).",
                    "tls", 502) from None
            raise SatomError(f"Cannot reach SATOM at {self.host}: {self._clean(msg)}",
                             "unreachable", 502) from None
        except httpx.TimeoutException:
            raise SatomError(f"SATOM at {self.host} did not answer in time.",
                             "timeout", 504) from None
        except httpx.HTTPError as exc:
            raise SatomError(f"Talking to SATOM at {self.host} failed: "
                             f"{self._clean(exc)}", "http", 502) from None
        if resp.status_code >= 300:
            raise self._map_error(method, route, resp)
        return resp

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).netloc

    def _map_error(self, method: str, route: str, resp: Answer) -> SatomError:
        status = resp.status_code
        code, smsg = "", ""
        try:
            j = json.loads(resp.content.decode("utf-8"))
            if isinstance(j, dict):
                code = str(j.get("error") or "")[:80]
                smsg = self._clean(str(j.get("message") or ""))[:1000]
        except (ValueError, UnicodeDecodeError):
            pass
        scope = SCOPES.get((method, route), "read")
        extra = EXTRA.get((method, route))
        if status == 401:
            msg = f"Your SATOM token is invalid or lacks scope {scope}."
            return SatomError(msg + (f" SATOM says: {smsg}" if smsg else ""),
                              "satom_token", 401, smsg)
        if status == 403:
            msg = f"Your SATOM token is invalid or lacks scope {scope}"
            msg += f" ({extra})." if extra else "."
            return SatomError(msg + (f" SATOM says: {smsg}" if smsg else ""),
                              "satom_forbidden", 403, smsg)
        if status == 404 and code in ("", "not_found") and not smsg:
            return SatomError(f"{self.host} does not offer the SATOM Builder API "
                              f"({PREFIX}{route}). Is it a SATOM release with Builder "
                              "support?", "satom_not_found", 404)
        if status == 413:
            return SatomError("SATOM refused the package as too large (10 MB at most).",
                              "satom_too_large", 413, smsg)
        if 300 <= status < 400:
            return SatomError(f"SATOM answered with a redirect ({status}); check the "
                              "target URL.", "satom_redirect", 502)
        if status >= 500:
            return SatomError(f"SATOM failed with HTTP {status}"
                              + (f": {smsg}" if smsg else "."), "satom_server", 502, smsg)
        return SatomError(smsg or f"SATOM refused the request (HTTP {status}).",
                          code or "satom_refused", status if status in (400, 404, 409, 422)
                          else 400, smsg)

    def _json(self, resp: Answer):
        try:
            return json.loads(resp.content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SatomError("SATOM answered with something that is not JSON.",
                             "satom_bad_answer", 502) from None

    def _package_call(self, route: str, package: bytes, options: dict | None):
        if len(package) > MAX_PACKAGE:
            raise SatomError("The package is larger than 10 MB.", "too_large", 413)
        files = {"package": ("plugin.satomplugin", package, "application/zip")}
        data = {"options": json.dumps(options)} if options is not None else None
        return self._json(self._request("POST", route, files=files, data=data))

    # -- contract section 1 --------------------------------------------------
    def target(self) -> dict:
        snap = self._json(self._request("GET", "/target"))
        if not isinstance(snap, dict) or snap.get("format") != "satom.builder-target/1":
            raise SatomError("SATOM's target snapshot has an unknown format.",
                             "satom_bad_answer", 502)
        return snap

    def rules_pack(self) -> tuple[bytes, str]:
        resp = self._request("GET", "/rules-pack", limit=MAX_RULES_PACK)
        sha = (resp.headers.get("X-Satom-Rules-Sha256") or "").strip().lower()
        if not sha:
            raise SatomError("SATOM sent the rules pack without its "
                             "X-Satom-Rules-Sha256 header.", "rules_pack", 502)
        return resp.content, sha

    def validate(self, package: bytes) -> dict:
        return self._package_call("/validate", package, None)

    def rehearse(self, package: bytes, options: dict) -> dict:
        return self._package_call("/rehearse", package, options)

    def import_package(self, package: bytes, options: dict) -> dict:
        return self._package_call("/import", package, options)

    def publishers(self) -> list:
        return self._json(self._request("GET", "/publishers"))

    # -- contract section 14: the library ----------------------------------
    def _library_call(self, route: str) -> dict:
        try:
            return self._json(self._request("GET", route))
        except SatomError as exc:
            if exc.status == 404 and route == "/library":
                raise SatomError(f"{self.host} runs a SATOM without the Builder library: it "
                                 "needs SATOM 4.2.0 or later.", "no_library", 409) from None
            raise

    def library(self) -> dict:
        j = self._library_call("/library")
        if not isinstance(j, dict) or j.get("format") != "satom.builder-library/1":
            raise SatomError("SATOM's library has an unknown format.", "satom_bad_answer", 502)
        return j

    def library_build(self, product: str, version: str, endpoint: str = "") -> dict:
        if not _LIB_PART.match(product or "") or not _LIB_PART.match(version or "") \
                or len(endpoint or "") > 200:
            raise SatomError("Malformed product, version or endpoint.", "bad_request", 400)
        route = "/library/fortinet/%s/%s" % (quote(product, safe=""), quote(version, safe=""))
        if endpoint:
            route += "?endpoint=" + quote(endpoint, safe="")
        j = self._library_call(route)
        if not isinstance(j, dict):
            raise SatomError("SATOM's library answer is not an object.", "satom_bad_answer", 502)
        return j

    # -- contract section 15: this server's rows for the test data ---------
    def data(self, datasets: list | None = None, stores: list | None = None,
             limit: int = 200) -> dict:
        """The rows SATOM shows the token's owner (``satom.builder-data/1``).
        ``None`` = every dataset / store, ``[]`` = none."""
        q = {"limit": str(max(1, min(int(limit or 200), 2000)))}
        for name, v in (("datasets", datasets), ("stores", stores)):
            if v is not None:
                q[name] = ",".join(str(x) for x in v)
        try:
            j = self._json(self._request("GET", "/data", params=q))
        except SatomError as exc:
            # /builder/data itself never answers 404: a 404 is a SATOM without it
            # (4.2.0 answers {"error": "Not found"} for an unknown route).
            if exc.status == 404:
                raise SatomError(f"{self.host} runs a SATOM that does not send its data to "
                                 "the Builder yet: it needs SATOM 4.2.1 or later. Generate "
                                 "test data instead.", "no_data_api", 409) from None
            raise
        if not isinstance(j, dict) or j.get("format") != "satom.builder-data/1":
            raise SatomError("SATOM's data answer has an unknown format.", "satom_bad_answer", 502)
        return j

    def register_publisher(self, label: str, public_key_b64: str) -> dict:
        return self._json(self._request("POST", "/publishers",
                                        json_body={"label": label[:80],
                                                   "public_key": public_key_b64}))
