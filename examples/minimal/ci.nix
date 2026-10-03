{ self, system, pkgs }:
{
  schemaVersion = 1;
  stages = {
    pr-fast.targets = [ self.checks.${system}.fast ];
    candidate.targets = [ self.checks.${system}.candidate ];
    # Production repos put a publication-only argv hook here, with no targets.
    release = { targets = [ ]; commands = [ ]; };
  };
}
