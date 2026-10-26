"""SATOM targets, per-author tokens, the target snapshot cache and the rules
pack that matches a target.

The snapshot (contract section 2) is cached per (target, author) for
``SATOM_BUILDER_SNAPSHOT_TTL`` seconds: it is fetched with that author's
token, so it reflects that author's RBAC and ADOM.
"""
from __future__ import annotations

from flask import current_app

from . import db, rules
from .satom_client import SatomClient, SatomError
from .secrets_store import decrypt, encrypt

#: test hook: an httpx transport used by every client (a fake SATOM)
TRANSPORT_KEY = "SATOM_TRANSPORT"


def get_target(conn, tid):
    if tid is None:
        return None
    return conn.execute("SELECT * FROM targets WHERE id = ?", (tid,)).fetchone()


def list_targets(conn):
    return conn.execute("SELECT * FROM targets ORDER BY name").fetchall()


def token_for(conn, user_id: int, target_id: int) -> str:
    row = conn.execute("SELECT token_enc FROM tokens WHERE user_id = ? AND target_id = ?",
                       (user_id, target_id)).fetchone()
    return decrypt(row["token_enc"]) if row else ""


def has_token(conn, user_id: int, target_id: int) -> bool:
    return conn.execute("SELECT 1 FROM tokens WHERE user_id = ? AND target_id = ?",
                        (user_id, target_id)).fetchone() is not None


def set_token(conn, user_id: int, target_id: int, token: str) -> None:
    conn.execute("INSERT INTO tokens (user_id, target_id, token_enc, updated_at) "
                 "VALUES (?,?,?,?) ON CONFLICT(user_id, target_id) DO UPDATE SET "
                 "token_enc = excluded.token_enc, updated_at = excluded.updated_at",
                 (user_id, target_id, encrypt(token), db.now()))
    forget_link(conn, user_id, target_id)


def forget_link(conn, user_id: int, target_id: int) -> None:
    """Drop what was cached through one author's link to a target: its
    snapshots (every ADOM) AND its library. A new token can reach other ADOMs
    (contract section 17); the old library would keep showing the old ones."""
    conn.execute("DELETE FROM snapshots WHERE user_id = ? AND target_id = ?",
                 (user_id, target_id))
    conn.execute("DELETE FROM library_cache WHERE user_id = ? AND target_id = ?",
                 (user_id, target_id))


def clear_token(conn, user_id: int, target_id: int) -> None:
    conn.execute("DELETE FROM tokens WHERE user_id = ? AND target_id = ?", (user_id, target_id))
    forget_link(conn, user_id, target_id)


def client(conn, target_row, user_id: int, adom: str | None = None) -> SatomClient:
    """A client for one author's link; *adom* goes to SATOM as ``?adom=`` on
    the calls that run in an ADOM (contract section 17)."""
    cfg = current_app.config
    return SatomClient(target_row["base_url"], token_for(conn, user_id, target_row["id"]),
                       ca_pem=target_row["ca_pem"], verify_tls=bool(target_row["verify_tls"]),
                       timeout=float(cfg["SATOM_BUILDER_SATOM_TIMEOUT"]),
                       transport=cfg.get(TRANSPORT_KEY), adom=adom)


def call_adom(snap: dict | None) -> str | None:
    """The ``?adom=`` the calls that follow a snapshot carry: the ADOM it was
    asked in, when the token may choose one; None otherwise (a bound token,
    or a SATOM older than section 17 -- they run in the token's ADOM)."""
    reach = (snap or {}).get("reach") or {}
    if isinstance(reach, dict) and reach.get("can_choose") and reach.get("adom"):
        return str(reach["adom"])
    return None


def forget_snapshots(conn, target_id: int) -> None:
    """Drop every author's cached snapshot of a target: after something that
    changes what the target answers (a publisher registered on it), a cached
    one would make the local check disagree with SATOM."""
    conn.execute("DELETE FROM snapshots WHERE target_id = ?", (target_id,))


def snapshot(conn, target_row, user_id: int, refresh: bool = False,
             adom: str | None = None) -> dict:
    """The cached target snapshot in ADOM *adom* (None = the token's own),
    refetched when older than the TTL."""
    ttl = float(current_app.config["SATOM_BUILDER_SNAPSHOT_TTL"])
    key = adom or ""
    if not refresh:
        row = conn.execute("SELECT fetched_at, data FROM snapshots WHERE target_id = ? "
                           "AND user_id = ? AND adom = ?",
                           (target_row["id"], user_id, key)).fetchone()
        if row and db.now() - row["fetched_at"] < ttl:
            snap = db.loads(row["data"])
            if isinstance(snap, dict):
                return snap
    with client(conn, target_row, user_id, adom=adom) as c:
        snap = c.target()
    conn.execute("INSERT INTO snapshots (target_id, user_id, adom, fetched_at, data) VALUES "
                 "(?,?,?,?,?) ON CONFLICT(target_id, user_id, adom) DO UPDATE SET "
                 "fetched_at = excluded.fetched_at, data = excluded.data",
                 (target_row["id"], user_id, key, db.now(), db.dumps(snap)))
    return snap


def rules_pack(conn, target_row, user_id: int, snap: dict) -> rules.RulesPack:
    """The verified rules pack for this target's version, downloaded once."""
    cfg = current_app.config
    data_dir = cfg["SATOM_BUILDER_DATA"]
    timeout = float(cfg["SATOM_BUILDER_RULES_TIMEOUT"])
    sha = str(snap.get("rules_sha256") or "").lower()
    version = str(snap.get("satom_version") or "")
    if not sha:
        raise rules.RulesError("The target snapshot names no rules pack (rules_sha256).")
    if not rules.have(data_dir, sha):
        with client(conn, target_row, user_id) as c:
            data, header_sha = c.rules_pack()
        if header_sha != sha:
            raise rules.RulesError(
                f"SATOM sent rules pack {header_sha[:12]} but its target snapshot names "
                f"{sha[:12]}: refused.")
        rules.store(data_dir, data, header_sha, version)
    return rules.load(data_dir, sha, version, timeout)


def cached_library(conn, target_row, user_id: int, key: str, fetch, refresh: bool = False) -> dict:
    """A library answer (contract section 14), cached per (target, author,
    key) for ``SATOM_BUILDER_SNAPSHOT_TTL`` seconds like the snapshot."""
    ttl = float(current_app.config["SATOM_BUILDER_SNAPSHOT_TTL"])
    if not refresh:
        row = conn.execute("SELECT fetched_at, data FROM library_cache WHERE target_id = ? AND "
                           "user_id = ? AND key = ?", (target_row["id"], user_id, key)).fetchone()
        if row and db.now() - row["fetched_at"] < ttl:
            data = db.loads(row["data"])
            if isinstance(data, dict):
                data["cached_at"] = row["fetched_at"]
                return data
    with client(conn, target_row, user_id) as c:
        data = fetch(c)
    conn.execute("INSERT INTO library_cache (target_id, user_id, key, fetched_at, data) "
                 "VALUES (?,?,?,?,?) ON CONFLICT(target_id, user_id, key) DO UPDATE SET "
                 "fetched_at = excluded.fetched_at, data = excluded.data",
                 (target_row["id"], user_id, key, db.now(), db.dumps(data)))
    data["cached_at"] = db.now()
    return data


def library(conn, target_row, user_id: int, refresh: bool = False) -> dict:
    """The connection's library (the token's own ADOM: every ADOM the owner
    may enter for a Global token); ``{}`` when it cannot be had."""
    try:
        return cached_library(conn, target_row, user_id, "library", lambda c: c.library(),
                              refresh)
    except SatomError:
        return {}


def library_catalog(conn, target_row, user_id: int, product: str, version: str) -> dict:
    """One API library build as an API Catalog version (contract section 19),
    cached like the library. Raises SatomError (not measured, older SATOM)."""
    return cached_library(conn, target_row, user_id, "catalog:%s:%s" % (product, version),
                          lambda c: c.library_catalog(product, version))


def catalog_resolver(conn, target_row, user_id: int):
    """``resolve(product, build)`` -> SATOM's catalog entry, over *conn* (a
    job thread passes its own connection)."""
    def resolve(product: str, build: str) -> dict:
        return library_catalog(conn, target_row, user_id, product, build)
    return resolve


def library_completion(conn, target_row, user_id: int):
    """``complete(project)`` -> ``(project, problems)``: the project with
    SATOM's endpoint list for every connection pinned to the API library
    (contract section 19). Without a target, a pin is a named problem."""
    from . import fortinet_apis as FA
    if target_row is None:
        def none(product, build):
            raise FA.PinError("the project has no SATOM connection")
        return lambda project: FA.complete(project, none)
    resolve = catalog_resolver(conn, target_row, user_id)
    return lambda project: FA.complete(project, resolve)


def library_prompt(conn, target_row, user_id: int, project: dict, text: str,
                   snapshot: dict | None = None):
    """The Fortinet endpoint index an app's prompt carries, or None. The
    *snapshot*'s connections are the slugs a new pin must not take."""
    from . import fortinet_apis as FA
    if target_row is None or project.get("kind") != "app":
        return None
    builds = FA.library_builds(library(conn, target_row, user_id))
    taken = ((snapshot or {}).get("existing") or {}).get("connections") or {}
    return FA.prompt_for(FA.plan(text, project, builds), builds,
                         catalog_resolver(conn, target_row, user_id), taken=list(taken))


def dataset_defs(conn, target_row, user_id: int) -> dict:
    """``{key: dataset}`` of the target's library (every source: built-in,
    administrator, device objects, contract sections 14 and 16); ``{}`` when
    the target's SATOM has no library or cannot be reached."""
    lib = library(conn, target_row, user_id)
    return {str(d.get("key")): d for d in lib.get("datasets") or []
            if isinstance(d, dict) and d.get("key")}


def reach_of(lib: dict) -> dict:
    """What the connection reaches (contract section 17), normalised. A
    SATOM older than section 17 sends no ``reach``: ``known`` is False."""
    r = (lib or {}).get("reach")
    if not isinstance(r, dict):
        return {"known": False, "token_adom": str((lib or {}).get("product") or ""),
                "can_choose": False, "adoms": []}
    return {"known": True, "token_adom": str(r.get("token_adom") or ""),
            "can_choose": bool(r.get("can_choose")),
            "adoms": [str(a) for a in r.get("adoms") or []]}


def elsewhere_of(lib: dict) -> dict:
    e = ((lib or {}).get("elsewhere") or {}).get("datasets") or {}
    return {str(k): str(v) for k, v in e.items()} if isinstance(e, dict) else {}


GLOBAL = "global"
#: the ADOMs SATOM ships with: what the pickers offer before a library says more
DEFAULT_ADOMS = ("global", "fortiweb", "fortigate", "fortiadc", "fortianalyzer",
                 "fortiauthenticator")


def known_adoms(conn, user_id: int) -> list[str]:
    """Global first, then every ADOM this author's cached libraries say their
    tokens reach (contract section 17); SATOM's own list before any library."""
    seen: list[str] = []
    for r in conn.execute("SELECT data FROM library_cache WHERE user_id = ? AND key = 'library'",
                          (user_id,)):
        for a in reach_of(db.loads(r["data"], {}) or {}).get("adoms") or []:
            if a not in seen:
                seen.append(a)
    if not seen:
        seen = list(DEFAULT_ADOMS)
    return [GLOBAL] + sorted(a for a in seen if a != GLOBAL)


def install_adom(kind: str, datasets, override: str, lib: dict) -> dict:
    """Where a project installs, from what it reads (contract section 17).
    Pure: the project's kind, dataset keys and ADOM override, and the
    connection's library.

    -> ``{"adom", "shown", "mode", "products", "reason", "away", "warning",
    "can_choose", "adoms", "token_adom"}``. ``adom`` is what the calls send
    (None = the token's own); ``shown`` is the ADOM to display. ``mode``:
    ``auto`` (from the datasets), ``override`` (the author fixed it),
    ``bound`` (a token bound to one ADOM: nothing to choose) or ``legacy``
    (a SATOM without section 17)."""
    reach = reach_of(lib)
    defs = {str(d.get("key")): d for d in (lib or {}).get("datasets") or []
            if isinstance(d, dict) and d.get("key")}
    away_all = elsewhere_of(lib)
    keys = [str(k) for k in datasets or []] if kind in ("view", "widget") else []
    products = sorted({str(defs[k].get("product")) for k in keys
                       if k in defs and defs[k].get("product")})
    away = {k: away_all[k] for k in keys if k not in defs and k in away_all}
    out = {"products": products, "away": away, "warning": "",
           "can_choose": reach["can_choose"], "adoms": reach["adoms"],
           "token_adom": reach["token_adom"]}
    if not reach["known"]:
        return dict(out, adom=None, shown=reach["token_adom"] or "the token's ADOM",
                    mode="legacy", reason="This SATOM does not say which ADOMs the token "
                    "reaches (it predates SATOM's ADOM-per-call API): the project installs "
                    "in the token's ADOM.")
    if not reach["can_choose"]:
        tok = reach["token_adom"]
        warn = ""
        if away:
            where = sorted(set(away.values()))
            warn = ("%s %s in ADOM %s, but this connection's token is bound to ADOM %s: "
                    "link a Global token (My tokens) to build across ADOMs."
                    % (", ".join(sorted(away)), "lives" if len(away) == 1 else "live",
                       " and ".join(where), tok))
        return dict(out, adom=None, shown=tok, mode="bound", warning=warn,
                    reason="This connection's token is bound to ADOM %s." % tok)
    ov = (override or "").strip().lower()
    if ov and ov in reach["adoms"]:
        warn = ""
        other = [p for p in products if p != ov]
        if ov != GLOBAL and other:
            warn = ("It reads %s datasets, but it is set to install in ADOM %s, which does "
                    "not offer them: SATOM will refuse it. Use Auto, or Global."
                    % (", ".join(other), ov))
        return dict(out, adom=ov, shown=ov, mode="override", warning=warn,
                    reason="Set by hand to %s." % ("Global" if ov == GLOBAL else "ADOM " + ov))
    if len(products) == 1:
        return dict(out, adom=products[0], shown=products[0], mode="auto",
                    reason="Every product dataset it reads is %s." % products[0])
    if len(products) > 1:
        return dict(out, adom=GLOBAL, shown=GLOBAL, mode="auto",
                    reason="It reads %s datasets: a plugin that reads more than one ADOM "
                    "installs in Global and is opened from SATOM's Global console."
                    % " and ".join(products))
    return dict(out, adom=GLOBAL, shown=GLOBAL, mode="auto",
                reason="It reads no product dataset yet, so it installs in Global; it "
                "moves to a product's ADOM as soon as it reads only that product.")


class NotReady(Exception):
    """A precondition for talking to SATOM is missing (target, token...)."""

    def __init__(self, message: str, code: str = "not_ready", status: int = 409):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def _project_target(conn, project_row, user):
    if project_row["target_id"] is None:
        raise NotReady("Pick a SATOM target for this project first.", "no_target")
    t = get_target(conn, project_row["target_id"])
    if t is None:
        raise NotReady("The project's SATOM target no longer exists.", "no_target")
    if not has_token(conn, user.id, t["id"]):
        raise NotReady(f"You have no SATOM token for {t['name']} yet: add one under "
                       "My tokens.", "no_token")
    return t


def project_adom(conn, project_row, user, target_row=None) -> dict:
    """:func:`install_adom` for a project row, with the connection's library."""
    t = target_row or _project_target(conn, project_row, user)
    draft = db.loads(project_row["draft"], {}) or {}
    return install_adom(project_row["kind"], draft.get("datasets") or [],
                        str(draft.get("adom") or ""), library(conn, t, user.id))


def context_for_project(conn, project_row, user, refresh: bool = False,
                        purpose: str = "install"):
    """-> (target_row, snapshot). Raises NotReady / SatomError.

    The snapshot is asked in the project's ADOM (contract section 17), so
    every call that follows it (``call_adom``) runs there too. ``install``
    (check, validate, rehearse, import, test data): where the project
    installs, from its datasets. ``generate``: Global when the token may
    choose and the author fixed no ADOM -- the AI then sees every dataset,
    and the ADOM follows what it picks."""
    t = _project_target(conn, project_row, user)
    where = project_adom(conn, project_row, user, t)
    adom = where["adom"]
    if purpose == "generate" and where["mode"] == "auto":
        adom = GLOBAL
    return t, snapshot(conn, t, user.id, refresh=refresh, adom=adom)


__all__ = ["SatomError", "NotReady", "client", "snapshot", "rules_pack",
           "context_for_project"]
