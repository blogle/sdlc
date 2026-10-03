import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("install_skills", ROOT / "src/install_skills.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class SkillInstallationTests(unittest.TestCase):
    def test_bundled_skill_is_installed_in_downstream_agent_location(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / ".agents/skills"
            installer.install(ROOT / "skills", destination)
            installed = destination / "sdlc/SKILL.md"
            self.assertEqual(installed.read_text(), (ROOT / "skills/sdlc/SKILL.md").read_text())
            for instruction in ("integration:auto", "integration:review", "Hestia", "CHANGELOG.md", "zero-commits-behind-main"):
                self.assertIn(instruction, installed.read_text())


if __name__ == "__main__":
    unittest.main()
