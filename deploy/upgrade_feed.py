"""The system-upgrade feed: find and download the newest signed update package.

Vendored from SATOM ``deploy/upgrade_feed.py`` (see deploy/VENDORED.md).

A feed is a small JSON document (schema ``satom-builder.upgrade-feed/1``) that
names the newest ``satom-builder-update-<version>.tar.gz``, where it is hosted,
its size and its sha256. The built-in feed is the ``satom-builder-update-latest.json``
asset of the newest GitHub release (``DEFAULT_FEED_URL``); ``--feed URL`` or
``SATOM_BUILDER_UPGRADE_FEED`` name another one (an internal mirror).

What this module is NOT: a reason to trust anything. The feed is an address
book. The bytes it leads to are checked against the sha256 the feed declares
(so a truncated or swapped download fails here, early, with a clear message),
and then the package goes through the same path as an uploaded one: the root
runner verifies the Ed25519 signature against the root-owned trust store. A
forged feed can at most point at a package no trusted key signed, and that
package is refused.

Rules, same as ``update_package.py`` and for the same reason (the CLI runs this
as root from the copy in ``/usr/local/lib/satom-builder-runner``):

* standard library only;
* HTTPS only, redirects included;
* nothing is written outside the staging directory the caller names, and the
  file only appears under its final name once size and sha256 both match.

Nothing calls this on its own. A node fetches the feed only when an operator
asks (``satom-builder update fetch``): an on-premise product does not phone
home on a timer.

``auth`` is ``(host, user, token)``: the credentials go as HTTP Basic ONLY to
that exact host, as an unredirected header, so a redirect never carries them.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

FEED_SCHEMA = "satom-builder.upgrade-feed/1"
#: The feed asset of the newest public release. GitHub answers this path with a
#: redirect to the asset of whatever release is marked latest.
DEFAULT_FEED_URL = ("https://github.com/visionebc/satom-builder/releases/latest/"
                    "download/satom-builder-update-latest.json")
#: A feed document is a few hundred bytes; anything bigger is not a feed.
MAX_FEED_BYTES = 64 * 1024
#: Same ceiling as an uploaded package (nginx client_max_body_size in the vhost).
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
PKG_RE = re.compile(r"^satom-builder-update-[0-9][A-Za-z0-9._-]{0,60}\.tar\.gz$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_UA = "satom-builder-upgrade-feed/1"

#: ``() -> path | None`` of an extra CA bundle to trust on top of the system
#: roots (a feed on an internal host signed by a company CA). The CLI sets it
#: from ``--ca-file``. Unset = system roots only.
CA_FILE_PROVIDER = None


class FeedError(Exception):
    """The feed or the download it points at cannot be used."""


def feed_url() -> str:
    """The feed this node reads: ``SATOM_BUILDER_UPGRADE_FEED``, else the
    built-in ``DEFAULT_FEED_URL``."""
    return (os.environ.get("SATOM_BUILDER_UPGRADE_FEED") or DEFAULT_FEED_URL).strip()


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise FeedError("refusing a redirect away from HTTPS: %s" % newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def url_host(url: str) -> str:
    """Lower-case host of ``url`` (``""`` when it has none)."""
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def _request(url: str, auth=None) -> urllib.request.Request:
    """The GET for ``url``. With ``auth`` = ``(host, user, token)`` and ``url``
    on exactly that host, it carries HTTP Basic as an UNREDIRECTED header:
    urllib copies only ordinary headers onto a redirect, so the credentials
    never follow one, not even back to the same host."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if auth:
        host, user, token = auth
        if host and user and token and url_host(url) == (host or "").lower():
            cred = base64.b64encode(("%s:%s" % (user, token)).encode()).decode()
            req.add_unredirected_header("Authorization", "Basic " + cred)
    return req


def _ssl_context() -> ssl.SSLContext:
    """System roots, plus :data:`CA_FILE_PROVIDER`'s bundle when there is one.
    A provider that fails leaves the system roots: never unverified TLS."""
    ctx = ssl.create_default_context()
    path = None
    if CA_FILE_PROVIDER is not None:
        try:
            path = CA_FILE_PROVIDER()
        except Exception:  # noqa: BLE001
            path = None
    if path:
        try:
            ctx.load_verify_locations(cafile=path)
        except (OSError, ssl.SSLError):
            pass
    return ctx


def _no_query(url: str) -> str:
    """``url`` without query or fragment: a CDN redirect carries a signed token."""
    p = urlsplit(url or "")
    return "%s://%s%s" % (p.scheme, p.netloc, p.path)


def _http_detail(url: str, exc) -> str:
    """Who answered an HTTP error, as `` (server X, type, "page title"; after a
    redirect to …)`` -- a bare "HTTP 404" does not say whether GitHub, a proxy
    or a portal in between answered it. Empty when nothing is known."""
    bits = []
    hdr = getattr(exc, "headers", None)
    if hdr is not None:
        server = " ".join((hdr.get("Server") or "").split())[:60]
        ctype = (hdr.get("Content-Type") or "").split(";")[0].strip()[:60]
        bits += ["server " + server] if server else []
        bits += [ctype] if ctype else []
    try:
        body = exc.read(8192) if getattr(exc, "fp", None) else b""
    except Exception:  # noqa: BLE001 -- the error page is a hint, never a failure
        body = b""
    text = body.decode("utf-8", "replace")
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    page = " ".join(re.sub(r"<[^>]+>", " ", m.group(1) if m else text).split())[:160]
    if page:
        bits.append('"%s"' % page.replace('"', "'"))
    final = getattr(exc, "url", "") or ""
    if final and _no_query(final) != _no_query(url):
        bits.append("after a redirect to %s" % _no_query(final))
    return " (%s)" % ", ".join(bits) if bits else ""


def _not_found_hint(url: str) -> str:
    """What a 404 usually means for this kind of URL."""
    host, path = url_host(url), urlsplit(url).path.lower()
    if host == "github.com":
        return "GitHub has no release file at that path; check the owner, repository and file name"
    if "docker" in path:
        return ("the URL names a Docker registry proxy (for example of ghcr.io); it serves "
                "container images only, never release files. Use a raw proxy of https://github.com/")
    if "/releases/" in path:
        return ("the proxy did not find the file. Check, in this order: (1) the repository in "
                "the URL is a raw proxy whose remote is https://github.com/ (the path after it "
                "starts with the owner, e.g. /<owner>/<repo>/releases/...); (2) the proxy is "
                "not caching an older 404 -- one asked before the release existed keeps "
                "answering 404 until its not-found cache expires (Nexus: invalidate the "
                "repository's cache); (3) the proxy host reaches github.com AND the download "
                "host it redirects to (release-assets.githubusercontent.com, "
                "objects.githubusercontent.com) -- a proxy that cannot fetch the file answers 404")
    return "the server has no file at that path; check the URL"


def _open(url: str, timeout: float, auth=None):
    if not (url or "").lower().startswith("https://"):
        raise FeedError("only https:// URLs are fetched (got %r)" % url)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_context()), _HttpsOnlyRedirect())
    req = _request(url, auth)
    try:
        return opener.open(req, timeout=timeout)
    except FeedError:
        raise
    except urllib.error.HTTPError as exc:
        what = _http_detail(url, exc)
        if exc.code in (401, 403):
            raise FeedError("%s answered HTTP %s%s: the source wants credentials, or "
                            "refused the ones given" % (url, exc.code, what)) from None
        if exc.code == 404:
            raise FeedError("%s answered HTTP 404%s: %s"
                            % (url, what, _not_found_hint(url))) from None
        raise FeedError("%s answered HTTP %s%s" % (url, exc.code, what)) from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise FeedError("cannot reach %s (%s). A node with no route to the "
                        "internet takes the package by upload instead "
                        "(satom-builder update apply <file>)."
                        % (url, reason)) from None


def parse_feed(raw: bytes) -> dict:
    """Validate a feed document. Raises FeedError on anything unexpected."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FeedError("the feed is not JSON: %s" % exc) from None
    if not isinstance(doc, dict) or doc.get("schema") != FEED_SCHEMA:
        raise FeedError("not a SATOM Builder upgrade feed (schema %r)"
                        % (doc.get("schema") if isinstance(doc, dict) else None))
    pkg = doc.get("package")
    if not isinstance(pkg, dict):
        raise FeedError("the feed names no package")
    name, url, sha = pkg.get("name") or "", pkg.get("url") or "", pkg.get("sha256") or ""
    if not PKG_RE.match(name):
        raise FeedError("the feed names %r, which is not an update package" % name)
    if not url.lower().startswith("https://") or not url.endswith("/" + name):
        raise FeedError("the package URL must be https:// and end in /%s" % name)
    if not _SHA_RE.match(sha):
        raise FeedError("the feed carries no valid sha256 for %s" % name)
    size = pkg.get("size")
    if not isinstance(size, int) or not 0 < size <= MAX_PACKAGE_BYTES:
        raise FeedError("the feed declares an implausible size for %s: %r" % (name, size))
    ver = str(doc.get("version") or "")
    if name != "satom-builder-update-%s.tar.gz" % ver:
        raise FeedError("the feed version %r does not match its package %s" % (ver, name))
    return doc


def fetch_small(url: str, timeout: float = 10, max_bytes: int = MAX_FEED_BYTES,
               auth=None) -> bytes:
    """GET a small document (a feed) over HTTPS with this module's rules:
    https only, redirects included, at most ``max_bytes``. Shared by the
    CLI's feed check, so every download goes through the same client, proxy
    environment and TLS verification."""
    with _open(url, timeout, auth) as resp:
        raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise FeedError("the feed at %s is larger than %d KB" % (url, max_bytes // 1024))
    return raw


#: Where releases are published. Every release also carries the feed as the
#: asset ``LATEST_ASSET``, so ``<repo>/releases/latest/download/<LATEST_ASSET>``
#: is a link that never changes.
GITHUB_BASE = "https://github.com/"
LATEST_ASSET = "satom-builder-update-latest.json"
_GH_REPO_RE = re.compile(r"^https://github\.com/([^/?#]+)/([^/?#]+)/releases/", re.I)


def mirror_prefix(url: str, doc: dict) -> str:
    """What stands for ``https://github.com/`` when the feed at ``url`` was read
    through a mirror of GitHub, or ``""``.

    A node that reaches GitHub only through a proxy repository (a Nexus or
    Artifactory "raw" proxy of https://github.com/) reads the feed at
    ``<mirror>/<owner>/<repo>/releases/latest/download/satom-builder-update-latest.json``,
    but the feed names its assets on github.com -- which that node cannot
    reach. The package's own URL says which repository the release lives
    in; a feed URL on another host whose path holds ``/<owner>/<repo>/releases/``
    was read through a mirror, and everything before that path is the mirror.
    """
    m = _GH_REPO_RE.match(((doc.get("package") or {}).get("url") or ""))
    if not m or url_host(url) in ("", "github.com"):
        return ""
    needle = "/%s/%s/releases/" % (m.group(1), m.group(2))
    parts = urlsplit(url)
    at = parts.path.lower().find(needle.lower())
    if at < 0:
        return ""
    return "https://%s%s" % (parts.netloc, parts.path[:at + 1])


def _through_mirror(doc: dict, prefix: str) -> None:
    """Point the feed's own-repository URLs at the mirror ``prefix``. Only URLs
    of the release's repository move; the human link ``release_url`` stays."""
    m = _GH_REPO_RE.match(doc["package"]["url"])
    own = GITHUB_BASE + "%s/%s/" % (m.group(1), m.group(2))

    def moved(u):
        if isinstance(u, str) and u.lower().startswith(own.lower()):
            return prefix + u[len(GITHUB_BASE):]
        return u
    for key in ("package", "signing_key"):
        if isinstance(doc.get(key), dict) and "url" in doc[key]:
            doc[key]["url"] = moved(doc[key]["url"])
    doc["_mirror"] = prefix


def fetch_feed(url: str = "", timeout: float = 10, auth=None) -> dict:
    """Download and validate the feed document. Read through a mirror of
    GitHub, its assets are fetched through the same mirror (:func:`mirror_prefix`),
    which is also the host the configured credentials go to."""
    url = url or feed_url()
    if not url:
        raise FeedError("no upgrade feed is configured: pass --feed https://... "
                        "or set SATOM_BUILDER_UPGRADE_FEED")
    doc = parse_feed(fetch_small(url, timeout, auth=auth))
    prefix = mirror_prefix(url, doc)
    if prefix:
        _through_mirror(doc, prefix)
    doc["_feed_url"] = url
    return doc


def download_package(feed: dict, dest_dir, progress=None, timeout: float = 60,
                     auth=None) -> Path:
    """Download the package the feed names into ``dest_dir``.

    Streams to a hidden ``.part`` file and only renames it once the byte count
    and the sha256 match the feed; a mismatch deletes it. ``progress(done,
    total)`` is called every few MB. Returns the final path. ``auth`` reaches
    the package only when it is on the same host as the feed (see ``_request``).
    """
    pkg = feed["package"]
    return download_file(pkg["url"], pkg["name"], pkg["sha256"], pkg["size"], dest_dir,
                         progress=progress, timeout=timeout, auth=auth)


def download_file(url: str, name: str, want_sha: str, size: int, dest_dir,
                  progress=None, timeout: float = 60, auth=None) -> Path:
    """Download ``url`` to ``dest_dir/name``, atomically and verified.

    The file only appears under ``name`` once exactly ``size`` bytes arrived
    and their sha256 is ``want_sha``; more bytes than declared stop the
    download at once."""
    if not _SHA_RE.match(want_sha or ""):
        raise FeedError("no valid sha256 for %s" % name)
    if not isinstance(size, int) or not 0 < size <= MAX_PACKAGE_BYTES:
        raise FeedError("implausible size for %s: %r" % (name, size))
    if not name or "/" in name or name.startswith("."):
        raise FeedError("%r is not a file name" % name)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / name
    part = dest_dir / (".%s.part" % name)
    h = hashlib.sha256()
    done = 0
    last = 0
    try:
        with _open(url, timeout, auth) as resp, open(part, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                done += len(chunk)
                if done > size:
                    raise FeedError("%s is larger than the %d bytes the feed declares"
                                    % (name, size))
                h.update(chunk)
                fh.write(chunk)
                if progress and done - last >= 4 << 20:
                    last = done
                    progress(done, size)
        if done != size:
            raise FeedError("%s: got %d bytes, the feed declares %d (interrupted?)"
                            % (name, done, size))
        got = h.hexdigest()
        if got != want_sha:
            raise FeedError("%s: sha256 %s does not match the feed (%s)"
                            % (name, got, want_sha))
        os.replace(part, final)
    finally:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
    if progress:
        progress(size, size)
    return final


__all__ = ["FEED_SCHEMA", "DEFAULT_FEED_URL", "FeedError", "feed_url", "url_host", "parse_feed",
           "fetch_feed", "fetch_small", "download_package", "download_file", "PKG_RE",
           "LATEST_ASSET", "mirror_prefix"]
