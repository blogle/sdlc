{ self, system }:
{
  fast = {
    format = self.checks.${system}.fast;
    contract = self.checks.${system}.contract;
  };
  verify = {
    validation = self.checks.${system}.candidate;
    artifact = self.checks.${system}.artifact;
  };
}
