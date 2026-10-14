let
  isDerivation = value: builtins.isAttrs value && (value.type or null) == "derivation";
  isDerivationSet = value:
    builtins.isAttrs value
    && builtins.attrNames value != [ ]
    && builtins.all (name: isDerivation value.${name}) (builtins.attrNames value);
  mkConsumer = args:
    if builtins.attrNames args != [ "fast" "publish" "verify" ] then
      throw "mkConsumer expects exactly fast, verify, and publish"
    else if !(isDerivationSet args.fast && isDerivationSet args.verify) then
      throw "fast and verify must be non-empty attrsets of Nix derivations"
    else if !(builtins.isAttrs args.publish && (args.publish.type or null) == "app") then
      throw "publish must be a Nix app"
    else args;
in
{
  apiVersion = 1;
  inherit mkConsumer;
  devTools = { pkgs, sdlcCli }: [ sdlcCli pkgs.skills ];
}
