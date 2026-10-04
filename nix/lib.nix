{ nixpkgs }:
let
  stageNames = [ "pr-fast" "candidate" ];
  validateContract = contract:
    let
      stages = contract.stages or { };
      valid = (contract.schemaVersion or null) == 1
        && builtins.isAttrs stages
        && builtins.attrNames stages == builtins.sort builtins.lessThan stageNames
        && builtins.all (name:
          builtins.isAttrs stages.${name}
          && stages.${name} ? drvPath
        ) stageNames;
    in
    if valid then contract else throw ''Invalid SDLC contract v1: stages must map pr-fast and candidate to one Nix derivation each.'';
in
{
  apiVersion = 1;
  inherit validateContract;

  mkConsumer = { contract }:
    let
      checked = validateContract contract;
    in {
      checks = {
        ci-pr-fast = checked.stages.pr-fast;
        ci-candidate = checked.stages.candidate;
      };
    };

  devTools = { pkgs, sdlcCli }:
    [ sdlcCli pkgs.skills ];
}
