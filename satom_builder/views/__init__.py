"""Blueprints."""
from __future__ import annotations


def register(app) -> None:
    from . import admin, api, auth, editor, me, projects
    app.register_blueprint(auth.bp)
    app.register_blueprint(projects.bp)
    app.register_blueprint(editor.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(me.bp)
    app.register_blueprint(admin.bp)

    @app.get("/")
    def index():
        from flask import g, redirect, url_for
        if g.get("user") is None:
            return redirect(url_for("auth.login"))
        return redirect(url_for("projects.index"))
