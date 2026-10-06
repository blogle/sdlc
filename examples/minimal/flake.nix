{
  description = "Minimal consumer of the shared SDLC flake library";
  inputs = {
    # This path input makes this checked-in fixture testable in the SDLC repo.
    # Consumers use github:blogle/sdlc/v1.0.0 and keep the exact revision in flake.lock.
    sdlc.url = "path:../..";
    nixpkgs.follows = "sdlc/nixpkgs";
  };
  outputs = { self, nixpkgs, sdlc }:
    let
      systems = [ "x86_64-linux" ];
    in {
      checks = nixpkgs.lib.genAttrs systems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in {
          fast = pkgs.runCommand "consumer-fast" { } ''echo fast admission; touch $out'';
          fast-contract = pkgs.runCommand "consumer-contract" { } ''echo validate declared contract; touch $out'';
          candidate = pkgs.runCommand "consumer-candidate" { } ''echo synthetic candidate validation; touch $out'';
          candidate-artifact = pkgs.runCommand "consumer-candidate-artifact" { } ''echo candidate artifact derivation; touch $out'';
        });
      hydraJobs = nixpkgs.lib.genAttrs systems (system:
        let ci = sdlc.lib.mkConsumer {
          contract = import ./ci.nix { inherit self system; };
        };
        in ci.hydraJobs);
      apps = nixpkgs.lib.genAttrs systems (system:
        {
          sdlc = { type = "app"; program = "${sdlc.packages.${system}.sdlc}/bin/sdlc"; };
        });
      packages = nixpkgs.lib.genAttrs systems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          sdlc = sdlc.packages.${system}.sdlc;
          nix-eval-jobs = pkgs.nix-eval-jobs;
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
