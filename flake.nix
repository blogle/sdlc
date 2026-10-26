{
  description = "Pinned fleet CI, policy, and rolling-release contract";
  inputs = {
    nixpkgs-latest.url = "github:NixOS/nixpkgs/nixos-unstable";
    nixpkgs-stable.url = "github:NixOS/nixpkgs/nixos-26.05";
    agent-skills-nix = {
      url = "github:Kyure-A/agent-skills-nix";
      inputs.nixpkgs.follows = "nixpkgs-latest";
    };
    mergify-nix = {
      url = "github:blogle/mergify-nix";
      inputs.nixpkgs.follows = "nixpkgs-latest";
    };
  };
  outputs = { self, nixpkgs-latest, nixpkgs-stable, mergify-nix, agent-skills-nix }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" ];
      eachSystem = nixpkgs-latest.lib.genAttrs systems;
      sdlcLib = import ./nix/lib.nix { inherit nixpkgs-latest nixpkgs-stable; };
      agentSkills = agent-skills-nix.lib.agent-skills;
      skillsInstallCheck = system:
        let
          pkgs = nixpkgs-latest.legacyPackages.${system};
          sources = { sdlc = { path = self; subdir = "skills"; }; };
          catalog = agentSkills.discoverCatalog sources;
          allowlist = agentSkills.allowlistFor {
            inherit catalog sources;
            enable = [ "sdlc" ];
          };
          selection = agentSkills.selectSkills {
            inherit catalog sources allowlist;
            skills = { };
          };
          bundle = agentSkills.mkBundle { inherit pkgs selection; };
          targets = {
            agents = agentSkills.defaultLocalTargets.agents // { enable = true; };
          };
          install = agentSkills.mkLocalInstallProgram { inherit pkgs bundle targets; };
        in pkgs.runCommand "sdlc-skills-install" { nativeBuildInputs = [ install ]; } ''
          mkdir -p work
          cd work
          ${install}/bin/skills-install-local
          test -f .agents/skills/sdlc/SKILL.md
          ${install}/bin/skills-install-local
          test -f .agents/skills/.agent-skills-managed.json
          touch $out
        '';
    in {
      # Preserve the direct upstream API; any future stable SDLC convenience
      # function must be additive so existing sdlc.lib.skills users keep working.
      lib = sdlcLib // { skills = agentSkills; };
      inherit nixpkgs-latest nixpkgs-stable;
      packages = eachSystem (system:
        let
          pkgs = nixpkgs-latest.legacyPackages.${system};
          sdlcCli = pkgs.writeShellApplication {
            name = "sdlc";
            runtimeInputs = [ pkgs.coreutils pkgs.git pkgs.gh pkgs.jq pkgs.nix (pkgs.python3.withPackages (ps: [ ps.json5 ])) ];
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
          nix-eval-jobs = pkgs.nix-eval-jobs;
        });
      apps = eachSystem (system: {
          default = { type = "app"; program = "${self.packages.${system}.sdlc}/bin/sdlc"; };
        });
      ci = nixpkgs-latest.lib.genAttrs [ "x86_64-linux" ] (system:
        sdlcLib.mkConsumer (import ./ci.nix {
          inherit self system;
          pkgs = nixpkgs-latest.legacyPackages.${system};
        }));
      devShells = eachSystem (system:
        let pkgs = nixpkgs-latest.legacyPackages.${system};
        in {
          default = pkgs.mkShell {
            packages = sdlcLib.devTools {
              inherit pkgs;
              sdlcCli = self.packages.${system}.sdlc;
            } ++ [ pkgs.just (pkgs.python3.withPackages (ps: [ ps.json5 ])) pkgs.gh pkgs.actionlint pkgs.renovate ];
          };
        });
      checks = eachSystem (system:
        let pkgs = nixpkgs-latest.legacyPackages.${system};
            test = pkgs.runCommand "sdlc-tests" { nativeBuildInputs = [ pkgs.git (pkgs.python3.withPackages (ps: [ ps.json5 ])) ]; } ''
              cd ${./.}
              python3 -m unittest discover -s tests -v
              touch $out
            '';
            syntax = pkgs.runCommand "sdlc-syntax" { nativeBuildInputs = [ pkgs.python3 ]; } ''
              python3 - <<'PY'
              from pathlib import Path
              for path in Path("${./src}").glob("*.py"):
                  compile(path.read_text(), str(path), "exec")
              PY
              touch $out
            '';
            config = pkgs.runCommand "sdlc-config" { nativeBuildInputs = [ pkgs.bash pkgs.jq ]; } ''
              jq empty ${./rulesets/default-branch.json}
              jq empty ${./default.json}
              bash -n ${./scripts/sdlc-release-publish.sh}
              touch $out
            '';
        in {
          inherit test;
          inherit syntax config;
          skills-install = skillsInstallCheck system;
        });
    };
}
