"""Per-author pages: My tokens, Publisher key, Account."""
from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .. import db, package, targets
from ..satom_client import SatomError
from ..security import audit, login_required

bp = Blueprint("me", __name__)

MAX_TOKEN = 4096


@bp.get("/tokens")
@login_required
def tokens():
    conn = db.get()
    rows = []
    for t in targets.list_targets(conn):
        tok = conn.execute("SELECT updated_at FROM tokens WHERE user_id = ? AND target_id = ?",
                           (g.user.id, t["id"])).fetchone()
        snap = conn.execute("SELECT fetched_at, data FROM snapshots WHERE user_id = ? AND "
                            "target_id = ? ORDER BY fetched_at DESC", (g.user.id, t["id"])
                            ).fetchone()
        s = db.loads(snap["data"], {}) if snap else {}
        lib = conn.execute("SELECT data FROM library_cache WHERE user_id = ? AND target_id = ? "
                           "AND key = 'library'", (g.user.id, t["id"])).fetchone()
        rows.append({"t": t, "set": tok is not None,
                     "updated_at": tok["updated_at"] if tok else None,
                     "caller": (s or {}).get("caller") or {},
                     "satom_version": (s or {}).get("satom_version", ""),
                     "fetched_at": snap["fetched_at"] if snap else None,
                     "reach": reach_summary(db.loads(lib["data"], {}) if lib else None)})
    return render_template("me/tokens.html", rows=rows)


def reach_summary(lib) -> dict | None:
    """What a connection reaches, for the page (contract section 17): the
    ADOMs, the datasets per ADOM it sees, and how many live in ADOMs it does
    not reach. None before the library was read once."""
    if not isinstance(lib, dict) or not lib:
        return None
    r = targets.reach_of(lib)
    per: dict = {}
    for d in lib.get("datasets") or []:
        if isinstance(d, dict):
            p = str(d.get("product") or "") or "every ADOM"
            per[p] = per.get(p, 0) + 1
    away: dict = {}
    for a in targets.elsewhere_of(lib).values():
        away[a] = away.get(a, 0) + 1
    return {"known": r["known"], "can_choose": r["can_choose"], "token_adom": r["token_adom"],
            "adoms": [a for a in r["adoms"] if a != targets.GLOBAL],
            "total": sum(per.values()), "per": sorted(per.items()),
            "away": sorted(away.items()), "away_total": sum(away.values())}


def reach_message(lib) -> str:
    rs = reach_summary(lib)
    if rs is None:
        return ""
    if not rs["known"]:
        return (" This SATOM does not report which ADOMs a token reaches: plugins install in "
                "the token's ADOM.")
    if rs["can_choose"]:
        return (" Global token: it sees %d datasets of every ADOM you may enter (%s); each "
                "project installs in the ADOM of the datasets it reads, or in Global when it "
                "reads more than one." % (rs["total"], ", ".join(rs["adoms"]) or "none"))
    msg = " This token is bound to ADOM %s: it sees %d datasets." % (rs["token_adom"], rs["total"])
    if rs["away_total"]:
        msg += (" %d more live in other ADOMs (%s): link a Global token to use them."
                % (rs["away_total"], ", ".join(a for a, _n in rs["away"])))
    return msg


@bp.post("/tokens/<int:tid>")
@login_required
def token_set(tid):
    conn = db.get()
    t = targets.get_target(conn, tid)
    if t is None:
        flash("No such SATOM target.", "danger")
        return redirect(url_for("me.tokens"))
    action = request.form.get("action") or "set"
    if action == "clear":
        targets.clear_token(conn, g.user.id, tid)
        audit("token.clear", t["name"])
        flash(f"Your token for {t['name']} was removed.", "success")
    elif action == "test":
        try:
            snap = targets.snapshot(conn, t, g.user.id, refresh=True)
        except SatomError as exc:
            audit("token.test", t["name"], ok=False, error=exc.code)
            flash(exc.message, "danger")
        else:
            c = snap.get("caller") or {}
            lib = targets.library(conn, t, g.user.id, refresh=True)
            audit("token.test", t["name"], ok=True, adom=str(snap.get("product") or ""))
            flash(f"Connected to SATOM {snap.get('satom_version', '?')} as "
                  f"{c.get('username', '?')} (scopes: {', '.join(c.get('scopes') or []) or '-'}"
                  f"; ADOM {snap.get('product', '?')}).{reach_message(lib)}", "success")
    else:
        tok = (request.form.get("token") or "").strip()
        if not tok or len(tok) > MAX_TOKEN or any(ch.isspace() for ch in tok):
            flash("Paste the SATOM API token (one line, no spaces).", "danger")
        else:
            targets.set_token(conn, g.user.id, tid, tok)
            audit("token.set", t["name"])
            flash(f"Token for {t['name']} saved. Use Test to check it.", "success")
    return redirect(url_for("me.tokens"))


@bp.get("/publisher")
@login_required
def publisher():
    conn = db.get()
    info = package.publisher_info(current_app.config["SATOM_BUILDER_PUBLISHER_KEY"])
    rows = []
    for t in targets.list_targets(conn):
        rows.append({"t": t, "has_token": targets.has_token(conn, g.user.id, t["id"])})
    return render_template("me/publisher.html", info=info, rows=rows,
                           key_path=current_app.config["SATOM_BUILDER_PUBLISHER_KEY"])


@bp.post("/publisher/register/<int:tid>")
@login_required
def publisher_register(tid):
    conn = db.get()
    t = targets.get_target(conn, tid)
    info = package.publisher_info(current_app.config["SATOM_BUILDER_PUBLISHER_KEY"])
    if t is None or not info["present"]:
        flash("No such target, or no publisher key on this installation.", "danger")
        return redirect(url_for("me.publisher"))
    label = (request.form.get("label") or "SATOM Builder").strip()[:80] or "SATOM Builder"
    try:
        with targets.client(conn, t, g.user.id) as c:
            res = c.register_publisher(label, info["public_key"])
    except SatomError as exc:
        audit("publisher.register", t["name"], ok=False, error=exc.code)
        flash(exc.message, "danger")
    else:
        targets.forget_snapshots(conn, t["id"])
        audit("publisher.register", t["name"], ok=True, key_id=info["key_id"])
        kid = (res or {}).get("key_id", info["key_id"])
        flash(f"Publisher key registered on {t['name']} (key_id {kid[:16]}...).", "success")
    return redirect(url_for("me.publisher"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    conn = db.get()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (g.user.id,)).fetchone()
    if request.method == "POST":
        if row["source"] != "local":
            flash("Your password is managed by the directory.", "danger")
        elif not check_password_hash(row["password_hash"] or "", request.form.get("current")
                                     or ""):
            audit("password.change", g.user.username, ok=False)
            flash("The current password is wrong.", "danger")
        elif len(request.form.get("new") or "") < 12:
            flash("The new password must be at least 12 characters.", "danger")
        elif request.form.get("new") != request.form.get("confirm"):
            flash("The two new passwords differ.", "danger")
        else:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (generate_password_hash(request.form["new"]), g.user.id))
            audit("password.change", g.user.username, ok=True)
            flash("Password changed.", "success")
        return redirect(url_for("me.account"))
    return render_template("me/account.html", row=row)

