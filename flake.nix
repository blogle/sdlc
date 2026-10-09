{
  description = "Shared SDLC Nix library, runner, reusable workflows, and release tooling";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    mergify-nix = {
      url = "github:blogle/mergify-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };
  outputs = { self, nixpkgs, mergify-nix }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
      sdlcLib = import ./nix/lib.nix;
    in {
      lib = sdlcLib;
      packages = eachSystem (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          sdlcCli = pkgs.writeShellApplication {
            name = "sdlc";
            runtimeInputs = [ pkgs.python3 pkgs.gh ];
            text = ''export SDLC_POLICY_MODULE_PATH=${./actions/repository-policy}; exec python3 ${./src/sdlc.py} "$@"'';
          };
        in {
          default = sdlcCli;
          sdlc = sdlcCli;
          skills = pkgs.skills;
          nix-eval-jobs = pkgs.nix-eval-jobs;
          mergify-cli = mergify-nix.packages.${system}.mergify-cli;
        });
      apps = eachSystem (system: {
          default = { type = "app"; program = "${self.packages.${system}.sdlc}/bin/sdlc"; };
        });
      hydraJobs = nixpkgs.lib.genAttrs [ "x86_64-linux" ] (system:
        let ci = sdlcLib.mkConsumer { contract = import ./ci.nix { inherit self system; }; };
        in ci.hydraJobs);
      devShells = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.mkShell {
            packages = sdlcLib.devTools {
              inherit pkgs;
              sdlcCli = self.packages.${system}.sdlc;
            } ++ [ pkgs.just pkgs.python3 pkgs.gh pkgs.actionlint pkgs.renovate ];
          };
        });
      checks = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          test = pkgs.runCommand "sdlc-tests" { nativeBuildInputs = [ pkgs.git pkgs.python3 ]; } ''
            cd ${./.}
            python3 -m unittest discover -s tests -v
            touch $out
          '';
        });
    };
}
