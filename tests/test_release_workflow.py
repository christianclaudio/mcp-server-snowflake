"""Release workflow shape (template v1.6.0): provenance comes from a separate attest job."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "release.yml"


def _load(path: Path = WORKFLOW) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def _jobs(path: Path = WORKFLOW) -> dict[str, Any]:
    jobs: dict[str, Any] = _load(path)["jobs"]
    return jobs


def _steps_using(job: dict[str, Any], action: str) -> list[dict[str, Any]]:
    return [s for s in job.get("steps", []) if str(s.get("uses", "")).startswith(action + "@")]


def test_only_the_attest_job_can_write_attestations() -> None:
    for name, job in _jobs().items():
        perms = job.get("permissions", {})
        if name == "attest":
            assert perms == {"contents": "read", "id-token": "write", "attestations": "write"}
        else:
            assert "attestations" not in perms, name
            assert "id-token" not in perms, name
            assert not _steps_using(job, "actions/attest-build-provenance"), name


def test_attest_job_covers_dist_and_image_after_publish() -> None:
    jobs = _jobs()
    attest = jobs["attest"]
    assert set(attest["needs"]) == {"build", "docker"}
    assert "publish" in jobs["docker"]["needs"]
    withs = [s["with"] for s in _steps_using(attest, "actions/attest-build-provenance")]
    assert {"subject-path": "dist/*"} in withs
    assert {
        "subject-name": "${{ needs.docker.outputs.image }}",
        "subject-digest": "${{ needs.docker.outputs.digest }}",
    } in withs
    assert all("push-to-registry" not in w for w in withs)


def test_commented_registry_job_waits_for_attest() -> None:
    """The MCP Registry job is commented out; when enabled it must need ``attest``."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "#   needs: [docker, attest]" in text
    assert "registry" not in _jobs()


def test_publish_drops_attestations() -> None:
    publish = _jobs()["publish"]
    assert publish["permissions"] == {"contents": "write"}


def test_docker_job_exposes_digest_and_pushes_a_plain_manifest() -> None:
    docker = _jobs()["docker"]
    assert docker["permissions"] == {"contents": "read", "packages": "write"}
    assert docker["outputs"]["digest"] == "${{ steps.build.outputs.digest }}"
    assert docker["outputs"]["image"] == "${{ steps.image.outputs.name }}"
    (build,) = _steps_using(docker, "docker/build-push-action")
    assert build["id"] == "build"
    assert build["with"]["provenance"] is False
    assert build["with"]["sbom"] is False


_PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit_sha(path: Path) -> None:
    """Step-level and job-level (reusable workflow) ``uses`` are both pinned to a full SHA."""
    for name, job in _jobs(path).items():
        if "uses" in job and not str(job["uses"]).startswith("./"):
            assert _PINNED.fullmatch(job["uses"]), (name, job["uses"])
        for step in job.get("steps", []):
            if "uses" in step and not str(step["uses"]).startswith("./"):
                assert _PINNED.fullmatch(step["uses"]), step["uses"]


def test_pin_check_catches_job_level_and_tag_refs() -> None:
    """The matcher itself refuses tag refs, so the checks above cannot pass vacuously."""
    assert not _PINNED.fullmatch("actions/checkout@v7")
    assert not _PINNED.fullmatch("org/repo/.github/workflows/x.yml@main")
    assert _PINNED.fullmatch("org/repo/.github/workflows/x.yml@" + "a" * 40)


def test_workflow_level_permissions_are_read_only() -> None:
    """The top-level block grants only ``contents: read``; each job asks for its own writes."""
    assert _load()["permissions"] == {"contents": "read"}


def test_every_release_output_is_kept() -> None:
    """The GitHub Release (wheel, sdist, SBOM, notes) and the GHCR image still ship."""
    jobs = _jobs()
    (release,) = _steps_using(jobs["publish"], "softprops/action-gh-release")
    assert release["with"] == {"files": "dist/*\nsbom.cdx.json\n", "body_path": "release-notes.md"}
    assert any("release_notes.py" in str(s.get("run", "")) for s in jobs["publish"]["steps"])
    (build,) = _steps_using(jobs["docker"], "docker/build-push-action")
    assert build["with"]["push"] is True
    tags = build["with"]["tags"]
    assert "${{ steps.image.outputs.name }}:${{ steps.version.outputs.version }}" in tags
    assert "${{ steps.image.outputs.name }}:latest" in tags
    assert "UV_DYNAMIC_VERSIONING_BYPASS" in build["with"]["build-args"]
    build_job = jobs["build"]
    assert any(s.get("name") == "Generate CycloneDX SBOM" for s in build_job["steps"])
    assert any(s.get("name") == "Verify wheel version matches tag" for s in build_job["steps"])
