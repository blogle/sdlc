let
  isDerivation = value: builtins.isAttrs value && (value.type or null) == "derivation";
  mkConsumer = args:
    if builtins.attrNames args != [ "fast" "publish" "verify" ] then
      throw "mkConsumer expects exactly fast, verify, and publish"
    else if !(isDerivation args.fast && isDerivation args.verify) then
      throw "fast and verify must be Nix derivations"
    else if !(builtins.isAttrs args.publish && (args.publish.type or null) == "app") then
      throw "publish must be a Nix app"
    else {
      checks.sdlc-fast = args.fast;
      checks.sdlc-verify = args.verify;
      apps.sdlc-publish = args.publish;
    };
in
{
  apiVersion = 1;
  inherit mkConsumer;
  devTools = { pkgs, sdlcCli }: [ sdlcCli pkgs.skills ];
}
