let
  flake = builtins.getFlake (toString ../.);
  lib = flake.lib;
  derivation = name: builtins.derivation {
    inherit name;
    system = "x86_64-linux";
    builder = "/bin/sh";
    args = [ "-c" "touch $out" ];
  };
  publisher = derivation "contract-publisher";
  app = { type = "app"; program = "${publisher}/bin/publish"; };
  result = lib.mkConsumer {
    check = {
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
assert builtins.attrNames result.check == [ "clippy" "fmt" ];
assert builtins.attrNames result.verify == [ "integration" "tests" ];
assert result.publish == app;
assert builtins.hasContext result.publish.program;
true
