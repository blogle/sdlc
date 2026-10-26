{ self, pkgs, system }:
let
  publishHook = pkgs.writeShellApplication {
    name = "sdlc-publish";
    runtimeInputs = [ pkgs.coreutils pkgs.git pkgs.gh pkgs.jq pkgs.gzip ];
    text = builtins.readFile ./scripts/sdlc-release-publish.sh;
  };
  publish = {
    type = "app";
    program = "${publishHook}/bin/sdlc-publish";
  };
in {
  check = {
    syntax = self.checks.${system}.syntax;
    config = self.checks.${system}.config;
  };
  verify = {
    tests = self.checks.${system}.test;
    skills-install = self.checks.${system}.skills-install;
  };
  inherit publish;
}
