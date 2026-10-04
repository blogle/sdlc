{ self, system }:
{
  schemaVersion = 1;
  stages = {
    pr-fast = self.checks.${system}.test;
    candidate = self.checks.${system}.test;
  };
}
