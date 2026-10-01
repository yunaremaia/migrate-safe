"""Tests for the composite action manifest (action.yml).

The action used to run `pip install migrate-safe`, but that distribution is not
published on PyPI (https://pypi.org/pypi/migrate-safe/json returns 404), so every
consumer of the action failed at the install step. These tests parse action.yml
as plain text — no PyYAML dependency, since CI installs only `-e . pytest` — and
assert the install resolves from git.

README.md is covered separately by PR #33 and is intentionally not asserted on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_YML = REPO_ROOT / "action.yml"

# `pip install migrate-safe` and variants that resolve the bare distribution name
# from an index. The `git+` URL form is deliberately excluded.
BARE_INSTALL_RE = re.compile(
    r"pip\s+install\s+(?:--?[\w-]+(?:[= ]\S+)?\s+)*migrate-safe\b(?![\w-])"
)


@pytest.fixture(scope="module")
def action_text() -> str:
    assert ACTION_YML.exists(), f"missing {ACTION_YML}"
    return ACTION_YML.read_text(encoding="utf-8")


def test_action_manifest_exists(action_text: str) -> None:
    assert "using: 'composite'" in action_text or "using: composite" in action_text


def test_no_bare_pip_install_of_unpublished_distribution(action_text: str) -> None:
    """`pip install migrate-safe` must never come back: the name 404s on PyPI."""
    offenders = [
        f"line {n}: {line.strip()}"
        for n, line in enumerate(action_text.splitlines(), 1)
        if BARE_INSTALL_RE.search(line)
    ]
    assert not offenders, (
        "action.yml installs the unpublished 'migrate-safe' distribution by bare "
        f"name, which fails at runtime: {offenders}"
    )


def test_install_step_uses_git_url(action_text: str) -> None:
    """The CLI must be installed from the repository, not from an index."""
    assert "git+https://github.com/yunaremaia/migrate-safe.git" in action_text, (
        "the install step must resolve migrate-safe from git"
    )


def test_install_ref_follows_the_action_ref(action_text: str) -> None:
    """Pinning to github.action_ref keeps the CLI on the same version as the action."""
    assert "github.action_ref" in action_text, (
        "install should be pinned to the ref the action was referenced by"
    )


def test_git_install_target_actually_resolves() -> None:
    """The repo declares the same distribution name the action installs.

    Guards against the action pointing at a git URL for a project that no longer
    builds a `migrate-safe` distribution with the `migrate-safe` console script.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "migrate-safe"' in pyproject
    assert 'migrate-safe = "migrate_safe.cli:main"' in pyproject, (
        "the action invokes the `migrate-safe` console script; it must be declared"
    )
