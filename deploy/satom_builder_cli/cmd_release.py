"""Signed releases: what root may install or run from deploy/.

Adapted from SATOM ``deploy/satom_cli/cmd_release.py`` (see deploy/VENDORED.md).

* ``DeploySource`` -- the gate the root verbs that run something from deploy/
  go through (``reinstall cli|runner``). With no release-trust anchor it is
  the app tree's deploy/, as before. With a root-owned anchor
  (``/etc/satom-builder/release-trust.allowed_signers``) it is a root-only
  staged copy of the newest SIGNED commit whose deploy/ equals HEAD's -- and
  when deploy/ has unsigned changes the verb refuses, naming the fix.
* ``sign_release`` -- the attestation: ONE signed, empty commit on top of HEAD,
  made with the root-only release key (``/root/.satom-builder-release/
  release_ed25519``). Unlike SATOM it never pushes: a Builder node has no
  upstream, and a release checkout is pushed by its maintainer.

Root never runs git inside a repository another account owns (its config can
name programs git executes); see release_trust.py.
"""
from __future__ import annotations

import os
import pwd
import shutil
import socket
import stat
import subprocess
import tempfile
from pathlib import Path

from . import release_trust as rt


def _owner(repo):
    try:
        return pwd.getpwuid(Path(repo).stat().st_uid).pw_name
    except (KeyError, OSError):
        return "root"


def repo_git(repo, *args, timeout=120, input_=None):
    """``git -C <repo>`` as the repository's OWNER (never as root in somebody
    else's repository), with that account's HOME and no system config."""
    owner = _owner(repo)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    cmd = ["git", "-C", str(repo), "-c", "safe.directory=%s" % repo] + list(args)
    prefix = rt.app_user_prefix(owner)
    if prefix:
        try:
            env["HOME"] = pwd.getpwnam(owner).pw_dir
        except KeyError:
            pass
        cmd = ["runuser", "-m", "-u", owner, "--"] + cmd
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd="/", env=env, input=input_)
    return p.returncode, p.stdout or "", p.stderr or ""


def head_of(repo):
    rc, out, err = repo_git(repo, "rev-parse", "--verify", "HEAD")
    sha = out.strip()
    if rc != 0 or not rt.is_full_sha(sha):
        raise rt.ReleaseTrustError("cannot read HEAD of %s: %s" % (repo, (err or out)[-200:]))
    return sha


class DeploySource:
    """``with DeploySource(app_dir) as src:`` then use ``src.dir`` / ``src.env``
    -- or report ``src.refusal`` (a list of lines) when it is set."""

    def __init__(self, app_dir):
        self.app_dir = Path(app_dir)
        self.dir = self.app_dir / "deploy"
        self.env = None
        self.refusal = None
        self.note = ""
        self._stage = None

    def __enter__(self):
        anchor = rt.find_anchor()
        if anchor is None:
            return self
        try:
            head = head_of(self.app_dir)
            res = rt.resolve(self.app_dir, head, anchor, app_user=_owner(self.app_dir))
        except Exception as exc:  # noqa: BLE001 -- fail closed for root verbs
            self.refusal = [
                "refusing: a release-trust anchor is installed (%s) and the release "
                "signature could not be checked: %s" % (anchor, str(exc)[:300])]
            return self
        if res["mode"] == "held":
            since = res.get("last_signed")
            self.refusal = [
                ("deploy/ has unsigned changes since %s; run `satom-builder "
                 "sign-release` first" % since[:12]) if since else
                ("no signed release in the last %d commits of %s; run "
                 "`satom-builder sign-release` first" % (rt.WALK_LIMIT, head[:12])),
                "A release-trust anchor is installed (%s), so root only installs "
                "what a release key signed." % anchor]
            return self
        try:
            self._stage = rt.root_only_dir("satom-builder-cli-rel-")
            self.dir = rt.stage_deploy(res["signed"], self._stage)
        except Exception as exc:  # noqa: BLE001
            self.__exit__(None, None, None)
            self.refusal = ["refusing: staging signed release %s failed: %s"
                            % (res["signed"][:12], str(exc)[:300])]
            return self
        self.env = dict(os.environ, SATOM_BUILDER_APP_DIR=str(self.app_dir),
                        SATOM_BUILDER_DEPLOY_SRC=str(self.dir))
        self.note = "installed from signed release %s (%s)" % (res["signed"][:12], res["mode"])
        return self

    def __exit__(self, *exc):
        if self._stage is not None:
            shutil.rmtree(str(self._stage), ignore_errors=True)
            self._stage = None
        return False


def _key_problem(key):
    """Why ``key`` must not be used, or None. Root only, closed to everyone."""
    try:
        ks = os.lstat(key)
        ds = os.lstat(key.parent)
    except OSError as exc:
        return "no release key at %s (%s)" % (key, exc.strerror or exc)
    if not stat.S_ISREG(ks.st_mode):
        return "%s is not a regular file" % key
    for p, s in ((key, ks), (key.parent, ds)):
        if s.st_uid != os.geteuid():
            return "%s is owned by uid %d, not by root" % (p, s.st_uid)
        if s.st_mode & 0o077:
            return ("%s is mode %o; it must be readable by root only (key 0600, "
                    "directory 0700)" % (p, s.st_mode & 0o777))
    return None


def _pubkey(key):
    p = subprocess.run(["ssh-keygen", "-y", "-f", str(key)], capture_output=True,
                       text=True, timeout=30, env=rt.clean_env())
    if p.returncode != 0 or not (p.stdout or "").strip():
        raise rt.ReleaseTrustError("cannot read the public half of %s: %s"
                                   % (key, (p.stderr or p.stdout)[-200:]))
    return p.stdout.strip().splitlines()[0]


def sign_release(repo, yes=False, key=None, mirror=None):
    """(exit code, lines). Dry run unless ``yes``."""
    if os.geteuid() != 0:
        return 3, ["refusing: must run as root (sudo satom-builder sign-release)"]
    repo = Path(repo)
    key = Path(key or rt.SIGNING_KEY)
    problem = _key_problem(key)
    if problem:
        return 1, ["refusing: %s" % problem, "",
                   "The release key lives ONLY on the release machine, root-only:",
                   "  install -d -m 0700 %s" % key.parent,
                   "  ssh-keygen -t ed25519 -N '' -C %s -f %s" % (rt.PRINCIPAL, key)]
    try:
        head = head_of(repo)
    except rt.ReleaseTrustError as exc:
        return 1, ["refusing: %s" % exc]
    rc, branch, _ = repo_git(repo, "symbolic-ref", "-q", "HEAD")
    branch = branch.strip()
    if rc != 0 or not branch.startswith("refs/heads/"):
        return 1, ["refusing: %s is on a detached HEAD; check out the release branch" % repo]

    tmp_mirror = None
    if mirror is None and repo.resolve() != Path("/opt/satom-builder"):
        tmp_mirror = rt.root_only_dir("satom-builder-sign-mirror-")
        mirror = Path(tmp_mirror) / "mirror.git"
    anchor = rt.find_anchor()
    tmp = Path(tempfile.mkdtemp(prefix="satom-builder-sign-"))
    os.chmod(str(tmp), 0o700)
    try:
        try:
            mir = rt.fetch(repo, head, app_user=_owner(repo), mirror=mirror)
            own = tmp / "own.allowed_signers"
            own.write_text('%s namespaces="git" %s\n' % (rt.PRINCIPAL, _pubkey(key)))
            if rt.is_signed(mir, head, str(own))[0]:
                return 0, ["sign release: HEAD %s is already signed" % head[:12]]
            last = rt.newest_signed(mir, head, str(own))
        except rt.ReleaseTrustError as exc:
            return 1, ["refusing: %s" % exc]

        if last:
            d = rt.mirror_git(mir, ["diff", "--stat", last, head, "--", "deploy"])
            diff = (d.stdout or "").splitlines() or ["(deploy/ unchanged)"]
        else:
            diff = ["no earlier release signed by this key: the WHOLE deploy/ at %s "
                    "is being attested" % head[:12]]
        if not yes:
            return 2, (["sign release (dry run)",
                        "  would sign  : release: sign %s (an empty commit on %s)"
                        % (head[:12], branch[len("refs/heads/"):]),
                        "  last signed : %s" % ((last or "none")[:12]),
                        "  anchor here : %s" % (anchor or "absent (enforcement OFF on "
                                                 "this machine)"),
                        "", "deploy/ changes you are attesting:"]
                       + ["  " + ln for ln in diff[-30:]]
                       + ["", "Review them, then re-run with --yes."])

        operator = os.environ.get("SUDO_USER") or "root"
        cfg = ["-c", "user.name=SATOM Builder release",
               "-c", "user.email=release@satom-builder.invalid",
               "-c", "gpg.format=ssh", "-c", "user.signingkey=%s" % key,
               "-c", "gpg.ssh.program=ssh-keygen"]
        c = rt.mirror_git(mir, cfg + [
            "commit-tree", "-S", "%s^{tree}" % head, "-p", head,
            "-m", "release: sign %s" % head[:12],
            "-m", "Release attested by %s on %s with satom-builder sign-release."
                  % (operator, socket.gethostname())], timeout=120)
        new = (c.stdout or "").strip()
        if c.returncode != 0 or not rt.is_full_sha(new):
            return 1, ["refusing: signing failed: %s" % (c.stderr or c.stdout or "")[-300:]]
        ok, detail = rt.is_signed(mir, new, str(own))
        if not ok:
            return 1, ["refusing: the new commit does not verify: %s" % detail]
        raw = rt.mirror_git(mir, ["cat-file", "commit", new]).stdout or ""
        # Written by the repository's OWNER: root never creates objects in a
        # .git another account owns.
        rc, out, err = repo_git(repo, "hash-object", "-t", "commit", "-w", "--stdin",
                                input_=raw)
        if rc != 0 or out.strip() != new:
            return 1, ["refusing: writing the commit gave %r (expected %s): %s"
                       % (out.strip()[:40], new[:12], err[-200:])]
        rc, out, err = repo_git(repo, "update-ref", "-m", "release: sign %s" % head[:12],
                                branch, new, head)
        if rc != 0:
            return 1, ["refusing: %s moved while signing (%s); re-run" % (branch, (err or out)[-200:])]
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
        if tmp_mirror:
            shutil.rmtree(str(tmp_mirror), ignore_errors=True)
    return 0, ["sign release: %s" % new[:12],
               "  signed commit : %s" % new,
               "  attests       : %s" % head,
               "  branch        : %s" % branch,
               "Nothing was pushed. Push it yourself when it is the release you mean."]
