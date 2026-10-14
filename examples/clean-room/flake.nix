{
  description = "Clean-room consumer using the released SDLC flake API";
  inputs = {
    sdlc.url = "github:blogle/sdlc/v1.2.3";
    nixpkgs.follows = "sdlc/nixpkgs";
  };
  outputs = { self, nixpkgs, sdlc }:
    let
      systems = [ "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      consumer = system:
        let
          ci = import ./ci.nix { inherit self system; };
          pkgs = nixpkgs.legacyPackages.${system};
          publish = { type = "app"; program = "${pkgs.writeShellScriptBin "publish" "echo publication hook"}/bin/publish"; };
        in sdlc.lib.mkConsumer { inherit (ci) fast verify; inherit publish; };
    in {
      checks = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          ci = consumer system;
        in {
          fast = pkgs.runCommand "clean-room-fast" { } ''touch $out'';
          contract = pkgs.runCommand "clean-room-contract" { } ''touch $out'';
          candidate = pkgs.runCommand "clean-room-candidate" { } ''touch $out'';
          artifact = pkgs.runCommand "clean-room-artifact" { } ''touch $out'';
        });
      ci = forAllSystems consumer;
      packages = forAllSystems (system:
        { nix-eval-jobs = nixpkgs.legacyPackages.${system}.nix-eval-jobs; });
    };
}
