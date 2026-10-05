{ self, system }:
{
  schemaVersion = 1;
  stages = {
    pr-fast = { tests = self.checks.${system}.test; };
    candidate = { tests = self.checks.${system}.test; };
  };
}
