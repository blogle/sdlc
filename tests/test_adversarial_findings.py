from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).parents[1]


class ConsumerLibraryTests(unittest.TestCase):
    def _nix_eval(self, expression: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["nix", "eval", "--impure", "--expr", expression],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_consumer_maps_hooks_to_stable_flake_outputs(self):
        if shutil.which("nix") is None:
            self.skipTest("nix is unavailable")
        expression = '''
          let
            flake = builtins.getFlake (toString ./.);
            pkgs = flake.inputs.nixpkgs.legacyPackages.x86_64-linux;
            check = pkgs.runCommand "contract-test" {} "touch $out";
            app = { type = "app"; program = "${check}/bin/publish"; };
            result = flake.lib.mkConsumer {
              fast = { fmt = check; clippy = check; };
              verify = { tests = check; integration = check; };
              publish = app;
            };
          in
            assert result.fast.fmt == check;
            assert result.fast.clippy == check;
            assert result.verify.tests == check;
            assert result.verify.integration == check;
            assert result.publish == app;
            true
        '''
        result = self._nix_eval(expression)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_consumer_library_rejects_invalid_hooks_and_extra_configuration(self):
        if shutil.which("nix") is None:
            self.skipTest("nix is unavailable")
        base = '''
          let
            lib = (builtins.getFlake (toString ./.)).lib;
            check = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.x86_64-linux.runCommand "contract-test" {} "touch $out";
            app = { type = "app"; program = "publish"; };
          in %s
        '''
        expressions = (
            base % 'builtins.deepSeq (lib.mkConsumer { fast = {}; verify = { test = check; }; publish = app; }) true',
            base % 'builtins.deepSeq (lib.mkConsumer { fast = { fmt = check; }; verify = { test = check; }; publish = app; extra = true; }) true',
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertNotEqual(self._nix_eval(expression).returncode, 0)


if __name__ == "__main__":
    unittest.main()
