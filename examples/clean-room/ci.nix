{ self, system }:
{
  check = {
    format = self.checks.${system}.check;
    contract = self.checks.${system}.contract;
  };
  verify = {
    validation = self.checks.${system}.candidate;
    artifact = self.checks.${system}.artifact;
  };
}
