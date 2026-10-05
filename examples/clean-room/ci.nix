{ self, system }:
{
  schemaVersion = 1;
  stages = {
    pr-fast = {
      fast = self.checks.${system}.fast;
      contract = self.checks.${system}.contract;
    };
    candidate = {
      validation = self.checks.${system}.candidate;
      artifact = self.checks.${system}.artifact;
    };
  };
}
