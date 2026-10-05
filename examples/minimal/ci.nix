{ self, system }:
{
  schemaVersion = 1;
  stages = {
    pr-fast = {
      format = self.checks.${system}.fast;
      contract = self.checks.${system}.fast-contract;
    };
    candidate = {
      integration = self.checks.${system}.candidate;
      artifact = self.checks.${system}.candidate-artifact;
    };
  };
}
