{
  description = "Shared SDLC Nix library, runner, reusable workflows, and release tooling";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    mergify-nix.url = "github:blogle/mergify-nix";
  };
  outputs = { self, nixpkgs, mergify-nix }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
      sdlcLib = import ./nix/lib.nix { inherit nixpkgs; };
    in {
      lib = sdlcLib;
      packages = eachSystem (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          sdlcCli = pkgs.writeShellApplication {
            name = "sdlc";
            runtimeInputs = [ pkgs.python3 ];
            text = ''exec python3 ${./src/sdlc.py} "$@"'';
          };
        in {
          default = sdlcCli;
          sdlc = sdlcCli;
          skills = pkgs.skills;
          mergify-cli = mergify-nix.packages.${system}.mergify-cli;
        });
      apps = eachSystem (system: {
          default = { type = "app"; program = "${self.packages.${system}.sdlc}/bin/sdlc"; };
        });
      devShells = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.mkShell {
            packages = sdlcLib.devTools {
              inherit pkgs;
              sdlcCli = self.packages.${system}.sdlc;
            } ++ [ pkgs.just pkgs.python3 pkgs.actionlint ];
          };
        });
      checks = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
          ci = sdlcLib.mkConsumer {
            contract = import ./ci.nix { inherit self system; };
          };
        in ci.checks // {
          test = pkgs.runCommand "sdlc-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            cd ${./.}
            python3 -m unittest discover -s tests -v
            touch $out
          '';
        });
    };
}
