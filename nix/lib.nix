let
  stageNames = [ "pr-fast" "candidate" ];
  validateContract = contract:
    let
      stages = contract.stages or { };
      validStage = stage:
        builtins.isAttrs stage
        && builtins.attrNames stage != [ ]
        && builtins.all (name: builtins.isAttrs stage.${name} && stage.${name} ? drvPath) (builtins.attrNames stage);
      valid = (contract.schemaVersion or null) == 1
        && builtins.isAttrs stages
        && builtins.attrNames stages == builtins.sort builtins.lessThan stageNames
        && builtins.all (name: validStage stages.${name}) stageNames;
    in
    if valid then contract else throw ''Invalid SDLC contract v1: stages must map pr-fast and candidate to non-empty attrsets of Nix derivations.'';
in
{
  apiVersion = 1;
  inherit validateContract;

  # Preserve the consumer's derivation attributes and Hestia metadata verbatim.
  # Hestia's matrix action owns evaluation, grouping, caching, and fan-out.
  mkConsumer = { contract }:
    let checked = validateContract contract;
    in {
      hydraJobs = {
        ci-pr-fast = checked.stages.pr-fast;
        ci-candidate = checked.stages.candidate;
      };
    };

  devTools = { pkgs, sdlcCli }:
    [ sdlcCli pkgs.skills ];
}
