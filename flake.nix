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
        installSkills = nixpkgs.legacyPackages.${system}.writeShellApplication {
          name = "install-sdlc-skills";
          runtimeInputs = [ nixpkgs.legacyPackages.${system}.git nixpkgs.legacyPackages.${system}.nodejs nixpkgs.legacyPackages.${system}.nix nixpkgs.legacyPackages.${system}.python3 ];
          text = ''
            root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
            nix run github:blogle/mergify-nix#installSkills --
            python3 ${./src/install_skills.py} ${./skills} "$root/.agents/skills"
            echo "Installed shared SDLC skills in $root/.agents/skills"
          '';
        };
        mergify-cli = mergify-nix.packages.${system}.mergify-cli;
      });
      apps = eachSystem (system: {
        default = { type = "app"; program = "${self.packages.${system}.sdlc}/bin/sdlc"; };
        installSkills = { type = "app"; program = "${self.packages.${system}.installSkills}/bin/install-sdlc-skills"; };
      });
      devShells = eachSystem (system: {
        default = nixpkgs.legacyPackages.${system}.mkShell {
          packages = with nixpkgs.legacyPackages.${system}; [ self.packages.${system}.sdlc just python3 actionlint ];
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
