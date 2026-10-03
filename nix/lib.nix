{ nixpkgs }:
let
  lib = nixpkgs.lib;
  stageNames = [ "pr-fast" "candidate" "release" ];
  stageAppName = stage: "ci-${stage}";

  validateContract = contract:
    let
      stages = contract.stages or { };
      known = builtins.all (name: builtins.elem name stageNames) (builtins.attrNames stages);
      hasStages = builtins.all (name: builtins.hasAttr name stages) stageNames;
      validStage = name:
        let stage = stages.${name};
        in builtins.isAttrs stage
          && builtins.isList (stage.targets or [ ])
          && builtins.all (target: builtins.isAttrs target && target ? drvPath) (stage.targets or [ ])
          && builtins.isList (stage.commands or [ ])
          && builtins.all (command: builtins.isList command && command != [ ] && builtins.all builtins.isString command) (stage.commands or [ ]);
      valid = (contract.schemaVersion or null) == 1
        && builtins.isAttrs stages
        && known && hasStages
        && builtins.all validStage stageNames
        && (stages.release.targets or [ ]) == [ ];
    in
    if valid then contract else throw ''Invalid SDLC contract v1: require stages pr-fast/candidate/release; targets must be derivations, commands argv arrays, and release targets must be empty.'';

  mkConsumer = { pkgs, contract }:
    let
      checked = validateContract contract;
      mkStage = stage:
        let
          spec = checked.stages.${stage};
          runCommands = lib.concatMapStringsSep "\n" (argv:
            ''${lib.escapeShellArgs argv}'') (spec.commands or [ ]);
        in
        pkgs.writeShellApplication {
          name = stageAppName stage;
          # Nix realizes declared targets before invoking the stage app. They
          # are runtime closure inputs so the app remains a stable local target.
          runtimeInputs = [ pkgs.nix ] ++ (spec.targets or [ ]);
          text = ''
            set -euo pipefail
            ${runCommands}
          '';
        };
      stagePackages = builtins.listToAttrs (map (stage: {
        name = stageAppName stage;
        value = mkStage stage;
      }) stageNames);
      stageApps = builtins.listToAttrs (map (stage: let name = stageAppName stage; in {
        inherit name;
        value = { type = "app"; program = "${stagePackages.${name}}/bin/${name}"; };
      }) stageNames);
    in
    {
      packages = stagePackages;
      apps = stageApps;
    };
in
{
  apiVersion = 1;
  inherit validateContract mkConsumer;

  devTools = { pkgs, sdlcCli }:
    [ sdlcCli pkgs.skills ];
}
