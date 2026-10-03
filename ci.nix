{ self, system }:
{
  schemaVersion = 1;
  stages = {
    pr-fast.targets = [ self.checks.${system}.test ];
    candidate.targets = [ self.checks.${system}.test ];
    release = { targets = [ ]; commands = [ ]; };
  };
}
