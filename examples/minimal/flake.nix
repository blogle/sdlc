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
          format = pkgs.runCommand "consumer-format-check" { nativeBuildInputs = [ pkgs.coreutils ]; } ''
            test -f ${./flake.nix}
            touch $out
          '';
          tests = pkgs.runCommand "consumer-unit-tests" { nativeBuildInputs = [ pkgs.coreutils ]; } ''
            test -f ${./ci.nix}
            touch $out
          '';
        });
      ci = nixpkgs.lib.genAttrs systems (system:
        let
          ci = import ./ci.nix { inherit self system; };
          pkgs = nixpkgs.legacyPackages.${system};
          publish = { type = "app"; program = "${pkgs.writeShellScriptBin "publish" "echo publication hook"}/bin/publish"; };
        in sdlc.lib.mkConsumer (ci // { inherit publish; }));
      packages = nixpkgs.lib.genAttrs systems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
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
