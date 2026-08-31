"""Resolve one authorized target into an immutable, run-owned Git checkout.

This module is deliberately narrower than a repository adapter. It implements the PR1 execution
truth boundary: exact URL/ref observation, copied objects, recursive commit peeling, workdir
containment, and a target-state receipt. It never selects a default branch or ambient ``HEAD``.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

from factory_core.manifest import digest_obj
from factory_core.target import TargetManifest
from factory_runtime.resources import ResourceLedger, ResourceLedgerError
from factory_runtime.schema import DocumentValidationError, validate_document

_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SCP_URL = re.compile(
    r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^?#]+)$"
)


class TargetResolutionError(ValueError):
    """Target bytes could not be selected without guessing or crossing authority."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def normalize_repository_url(value: str) -> str:
    """Return a stable credential-free Git URL or refuse it.

    HTTP redirects are disabled by the resolver, so this normalized URL is also the canonical
    contact destination. SCP-style SSH is accepted only with the conventional non-secret ``git``
    user and normalized into explicit ``ssh://`` form.
    """

    candidate = value.strip()
    scp = _SCP_URL.fullmatch(candidate)
    if scp:
        if scp.group("user") != "git":
            raise TargetResolutionError(
                "url-credentials", "SCP-style repository URLs may use only the non-secret git user"
            )
        candidate = f"ssh://git@{scp.group('host')}/{scp.group('path')}"
    parsed = urlsplit(candidate)
    if parsed.query or parsed.fragment:
        raise TargetResolutionError(
            "url-ambiguous", "repository URL may not contain query/fragment"
        )
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "ssh", "file"}:
        raise TargetResolutionError(
            "url-scheme", "repository URL scheme must be https, ssh, or file"
        )
    if parsed.password:
        raise TargetResolutionError("url-credentials", "repository URL embeds a password/token")
    if scheme == "https" and parsed.username:
        raise TargetResolutionError("url-credentials", "HTTPS repository URL embeds a username")
    if scheme == "ssh" and parsed.username not in {None, "git"}:
        raise TargetResolutionError(
            "url-credentials", "SSH repository URL may use only the non-secret git user"
        )
    if scheme == "file":
        if parsed.username or parsed.hostname not in {None, "", "localhost"}:
            raise TargetResolutionError("url-credentials", "file URL may not name a remote host")
        local_path = Path(unquote(parsed.path)).expanduser().resolve()
        if not local_path.is_absolute():
            raise TargetResolutionError("url-path", "file repository URL must be absolute")
        return f"file://{local_path.as_posix()}"
    if not parsed.hostname:
        raise TargetResolutionError("url-host", "repository URL has no hostname")
    host = parsed.hostname.lower()
    port = parsed.port
    if (scheme == "https" and port == 443) or (scheme == "ssh" and port == 22):
        port = None
    user = "git@" if scheme == "ssh" and parsed.username == "git" else ""
    netloc = f"{user}{host}" + (f":{port}" if port else "")
    path_text = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
    if not path_text or path_text == "/":
        raise TargetResolutionError("url-path", "repository URL has no repository path")
    return urlunsplit(SplitResult(scheme, netloc, path_text, "", ""))


def normalize_subpath(value: str) -> str:
    """Canonicalize a relative POSIX repository subpath without resolving filesystem links."""

    if "\\" in value:
        raise TargetResolutionError("subpath-invalid", "subpath must use POSIX separators")
    if value in {"", "."}:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise TargetResolutionError(
            "subpath-escape", "subpath must be canonical, relative, and contain no dot segments"
        )
    normalized = path.as_posix()
    if normalized != value.rstrip("/"):
        raise TargetResolutionError("subpath-invalid", "subpath is not in canonical form")
    return normalized


def _manifest_subpath(manifest: TargetManifest) -> str:
    return normalize_subpath(str(manifest.repo.get("subpath", "")))


class TargetResolver:
    """Git-backed resolver with an injectable child-process seam for denial tests."""

    def __init__(
        self,
        run_dir: str | Path,
        run_id: str,
        *,
        repository_id: str,
        generation: int,
        clock: Callable[[], int] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.run_id = run_id
        self.repository_id = repository_id
        self.generation = generation
        self._clock = clock or (lambda: int(time.time()))
        self._runner = runner
        self.resources = ResourceLedger(self.run_dir, run_id, clock=self._clock)

    def _git(self, arguments: Sequence[str], *, contact: bool = False) -> str:
        command = [
            "git",
            "-c",
            "credential.helper=",
            "-c",
            "http.followRedirects=false",
            *arguments,
        ]
        environment = dict(os.environ)
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": os.devnull,
                "SSH_ASKPASS": os.devnull,
            }
        )
        try:
            result = self._runner(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=120,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            kind = "contact-failed" if contact else "git-failed"
            raise TargetResolutionError(kind, str(exc)) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "git command failed").strip()
            kind = "contact-failed" if contact else "git-failed"
            raise TargetResolutionError(kind, detail[:1000])
        return result.stdout.strip()

    @staticmethod
    def _resource_baseline(**values: Any) -> dict[str, Any]:
        return {key: value for key, value in values.items()}

    def _append_resource(self, **values: Any) -> str:
        try:
            return self.resources.append(generation=self.generation, **values)
        except ResourceLedgerError as exc:
            raise TargetResolutionError("resource-ledger", str(exc)) from exc

    def _source_remote(self, source: Path) -> str:
        raw = self._git(["-C", str(source), "remote", "get-url", "origin"], contact=True)
        return normalize_repository_url(raw)

    def _local_ref(self, source: Path, requested_ref: str) -> tuple[str, str, str]:
        if _OBJECT_ID.fullmatch(requested_ref):
            observed = requested_ref
            try:
                peeled = self._git(
                    ["-C", str(source), "rev-parse", "--verify", f"{requested_ref}^{{commit}}"],
                    contact=True,
                )
            except TargetResolutionError as exc:
                raise TargetResolutionError("ref-not-a-commit", requested_ref) from exc
            return requested_ref, observed, peeled
        if requested_ref.startswith("refs/"):
            candidates = [requested_ref]
        else:
            candidates = [
                f"refs/remotes/origin/{requested_ref}",
                f"refs/tags/{requested_ref}",
                f"refs/heads/{requested_ref}",
            ]
        matches: list[tuple[str, str]] = []
        for candidate in candidates:
            try:
                observed = self._git(
                    ["-C", str(source), "show-ref", "--verify", "--hash", candidate],
                    contact=True,
                )
            except TargetResolutionError:
                continue
            matches.append((candidate, observed))
        if not matches:
            raise TargetResolutionError("ref-not-found", requested_ref)
        unique = {(name, object_id) for name, object_id in matches}
        if len(unique) != 1:
            raise TargetResolutionError(
                "ref-ambiguous",
                f"requested ref {requested_ref!r} matches more than one exact local ref",
            )
        selected, observed = matches[0]
        try:
            peeled = self._git(
                ["-C", str(source), "rev-parse", "--verify", f"{selected}^{{commit}}"],
                contact=True,
            )
        except TargetResolutionError as exc:
            raise TargetResolutionError("ref-not-a-commit", selected) from exc
        return selected, observed, peeled

    @staticmethod
    def _parse_ls_remote(output: str, requested_ref: str) -> tuple[str, str, str]:
        rows: dict[str, str] = {}
        for line in output.splitlines():
            fields = line.split("\t", 1)
            if len(fields) == 2 and _OBJECT_ID.fullmatch(fields[0]):
                rows[fields[1]] = fields[0]
        if requested_ref.startswith("refs/"):
            candidates = [requested_ref]
        else:
            candidates = [f"refs/heads/{requested_ref}", f"refs/tags/{requested_ref}"]
        matches = [(name, rows[name]) for name in candidates if name in rows]
        if not matches:
            raise TargetResolutionError("ref-not-found", requested_ref)
        if len(matches) != 1:
            raise TargetResolutionError("ref-ambiguous", requested_ref)
        selected, observed = matches[0]
        peeled = rows.get(f"{selected}^{{}}", observed)
        return selected, observed, peeled

    def _remote_ref(self, url: str, requested_ref: str) -> tuple[str, str, str]:
        if _OBJECT_ID.fullmatch(requested_ref):
            return requested_ref, requested_ref, requested_ref
        patterns = (
            [requested_ref, f"{requested_ref}^{{}}"]
            if requested_ref.startswith("refs/")
            else [
                f"refs/heads/{requested_ref}",
                f"refs/tags/{requested_ref}",
                f"refs/tags/{requested_ref}^{{}}",
            ]
        )
        output = self._git(["ls-remote", "--exit-code", url, *patterns], contact=True)
        return self._parse_ls_remote(output, requested_ref)

    def _append_failure(self, resource_id: str, code: str) -> None:
        prior = self.resources.latest().get(resource_id)
        if not prior or prior["status"] not in {"planned", "active"}:
            return
        path_exists = Path(str(prior["identifier"])).exists()
        if prior["status"] == "planned" and not path_exists:
            status = "abandoned"
        elif prior["status"] == "planned":
            status = "active"
        else:
            return
        if status == "active":
            self._append_resource(
                resource_id=resource_id,
                resource_type=str(prior["resource_type"]),
                identifier=str(prior["identifier"]),
                creator_action=str(prior["creator_action"]),
                ownership=str(prior["ownership"]),
                baseline=dict(prior["baseline"]),
                disposition={},
                status="active",
                evidence_digests={},
                actor="target-resolver",
            )
            return
        self._append_resource(
            resource_id=resource_id,
            resource_type=str(prior["resource_type"]),
            identifier=str(prior["identifier"]),
            creator_action=str(prior["creator_action"]),
            ownership=str(prior["ownership"]),
            baseline=dict(prior["baseline"]),
            disposition={"reason": code, "residue": False},
            status="abandoned",
            evidence_digests={},
            actor="target-resolver",
        )

    def resolve(
        self,
        *,
        manifest: TargetManifest,
        request: Mapping[str, Any],
        object_source: str | Path | None = None,
    ) -> dict[str, Any]:
        """Resolve, copy, verify, and materialize one exact target state."""

        expected_url = normalize_repository_url(str(manifest.repo["url"]))
        requested_ref = str(manifest.repo["ref"])
        subpath = _manifest_subpath(manifest)
        if request.get("normalized_url") != expected_url:
            raise TargetResolutionError("request-url-mismatch", expected_url)
        if request.get("requested_ref") != requested_ref:
            raise TargetResolutionError("request-ref-mismatch", requested_ref)
        if request.get("subpath") != subpath:
            raise TargetResolutionError("request-subpath-mismatch", subpath)
        if request.get("target_manifest_digest") != manifest.source_digest:
            raise TargetResolutionError("request-manifest-mismatch", manifest.source_digest)

        allowed = frozenset(str(value) for value in request["allowed_contact_operations"])
        mode = "local-object-source" if object_source is not None else "remote"
        required = (
            frozenset({"git-local-object-read"})
            if object_source is not None
            else frozenset({"git-ls-remote", "git-fetch"})
        )
        if not required.issubset(allowed):
            raise TargetResolutionError(
                "contact-operation-not-authorized",
                ", ".join(sorted(required - allowed)),
            )

        target_dir = self.run_dir / "target"
        object_store = target_dir / "objects.git"
        source_root = target_dir / "source"
        for path in (target_dir, object_store, source_root):
            if path.exists() or path.is_symlink():
                raise TargetResolutionError("checkout-path-reuse", str(path))

        contact_id = "target-contact"
        object_id = "target-objects"
        source_id = "target-source"
        contact_identifier = str(Path(object_source).resolve()) if object_source else expected_url
        contact_baseline = self._resource_baseline(
            normalized_url=expected_url,
            requested_ref=requested_ref,
            allowed_operations=sorted(allowed),
            mode=mode,
        )
        self._append_resource(
            resource_id=contact_id,
            resource_type="repository-contact",
            identifier=contact_identifier,
            creator_action="resolve-target",
            ownership="external-non-owned",
            baseline=contact_baseline,
            disposition={},
            status="planned",
            evidence_digests={"target-resolution-request": digest_obj(dict(request))},
            actor="target-resolver",
        )

        try:
            if object_source is not None:
                source = Path(object_source).expanduser().resolve(strict=True)
                if not source.is_dir():
                    raise TargetResolutionError("object-source-invalid", str(source))
                first_remote = self._source_remote(source)
                if first_remote != expected_url:
                    raise TargetResolutionError(
                        "object-source-url-mismatch", f"{first_remote!r} != {expected_url!r}"
                    )
                selected_ref, observed, peeled = self._local_ref(source, requested_ref)
            else:
                source = None
                selected_ref, observed, peeled = self._remote_ref(expected_url, requested_ref)

            self._append_resource(
                resource_id=object_id,
                resource_type="object-store",
                identifier=str(object_store),
                creator_action="resolve-target",
                ownership="run-owned",
                baseline=self._resource_baseline(absent_at_plan=True),
                disposition={},
                status="planned",
                evidence_digests={},
                actor="target-resolver",
            )
            target_dir.mkdir(parents=False)
            self._git(["init", "--bare", str(object_store)])
            if source is not None:
                # Force the upload-pack protocol even for a local repository. A local clone may
                # hardlink objects or copy ambient refs/HEAD; this fetch requests exactly the
                # already-observed ref and writes only into the run-owned object store.
                local_url = f"file://{source.as_posix()}"
                local_refspec = (
                    selected_ref
                    if _OBJECT_ID.fullmatch(selected_ref)
                    else f"{selected_ref}:refs/factory/requested"
                )
                self._git(
                    [
                        "--git-dir",
                        str(object_store),
                        "fetch",
                        "--no-tags",
                        "--no-write-fetch-head",
                        "--depth=1",
                        local_url,
                        local_refspec,
                    ],
                    contact=True,
                )
            else:
                self._git(["--git-dir", str(object_store), "remote", "add", "origin", expected_url])
                refspec = (
                    requested_ref
                    if _OBJECT_ID.fullmatch(requested_ref)
                    else f"{selected_ref}:refs/factory/requested"
                )
                self._git(
                    [
                        "--git-dir",
                        str(object_store),
                        "fetch",
                        "--no-tags",
                        "--depth=1",
                        "origin",
                        refspec,
                    ],
                    contact=True,
                )
            if (object_store / "objects" / "info" / "alternates").exists():
                raise TargetResolutionError("object-store-shared", "Git alternates are forbidden")
            try:
                resolved_commit = self._git(
                    [
                        "--git-dir",
                        str(object_store),
                        "rev-parse",
                        "--verify",
                        f"{peeled}^{{commit}}",
                    ]
                )
            except TargetResolutionError as exc:
                raise TargetResolutionError("ref-not-a-commit", selected_ref) from exc
            if resolved_commit != peeled:
                raise TargetResolutionError("ref-not-a-commit", selected_ref)
            tree = self._git(
                ["--git-dir", str(object_store), "rev-parse", f"{resolved_commit}^{{tree}}"]
            )
            self._append_resource(
                resource_id=object_id,
                resource_type="object-store",
                identifier=str(object_store),
                creator_action="resolve-target",
                ownership="run-owned",
                baseline=self._resource_baseline(absent_at_plan=True),
                disposition={},
                status="active",
                evidence_digests={
                    "commit-tree": digest_obj({"commit": resolved_commit, "tree": tree})
                },
                actor="target-resolver",
            )

            if source is not None:
                second_remote = self._source_remote(source)
                _, observed_after, peeled_after = self._local_ref(source, requested_ref)
                if (
                    second_remote != first_remote
                    or observed_after != observed
                    or peeled_after != peeled
                ):
                    raise TargetResolutionError("ref-moved", requested_ref)
            else:
                _, observed_after, peeled_after = self._remote_ref(expected_url, requested_ref)
                if observed_after != observed or peeled_after != peeled:
                    raise TargetResolutionError("ref-moved", requested_ref)
            contact_head = self._append_resource(
                resource_id=contact_id,
                resource_type="repository-contact",
                identifier=contact_identifier,
                creator_action="resolve-target",
                ownership="external-non-owned",
                baseline=contact_baseline,
                disposition={},
                status="succeeded",
                evidence_digests={
                    "observed-target": digest_obj(
                        {"selected_ref": selected_ref, "observed": observed, "peeled": peeled}
                    )
                },
                actor="target-resolver",
            )

            self._append_resource(
                resource_id=source_id,
                resource_type="source-worktree",
                identifier=str(source_root),
                creator_action="resolve-target",
                ownership="run-owned",
                baseline=self._resource_baseline(absent_at_plan=True),
                disposition={},
                status="planned",
                evidence_digests={},
                actor="target-resolver",
            )
            self._git(
                [
                    "--git-dir",
                    str(object_store),
                    "worktree",
                    "add",
                    "--detach",
                    str(source_root),
                    resolved_commit,
                ]
            )
            workdir = source_root if not subpath else source_root / subpath
            if not workdir.is_dir():
                raise TargetResolutionError("subpath-missing", str(workdir))
            current = source_root
            for part in PurePosixPath(subpath).parts if subpath else ():
                current = current / part
                if current.is_symlink():
                    raise TargetResolutionError("subpath-symlink", str(current))
            resolved_workdir = workdir.resolve(strict=True)
            if not resolved_workdir.is_relative_to(source_root.resolve(strict=True)):
                raise TargetResolutionError("subpath-escape", str(workdir))
            head = self._git(["-C", str(source_root), "rev-parse", "HEAD^{commit}"])
            dirt = self._git(
                ["-C", str(source_root), "status", "--porcelain", "--untracked-files=all"]
            )
            if head != resolved_commit or dirt:
                raise TargetResolutionError("target-state-diverged", str(source_root))
            self._append_resource(
                resource_id=source_id,
                resource_type="source-worktree",
                identifier=str(source_root),
                creator_action="resolve-target",
                ownership="run-owned",
                baseline=self._resource_baseline(absent_at_plan=True),
                disposition={},
                status="active",
                evidence_digests={
                    "checkout": digest_obj({"commit": resolved_commit, "tree": tree})
                },
                actor="target-resolver",
            )
            resource_head = self.resources.head()
            checkout_id = digest_obj(
                {
                    "run_id": self.run_id,
                    "generation": self.generation,
                    "commit": resolved_commit,
                    "source_root": str(source_root),
                }
            )
            target_state = {
                "schema_version": "factory-target-state/1",
                "run_id": self.run_id,
                "repository_id": self.repository_id,
                "generation": self.generation,
                "target_id": manifest.target_id,
                "target_manifest_digest": manifest.source_digest,
                "requested_url": expected_url,
                "canonical_url": expected_url,
                "requested_ref": requested_ref,
                "observed_ref_object": observed,
                "peeled_object": peeled,
                "resolved_commit": resolved_commit,
                "resolved_tree": tree,
                "control_root": str(self.run_dir),
                "object_store": str(object_store),
                "source_root": str(source_root),
                "subpath": subpath,
                "workdir": str(resolved_workdir),
                "checkout_id": checkout_id,
                "observation_method": mode,
                "remote_freshness": "UNPROVED" if source is not None else "PROVED",
                "contact_ledger_head": contact_head,
                "resource_ledger_head": resource_head,
                "created_at": self._clock(),
            }
            validate_document("target-state", target_state)
            return target_state
        except (DocumentValidationError, OSError, TargetResolutionError) as exc:
            code = exc.code if isinstance(exc, TargetResolutionError) else "resolution-failed"
            try:
                latest = self.resources.latest()
                contact = latest.get(contact_id)
                if contact and contact["status"] == "planned":
                    self._append_resource(
                        resource_id=contact_id,
                        resource_type="repository-contact",
                        identifier=contact_identifier,
                        creator_action="resolve-target",
                        ownership="external-non-owned",
                        baseline=contact_baseline,
                        disposition={"reason": code, "residue": False},
                        status="failed",
                        evidence_digests={},
                        actor="target-resolver",
                    )
                self._append_failure(object_id, code)
                self._append_failure(source_id, code)
            except (ResourceLedgerError, TargetResolutionError):
                pass
            if isinstance(exc, TargetResolutionError):
                raise
            raise TargetResolutionError(code, str(exc)) from exc


def verify_target_state(
    target_state: Mapping[str, Any],
    *,
    expected_digest: str = "",
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Re-derive a retained target state and prove its immutable baseline is still intact."""

    try:
        validate_document("target-state", target_state)
    except DocumentValidationError as exc:
        raise TargetResolutionError("target-state-invalid", str(exc)) from exc
    actual_digest = digest_obj(dict(target_state))
    if expected_digest and actual_digest != expected_digest:
        raise TargetResolutionError("target-state-digest-mismatch", actual_digest)
    control_root = Path(str(target_state["control_root"])).resolve(strict=True)
    object_store = Path(str(target_state["object_store"])).resolve(strict=True)
    source_root = Path(str(target_state["source_root"])).resolve(strict=True)
    workdir = Path(str(target_state["workdir"])).resolve(strict=True)
    for path in (object_store, source_root, workdir):
        if not path.is_relative_to(control_root):
            raise TargetResolutionError("target-state-path-escape", str(path))
    subpath = normalize_subpath(str(target_state["subpath"]))
    if workdir != (source_root if not subpath else source_root / subpath).resolve(strict=True):
        raise TargetResolutionError("target-state-workdir-mismatch", str(workdir))

    def run_git(arguments: Sequence[str]) -> str:
        result = runner(
            ["git", "-c", "credential.helper=", *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        )
        if result.returncode != 0:
            raise TargetResolutionError(
                "target-state-git-failed", (result.stderr or result.stdout).strip()[:1000]
            )
        return result.stdout.strip()

    commit = str(target_state["resolved_commit"])
    object_commit = run_git(
        ["--git-dir", str(object_store), "rev-parse", "--verify", f"{commit}^{{commit}}"]
    )
    object_tree = run_git(
        ["--git-dir", str(object_store), "rev-parse", f"{commit}^{{tree}}"]
    )
    source_commit = run_git(["-C", str(source_root), "rev-parse", "HEAD^{commit}"])
    dirt = run_git(["-C", str(source_root), "status", "--porcelain", "--untracked-files=all"])
    if (
        object_commit != commit
        or object_tree != target_state["resolved_tree"]
        or source_commit != commit
        or dirt
    ):
        raise TargetResolutionError("target-state-diverged", str(source_root))


__all__ = [
    "TargetResolutionError",
    "TargetResolver",
    "normalize_repository_url",
    "normalize_subpath",
    "verify_target_state",
]
