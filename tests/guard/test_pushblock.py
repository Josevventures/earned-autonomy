"""The push block, against real git: an ordinary push works without it and fails with it, overrides included."""
import os
import shutil
import subprocess

import pytest

from earned_autonomy.guard.pushblock import MARKER, block_push

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
ID = ["-c", "user.name=test", "-c", "user.email=test@example.com"]


def git(*args, env=None, cwd=None):
    return subprocess.run(["git", *args], capture_output=True, text=True, env=env, cwd=cwd, timeout=60)


@pytest.fixture
def repo(tmp_path):
    remote, clone = tmp_path / "remote.git", tmp_path / "clone"
    assert git("init", "--bare", "-q", remote.as_posix()).returncode == 0
    assert git("clone", "-q", remote.as_posix(), clone.as_posix()).returncode == 0
    (clone / "f.txt").write_text("x", encoding="utf-8")
    assert git(*ID, "-C", clone.as_posix(), "add", "f.txt").returncode == 0
    assert git(*ID, "-C", clone.as_posix(), "commit", "-q", "-m", "probe").returncode == 0
    return remote, clone


def refs(remote):
    return git("-C", remote.as_posix(), "for-each-ref", "--format=%(refname)").stdout.split()


def test_control_an_unblocked_push_works(repo):
    remote, clone = repo
    assert git("-C", clone.as_posix(), "push", "-q", "origin", "HEAD:refs/heads/ok").returncode == 0
    assert refs(remote) == ["refs/heads/ok"]


@pytest.mark.parametrize("args", [
    ["push", "origin", "HEAD:refs/heads/a"],
    ["-c", "protocol.allow=always", "-c", "protocol.file.allow=always", "push", "origin", "HEAD:refs/heads/b"],
    ["--no-pager", "-c", "protocol.allow=always", "push", "origin", "HEAD:refs/heads/c"],
])
def test_a_blocked_env_stops_every_push(repo, tmp_path, args):
    remote, clone = repo
    env = block_push(gh_config_dir=str(tmp_path / "gh"))
    r = git(*args, env=env, cwd=clone.as_posix())
    assert r.returncode != 0
    assert refs(remote) == []


def test_send_pack_is_blocked_too(repo, tmp_path):
    remote, clone = repo
    r = git("-C", clone.as_posix(), "send-pack", remote.as_posix(), "HEAD:refs/heads/s",
            env=block_push(gh_config_dir=str(tmp_path / "gh")))
    assert r.returncode != 0 and refs(remote) == []


def test_a_repo_level_protocol_override_cannot_reopen_it(repo, tmp_path):
    remote, clone = repo
    assert git("-C", clone.as_posix(), "config", "protocol.file.allow", "always").returncode == 0
    r = git("-C", clone.as_posix(), "push", "origin", "HEAD:refs/heads/r",
            env=block_push(gh_config_dir=str(tmp_path / "gh")))
    assert r.returncode != 0 and refs(remote) == []


def test_local_git_still_works_under_the_block(repo, tmp_path):
    _, clone = repo
    env = block_push(gh_config_dir=str(tmp_path / "gh"))
    assert git("-C", clone.as_posix(), "tag", "push-notification-fix", env=env).returncode == 0


def test_the_block_is_a_copy_and_strips_gh_tokens(tmp_path):
    env = block_push({"GH_TOKEN": "t", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.x",
                      "GIT_CONFIG_VALUE_0": "y"}, gh_config_dir=str(tmp_path / "gh"))
    assert "GH_TOKEN" not in env and env[MARKER] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "core.x"              # existing env config is kept, appended after
    assert MARKER not in os.environ
