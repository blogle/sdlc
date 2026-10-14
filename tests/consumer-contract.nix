let
  lib = import ../nix/lib.nix;
  derivation = name: { type = "derivation"; inherit name; };
  app = { type = "app"; program = "/bin/true"; };
  result = lib.mkConsumer {
    fast = {
      fmt = derivation "fmt";
      clippy = derivation "clippy";
    };
    verify = {
      tests = derivation "tests";
      integration = derivation "integration";
    };
    publish = app;
  };
in
assert builtins.attrNames result.fast == [ "clippy" "fmt" ];
assert builtins.attrNames result.verify == [ "integration" "tests" ];
assert result.publish == app;
true
