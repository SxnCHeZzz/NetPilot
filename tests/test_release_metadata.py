"""Keep every public release surface on the same version and artifact names."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.5.4-rc11"


class ReleaseMetadataTests(unittest.TestCase):
    def test_application_version(self):
        source = (ROOT / "src" / "app.py").read_text(encoding="utf-8")
        match = re.search(r"^VERSION\s*=\s*['\"]([^'\"]+)", source, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), VERSION)

    def test_installer_version_and_name(self):
        installer = (ROOT / "installer" / "NetPilot.iss").read_text(encoding="utf-8")
        self.assertIn(f'#define MyAppVersion "{VERSION}"', installer)
        self.assertIn(f"OutputBaseFilename=NetPilot-{VERSION}-Setup", installer)

    def test_workflow_artifacts_use_release_version(self):
        workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
        self.assertIn(f"NetPilot-{VERSION}-Setup.exe", workflow)
        self.assertIn(f"NetPilot-{VERSION}-Portable.zip", workflow)
        self.assertNotRegex(workflow, r"0\.5\.4-(?:rc|RC)(?:6|10)\b")

    def test_readme_documents_current_release(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(VERSION, readme)
        self.assertIn(f"NetPilot-{VERSION}-Setup.exe", readme)
        self.assertIn(f"NetPilot-{VERSION}-Portable.zip", readme)
        self.assertNotRegex(readme, r"0\.5\.4-(?:rc|RC)(?:6|10)\b")

    def test_windows_ui_toolkit_is_reproducibly_pinned(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn("PySide6==6.10.2", requirements)


if __name__ == "__main__":
    unittest.main()
