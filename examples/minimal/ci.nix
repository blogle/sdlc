{ self, system }:
{
  schemaVersion = 1;
  stages = {
    pr-fast = self.checks.${system}.fast;
    candidate = self.checks.${system}.candidate;
  };
}
