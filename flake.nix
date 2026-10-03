{
  description = "Shared SDLC protocol, runner, workflows, and release tooling";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    mergify-nix.url = "github:blogle/mergify-nix";
  };
  outputs = { self, nixpkgs, mergify-nix }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
    in {
      packages = eachSystem (system: {
        default = self.packages.${system}.sdlc;
        sdlc = nixpkgs.legacyPackages.${system}.writeShellApplication {
          name = "sdlc";
          runtimeInputs = [ nixpkgs.legacyPackages.${system}.python3 ];
          text = ''exec python3 ${./src/sdlc.py} "$@"'';
        };
        mergify-cli = mergify-nix.packages.${system}.mergify-cli;
      });
      apps = eachSystem (system: {
        default = { type = "app"; program = "${self.packages.${system}.sdlc}/bin/sdlc"; };
      });
      devShells = eachSystem (system: {
        default = nixpkgs.legacyPackages.${system}.mkShell {
          packages = with nixpkgs.legacyPackages.${system}; [ self.packages.${system}.sdlc just python3 actionlint skills ];
        };
      });
      checks = eachSystem (system: {
        test = nixpkgs.legacyPackages.${system}.runCommand "sdlc-tests" { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
          cd ${./.}
          python3 -m unittest discover -s tests -v
          touch $out
        '';
      });
    };
}
