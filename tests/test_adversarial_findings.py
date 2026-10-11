from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).parents[1]


class AdversarialFindingTests(unittest.TestCase):
    def _nix_eval(self, expression: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["nix", "eval", "--impure", "--expr", expression],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_contract_accepts_real_derivations_only(self):
        expression = """
          let
            flake = builtins.getFlake (toString ./.);
            pkgs = flake.inputs.nixpkgs.legacyPackages.x86_64-linux;
            real = pkgs.runCommand "contract-test" {} "touch $out";
            contract = {
              schemaVersion = 1;
              stages = { pr-fast = { real = real; }; candidate = { real = real; }; };
            };
          in
            assert pkgs.lib.isDerivation real;
            builtins.seq ((flake.lib.mkConsumer { inherit contract; }).hydraJobs.ci-pr-fast) true
        """
        result = self._nix_eval(expression)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_contract_rejects_fake_derivation_and_unknown_keys(self):
        fake = """
          let
            lib = (builtins.getFlake (toString ./. )).lib;
            fake = { drvPath = "/nix/store/fake"; };
            contract = { schemaVersion = 1; stages = { pr-fast = { fake = fake; }; candidate = { fake = fake; }; }; };
          in builtins.seq ((lib.mkConsumer { inherit contract; }).hydraJobs.ci-pr-fast) true
        """
        unknown = """
          let
            lib = (builtins.getFlake (toString ./. )).lib;
            contract = { schemaVersion = 1; stages = { pr-fast = { }; candidate = { }; }; extra = true; };
          in builtins.deepSeq (lib.mkConsumer { inherit contract; }) true
        """
        for expression in (fake, unknown):
            with self.subTest(expression=expression):
                self.assertNotEqual(self._nix_eval(expression).returncode, 0)

    def test_packaged_cli_declares_openssl_runtime(self):
        flake = (ROOT / "flake.nix").read_text()
        self.assertIn("pkgs.openssl", flake)

    def test_readme_has_one_consumer_packages_attrset(self):
        readme = (ROOT / "README.md").read_text()
        self.assertEqual(readme.count("packages = forAllSystems"), 1)


if __name__ == "__main__":
    unittest.main()
