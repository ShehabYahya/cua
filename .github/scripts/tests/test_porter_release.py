"""Tests for Porter's automatic release version handling."""

from pathlib import Path
import tempfile
import unittest

from porter_release import bump_version, update_version_file


class TestPorterReleaseVersion(unittest.TestCase):
    def test_semantic_bumps(self) -> None:
        self.assertEqual(bump_version("0.1.0", "patch"), "0.1.1")
        self.assertEqual(bump_version("0.1.9", "minor"), "0.2.0")
        self.assertEqual(bump_version("0.9.9", "major"), "1.0.0")

    def test_prerelease_input_produces_normal_release(self) -> None:
        self.assertEqual(bump_version("2.4.0-rc.1+build.7", "patch"), "2.4.1")

    def test_rejects_invalid_semver(self) -> None:
        for version in ("01.2.3", "1.2", "1.2.3-01"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                bump_version(version, "patch")

    def test_updates_only_canonical_version_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            version_file = Path(directory) / "version.py"
            version_file.write_text('APP_NAME = "Porter"\n__version__ = "0.1.0"\n')
            self.assertEqual(update_version_file(version_file, "patch"), "0.1.1")
            self.assertEqual(
                version_file.read_text(),
                'APP_NAME = "Porter"\n__version__ = "0.1.1"\n',
            )


class TestPorterReleaseWorkflowWiring(unittest.TestCase):
    def test_merge_workflow_has_independent_token_release_job(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[2]
            / "workflows/release-on-merge.yml"
        ).read_text()
        self.assertIn("  porter-release:\n", workflow)
        self.assertIn("group: porter-auto-release", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("gh workflow run release-porter.yml", workflow)
        self.assertIn('"refs/tags/$tag"', workflow)
        self.assertIn('"Porter-PR: #$PR_NUMBER"', workflow)
        self.assertIn("--branch \"$tag\"", workflow)
        self.assertNotIn("f-trycua", workflow.split("  porter-release:", 1)[1].split("  upstream-auto-release:", 1)[0])

    def test_dedicated_release_supports_generated_notes(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[2]
            / "workflows/release-porter.yml"
        ).read_text()
        self.assertIn("--notes-file", workflow)
        self.assertIn("--generate-notes", workflow)
        self.assertIn('gh release view "$TAG"', workflow)

    def test_porter_ci_reads_the_canonical_version(self) -> None:
        workflows = Path(__file__).resolve().parents[2] / "workflows"
        for filename in ("build-porter-deb.yml", "ci-porter-gui.yml"):
            with self.subTest(filename=filename):
                workflow = (workflows / filename).read_text()
                self.assertIn("from version import __version__", workflow)
                self.assertNotIn("0.1.1", workflow)


if __name__ == "__main__":
    unittest.main()
