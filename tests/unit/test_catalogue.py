"""The catalogue's own coherence, and the fingerprint.

Cheap checks that catch the class of mistake which is otherwise found in a
Statement of Applicability: a control naming a capability nobody defined, an
identifier appearing twice, a file edited without the version being bumped.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sentinel.catalogue import Catalogue, load_catalogue
from sentinel.exceptions import CatalogueError

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestContents:
    def test_annex_a_is_complete(self, catalogue: Catalogue) -> None:
        """93 controls across the four themes, and nothing missing in between."""
        assert len(catalogue.controls) == 93
        assert len(catalogue.by_theme("organizational")) == 37
        assert len(catalogue.by_theme("people")) == 8
        assert len(catalogue.by_theme("physical")) == 14
        assert len(catalogue.by_theme("technological")) == 34

    @pytest.mark.parametrize(
        ("theme", "prefix", "last"),
        [
            ("organizational", "A.5.", 37),
            ("people", "A.6.", 8),
            ("physical", "A.7.", 14),
            ("technological", "A.8.", 34),
        ],
    )
    def test_no_control_number_is_skipped(
        self, catalogue: Catalogue, theme: str, prefix: str, last: int
    ) -> None:
        """A gap in the numbering is a control somebody forgot to write."""
        found = {control.id for control in catalogue.by_theme(theme)}
        expected = {f"{prefix}{index}" for index in range(1, last + 1)}
        assert found == expected

    def test_every_control_has_an_intent(self, catalogue: Catalogue) -> None:
        for control in catalogue.controls:
            assert len(control.intent) > 40, f"{control.id} has no usable intent"
            assert control.title.strip()

    def test_evidence_lifetimes_are_positive_and_bounded(self, catalogue: Catalogue) -> None:
        for control in catalogue.controls:
            assert 30 <= control.evidence_valid_days <= 365, control.id

    def test_the_clause_requirements_are_present(self, catalogue: Catalogue) -> None:
        clauses = {requirement.clause.split(".")[0] for requirement in catalogue.requirements}
        assert clauses == {"4", "5", "6", "7", "8", "9", "10"}
        assert len(catalogue.mandatory_requirements) >= 20

    def test_internal_audit_and_management_review_are_mandatory(self, catalogue: Catalogue) -> None:
        """The two clause 9 requirements a certification audit will not waive."""
        for requirement_id in ("C.9.2", "C.9.3"):
            assert catalogue.requirement(requirement_id).mandatory_for_certification

    def test_lookups_raise_on_unknown_identifiers(self, catalogue: Catalogue) -> None:
        with pytest.raises(CatalogueError):
            catalogue.control("A.5.99")
        with pytest.raises(CatalogueError):
            catalogue.requirement("C.99")
        assert not catalogue.has_control("A.5.99")

    def test_no_text_of_the_standard_is_shipped(self, catalogue: Catalogue) -> None:
        """The note that has to be on every export, and in the catalogue itself."""
        assert "No text from the standard is reproduced" in catalogue.framework_note


class TestFingerprint:
    def test_it_is_a_sha256(self, catalogue: Catalogue) -> None:
        assert len(catalogue.fingerprint) == 64
        assert all(character in "0123456789abcdef" for character in catalogue.fingerprint)

    def test_it_is_stable_across_loads(self, catalogue: Catalogue) -> None:
        again = load_catalogue(REPO_ROOT / "catalogue")
        assert again.fingerprint == catalogue.fingerprint

    def test_editing_a_control_changes_it(self, catalogue: Catalogue, tmp_path: Path) -> None:
        """The property the whole design rests on.

        Two catalogues can carry the same version number and different
        content, because somebody edited a file and did not bump it. The
        fingerprint is what an assessment records, so that the SoA from March
        can be matched to the catalogue that produced it.
        """
        copy = tmp_path / "catalogue"
        shutil.copytree(REPO_ROOT / "catalogue", copy)
        target = copy / "controls" / "06_people.yaml"
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "Screening before employment", "Pre-employment screening"
            ),
            encoding="utf-8",
        )
        edited = load_catalogue(copy)
        assert edited.version == catalogue.version  # nobody bumped it
        assert edited.fingerprint != catalogue.fingerprint  # and it did not help


class TestValidation:
    def _copy(self, tmp_path: Path) -> Path:
        copy = tmp_path / "catalogue"
        shutil.copytree(REPO_ROOT / "catalogue", copy)
        return copy

    def test_an_unknown_capability_is_refused(self, tmp_path: Path) -> None:
        copy = self._copy(tmp_path)
        target = copy / "controls" / "06_people.yaml"
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "capability: human_resources", "capability: vibes", 1
            ),
            encoding="utf-8",
        )
        with pytest.raises(CatalogueError, match="unknown capability"):
            load_catalogue(copy)

    def test_a_duplicate_identifier_is_refused(self, tmp_path: Path) -> None:
        copy = self._copy(tmp_path)
        target = copy / "controls" / "06_people.yaml"
        content = target.read_text(encoding="utf-8")
        block = content.split("  - id: A.6.2")[1].split("  - id: A.6.3")[0]
        target.write_text(content + "\n  - id: A.6.2" + block, encoding="utf-8")
        with pytest.raises(CatalogueError, match="defined twice"):
            load_catalogue(copy)

    def test_zero_day_evidence_is_refused(self, tmp_path: Path) -> None:
        """Evidence that is stale on the day it is collected supports nothing."""
        copy = self._copy(tmp_path)
        target = copy / "controls" / "06_people.yaml"
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "evidence_valid_days: 365", "evidence_valid_days: 0", 1
            ),
            encoding="utf-8",
        )
        with pytest.raises(CatalogueError, match="must be positive"):
            load_catalogue(copy)

    def test_a_missing_file_names_itself(self, tmp_path: Path) -> None:
        copy = self._copy(tmp_path)
        (copy / "controls" / "07_physical.yaml").unlink()
        with pytest.raises(CatalogueError, match=r"07_physical\.yaml"):
            load_catalogue(copy)
