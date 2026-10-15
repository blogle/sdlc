{ self, pkgs, system }:
let
  publishHook = pkgs.writeShellApplication {
    name = "sdlc-publish";
    runtimeInputs = [ pkgs.coreutils pkgs.git pkgs.gh pkgs.jq ];
    text = builtins.readFile ./scripts/sdlc-release-publish.sh;
  };
  publish = {
    type = "app";
    program = "${publishHook}/bin/sdlc-publish";
  };
in {
  fast = {
    syntax = self.checks.${system}.syntax;
    config = self.checks.${system}.config;
  };
  verify = {
    tests = self.checks.${system}.test;
  };
  inherit publish;
}
