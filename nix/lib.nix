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
    if builtins.attrNames args != [ "fast" "publish" "verify" ] then
      throw "mkConsumer expects exactly fast, verify, and publish"
    else if !(isDerivationSet args.fast && isDerivationSet args.verify) then
      throw "fast and verify must be non-empty attrsets of Nix derivations"
    else if !isPublishApp args.publish then
      throw "publish must be a Nix app with a store-backed executable program"
    else args;
in
{
  apiVersion = 1;
  inherit mkConsumer;
  devTools = { pkgs, sdlcCli }: [ sdlcCli pkgs.skills ];
}
