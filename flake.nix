{
  description = "Pinned fleet CI, policy, and rolling-release contract";
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
            runtimeInputs = [ pkgs.coreutils pkgs.git pkgs.gh pkgs.jq pkgs.nix pkgs.python3 ];
            text = ''
              export SDLC_SOURCE_PATH=${./.}
              export PYTHONPATH=${./src}:''${PYTHONPATH:-}
              exec python3 ${./src/sdlc.py} "$@"
            '';
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
            } ++ [ pkgs.just pkgs.python3 pkgs.gh pkgs.actionlint pkgs.renovate ];
          };
        });
      checks = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
            test = pkgs.runCommand "sdlc-tests" { nativeBuildInputs = [ pkgs.git pkgs.python3 ]; } ''
              cd ${./.}
              python3 -m unittest discover -s tests -v
              touch $out
            '';
        in {
          inherit test;
          sdlc-fast = pkgs.runCommand "sdlc-fast" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            python3 - <<'PY'
            from pathlib import Path
            for name in ("src/sdlc.py", "src/release_gate.py", "src/release_publisher.py", "src/release_reconcile.py"):
                path = Path("${./.}") / name
                compile(path.read_text(), str(path), "exec")
            PY
            ${pkgs.jq}/bin/jq empty ${./rulesets/default-branch.json}
            ${pkgs.jq}/bin/jq empty ${./default.json}
            touch $out
          '';
          sdlc-verify = pkgs.runCommand "sdlc-verify" { buildInputs = [ test ]; } ''
            touch $out
          '';
        });
    };
}
