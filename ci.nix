{ self, system }:
{
  fast = {
    syntax = self.checks.${system}.syntax;
    config = self.checks.${system}.config;
  };
  verify = {
    tests = self.checks.${system}.test;
  };
}
