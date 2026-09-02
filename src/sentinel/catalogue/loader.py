"""Loading and validating the control catalogue.

The catalogue is data, not code: versioned YAML that a security manager can
read and argue with, loaded through a closed schema. Two properties are worth
the code they cost.

**It fails at load, not in the middle of an export.** Every identifier, theme,
function, capability and evidence kind is checked against the manifest's
vocabularies when the catalogue is read. A catalogue naming a capability
nobody defined fails immediately, rather than producing a Statement of
Applicability with a column an auditor asks about and nobody can explain.

**It is fingerprinted.** The version number is what a human quotes; the
SHA-256 over the bytes of every catalogue file is what an assessment records.
Two catalogues can carry the same version and different content -- somebody
edited a file and did not bump it -- and only one of them is the one that ran.
Comparing fingerprints is how a six-month-old SoA can be matched to the
catalogue that produced it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from sentinel.config import Settings, get_settings
from sentinel.exceptions import CatalogueError
from sentinel.logging_config import get_logger

logger = get_logger(__name__)

CONTROL_ID = "A."
REQUIREMENT_ID = "C."


@dataclass(frozen=True)
class Control:
    """One Annex A control, as this project describes it."""

    id: str
    title: str
    intent: str
    theme: str
    control_types: tuple[str, ...]
    properties: tuple[str, ...]
    function: str
    capability: str
    evidence_expected: tuple[str, ...]
    evidence_valid_days: int

    @property
    def number(self) -> tuple[int, ...]:
        """(5, 12) for A.5.12 -- so that A.5.2 sorts before A.5.12."""
        return tuple(int(part) for part in self.id.removeprefix(CONTROL_ID).split("."))


@dataclass(frozen=True)
class Requirement:
    """One management-system requirement from clauses 4 to 10."""

    id: str
    clause: str
    title: str
    intent: str
    evidence_expected: tuple[str, ...]
    mandatory_for_certification: bool

    @property
    def number(self) -> tuple[int, ...]:
        return tuple(int(part) for part in self.clause.split("."))


@dataclass(frozen=True)
class Catalogue:
    """Everything the standard asks for, as this project models it."""

    version: str
    standard: str
    framework_note: str
    fingerprint: str
    controls: tuple[Control, ...]
    requirements: tuple[Requirement, ...]
    themes: dict[str, str] = field(default_factory=dict)
    capabilities: dict[str, str] = field(default_factory=dict)
    functions: dict[str, str] = field(default_factory=dict)
    evidence_kinds: dict[str, str] = field(default_factory=dict)

    # --- lookups ----------------------------------------------------------

    def control(self, control_id: str) -> Control:
        try:
            return self._by_control_id[control_id]
        except KeyError as exc:
            raise CatalogueError(f"no control {control_id!r} in catalogue {self.version}") from exc

    def requirement(self, requirement_id: str) -> Requirement:
        try:
            return self._by_requirement_id[requirement_id]
        except KeyError as exc:
            raise CatalogueError(
                f"no requirement {requirement_id!r} in catalogue {self.version}"
            ) from exc

    def has_control(self, control_id: str) -> bool:
        return control_id in self._by_control_id

    def by_theme(self, theme: str) -> tuple[Control, ...]:
        return tuple(c for c in self.controls if c.theme == theme)

    def by_capability(self, capability: str) -> tuple[Control, ...]:
        return tuple(c for c in self.controls if c.capability == capability)

    @property
    def mandatory_requirements(self) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements if r.mandatory_for_certification)

    @property
    def control_ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.controls)

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.requirements)

    @property
    def _by_control_id(self) -> dict[str, Control]:
        return {c.id: c for c in self.controls}

    @property
    def _by_requirement_id(self) -> dict[str, Requirement]:
        return {r.id: r for r in self.requirements}

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for control in self.controls:
            counts[control.theme] = counts.get(control.theme, 0) + 1
        return {
            "version": self.version,
            "fingerprint": self.fingerprint,
            "controls": len(self.controls),
            "by_theme": counts,
            "requirements": len(self.requirements),
            "mandatory_requirements": len(self.mandatory_requirements),
        }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CatalogueError("file not found", path=str(path))
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CatalogueError(f"invalid YAML: {exc}", path=str(path)) from exc
    if not isinstance(loaded, dict):
        raise CatalogueError("expected a mapping at the top level", path=str(path))
    return loaded


def _require(mapping: dict[str, Any], key: str, path: Path) -> Any:
    if key not in mapping:
        raise CatalogueError(f"missing key {key!r}", path=str(path))
    return mapping[key]


def _check_vocabulary(
    values: list[str] | tuple[str, ...],
    vocabulary: dict[str, str] | set[str],
    *,
    what: str,
    owner: str,
    path: Path,
) -> None:
    unknown = sorted(set(values) - set(vocabulary))
    if unknown:
        raise CatalogueError(
            f"{owner}: unknown {what}: {', '.join(unknown)}; "
            f"the manifest defines {', '.join(sorted(vocabulary))}",
            path=str(path),
        )


def _fingerprint(paths: list[Path]) -> str:
    """SHA-256 over every catalogue file, in a fixed order.

    The file's path relative to the catalogue directory goes into the digest
    as well as its bytes, so that renaming a file changes the fingerprint.
    Two catalogues holding the same controls under different filenames are
    not the same catalogue -- the second one loads different files.
    """
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_catalogue(directory: Path | None = None, settings: Settings | None = None) -> Catalogue:
    """Read, validate and fingerprint the catalogue."""
    settings = settings or get_settings()
    directory = directory or settings.catalogue_path
    manifest_path = directory / "catalogue.yaml"
    manifest = _read_yaml(manifest_path)

    themes: dict[str, str] = _require(manifest, "themes", manifest_path)
    capabilities: dict[str, str] = _require(manifest, "capabilities", manifest_path)
    functions: dict[str, str] = _require(manifest, "functions", manifest_path)
    evidence_kinds: dict[str, str] = _require(manifest, "evidence_kinds", manifest_path)
    control_types: dict[str, str] = _require(manifest, "control_types", manifest_path)
    properties: dict[str, str] = _require(manifest, "properties", manifest_path)

    files = [manifest_path]
    controls: list[Control] = []
    seen: set[str] = set()

    for relative in _require(manifest, "control_files", manifest_path):
        path = directory / relative
        files.append(path)
        document = _read_yaml(path)
        theme = _require(document, "theme", path)
        _check_vocabulary([theme], themes, what="theme", owner=relative, path=path)

        for raw in _require(document, "controls", path):
            control = _control_from(raw, theme=theme, path=path)
            if control.id in seen:
                raise CatalogueError(f"{control.id} is defined twice", path=str(path))
            seen.add(control.id)
            _check_vocabulary(
                control.control_types,
                control_types,
                what="control type",
                owner=control.id,
                path=path,
            )
            _check_vocabulary(
                control.properties, properties, what="property", owner=control.id, path=path
            )
            _check_vocabulary(
                [control.function], functions, what="function", owner=control.id, path=path
            )
            _check_vocabulary(
                [control.capability], capabilities, what="capability", owner=control.id, path=path
            )
            _check_vocabulary(
                control.evidence_expected,
                evidence_kinds,
                what="evidence kind",
                owner=control.id,
                path=path,
            )
            controls.append(control)

    clause_path = directory / _require(manifest, "clause_file", manifest_path)
    files.append(clause_path)
    clause_document = _read_yaml(clause_path)
    requirements: list[Requirement] = []
    seen_requirements: set[str] = set()
    for raw in _require(clause_document, "requirements", clause_path):
        requirement = _requirement_from(raw, path=clause_path)
        if requirement.id in seen_requirements:
            raise CatalogueError(f"{requirement.id} is defined twice", path=str(clause_path))
        seen_requirements.add(requirement.id)
        _check_vocabulary(
            requirement.evidence_expected,
            evidence_kinds,
            what="evidence kind",
            owner=requirement.id,
            path=clause_path,
        )
        requirements.append(requirement)

    if not controls:
        raise CatalogueError("the catalogue defines no controls", path=str(directory))
    if not requirements:
        raise CatalogueError("the catalogue defines no clause requirements", path=str(directory))

    catalogue = Catalogue(
        version=str(_require(manifest, "version", manifest_path)),
        standard=str(_require(manifest, "standard", manifest_path)),
        framework_note=str(_require(manifest, "framework_note", manifest_path)),
        fingerprint=_fingerprint(files),
        controls=tuple(sorted(controls, key=lambda c: c.number)),
        requirements=tuple(sorted(requirements, key=lambda r: r.number)),
        themes=themes,
        capabilities=capabilities,
        functions=functions,
        evidence_kinds=evidence_kinds,
    )
    logger.info(
        "catalogue loaded",
        extra={
            "version": catalogue.version,
            "fingerprint": catalogue.fingerprint[:12],
            "controls": len(catalogue.controls),
            "requirements": len(catalogue.requirements),
        },
    )
    return catalogue


def _control_from(raw: Any, *, theme: str, path: Path) -> Control:
    if not isinstance(raw, dict):
        raise CatalogueError("each control must be a mapping", path=str(path))
    control_id = str(_require(raw, "id", path))
    if not control_id.startswith(CONTROL_ID):
        raise CatalogueError(
            f"control identifier {control_id!r} must start with {CONTROL_ID!r}", path=str(path)
        )
    try:
        valid_days = int(_require(raw, "evidence_valid_days", path))
    except (TypeError, ValueError) as exc:
        raise CatalogueError(
            f"{control_id}: evidence_valid_days must be a number", path=str(path)
        ) from exc
    if valid_days <= 0:
        raise CatalogueError(
            f"{control_id}: evidence_valid_days must be positive; evidence that is stale "
            "on the day it is collected cannot support anything",
            path=str(path),
        )
    return Control(
        id=control_id,
        title=str(_require(raw, "title", path)),
        intent=str(_require(raw, "intent", path)).strip(),
        theme=theme,
        control_types=tuple(_require(raw, "control_types", path)),
        properties=tuple(str(p) for p in _require(raw, "properties", path)),
        function=str(_require(raw, "function", path)),
        capability=str(_require(raw, "capability", path)),
        evidence_expected=tuple(_require(raw, "evidence_expected", path)),
        evidence_valid_days=valid_days,
    )


def _requirement_from(raw: Any, *, path: Path) -> Requirement:
    if not isinstance(raw, dict):
        raise CatalogueError("each requirement must be a mapping", path=str(path))
    requirement_id = str(_require(raw, "id", path))
    if not requirement_id.startswith(REQUIREMENT_ID):
        raise CatalogueError(
            f"requirement identifier {requirement_id!r} must start with {REQUIREMENT_ID!r}",
            path=str(path),
        )
    return Requirement(
        id=requirement_id,
        clause=str(_require(raw, "clause", path)),
        title=str(_require(raw, "title", path)),
        intent=str(_require(raw, "intent", path)).strip(),
        evidence_expected=tuple(_require(raw, "evidence_expected", path)),
        mandatory_for_certification=bool(_require(raw, "mandatory_for_certification", path)),
    )


@lru_cache(maxsize=4)
def _cached(directory: str) -> Catalogue:
    return load_catalogue(Path(directory))


def get_catalogue(settings: Settings | None = None) -> Catalogue:
    """The catalogue, read once per directory.

    Cached because an assessment consults it thousands of times and the files
    do not change while a process runs. ``reset_catalogue`` exists for tests
    that write a catalogue and expect to see it.
    """
    settings = settings or get_settings()
    return _cached(str(settings.catalogue_path))


def reset_catalogue() -> None:
    _cached.cache_clear()
