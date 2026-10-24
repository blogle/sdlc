{ nixpkgs-latest, nixpkgs-stable }:
let
  isDerivation = value: builtins.isAttrs value && (value.type or null) == "derivation";
  isDerivationSet = value:
    builtins.isAttrs value
    && builtins.attrNames value != [ ]
    && builtins.all (name: isDerivation value.${name}) (builtins.attrNames value);
  isPublishApp = value:
    builtins.isAttrs value
    && builtins.all (name: builtins.elem name [ "meta" "program" "type" ]) (builtins.attrNames value)
    && (value.type or null) == "app"
    && (!(value ? meta) || builtins.isAttrs value.meta)
    && builtins.isString (value.program or null)
    && builtins.hasContext value.program
    && builtins.substring 0 (builtins.stringLength builtins.storeDir + 1) value.program == "${builtins.storeDir}/";
  mkConsumer = args:
    if builtins.attrNames args != [ "check" "publish" "verify" ] then
      throw "mkConsumer expects exactly check, publish, and verify"
    else if !(isDerivationSet args.check && isDerivationSet args.verify) then
      throw "check and verify must be non-empty attrsets of Nix derivations"
    else if !isPublishApp args.publish then
      throw "publish must be a Nix app with a store-backed executable program"
    else args;
in
{
  apiVersion = 2;
  inherit mkConsumer;
  importPackages = { system, latest ? { }, stable ? { } }:
    {
      latest = import nixpkgs-latest ({ inherit system; } // latest);
      stable = import nixpkgs-stable ({ inherit system; } // stable);
    };
  devTools = { pkgs, sdlcCli }: [ sdlcCli pkgs.skills ];
}
