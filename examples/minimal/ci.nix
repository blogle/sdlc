{ self, system }:
{
  check = {
    format = self.checks.${system}.format;
  };
  verify = {
    tests = self.checks.${system}.tests;
  };
}
