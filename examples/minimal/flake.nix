{
  description = "Minimal consumer of the shared SDLC flake library";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # This path input makes this checked-in fixture testable in the SDLC repo.
    # Consumers use github:blogle/sdlc/v1 and keep the exact revision in flake.lock.
    sdlc.url = "path:../..";
    sdlc.inputs.nixpkgs.follows = "nixpkgs";
  };
  outputs = { self, nixpkgs, sdlc }:
    let
      systems = [ "x86_64-linux" ];
    in {
      checks = nixpkgs.lib.genAttrs systems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          ci = sdlc.lib.mkConsumer {
            contract = import ./ci.nix { inherit self system; };
          };
        in ci.checks // {
          fast = pkgs.runCommand "consumer-fast" { } ''echo fast admission; touch $out'';
          candidate = pkgs.runCommand "consumer-candidate" { } ''echo synthetic candidate validation; touch $out'';
        });
      apps = nixpkgs.lib.genAttrs systems (system:
        {
          sdlc = { type = "app"; program = "${sdlc.packages.${system}.sdlc}/bin/sdlc"; };
        });
      packages = nixpkgs.lib.genAttrs systems (system:
        {
          sdlc = sdlc.packages.${system}.sdlc;
        });
      devShells = nixpkgs.lib.genAttrs systems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.mkShell {
            packages = sdlc.lib.devTools {
              inherit pkgs;
              sdlcCli = sdlc.packages.${system}.sdlc;
          } ++ [ pkgs.just pkgs.gh ];
          };
        });
    };
}
