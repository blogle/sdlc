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
      consumer = system: sdlc.lib.mkConsumer {
        contract = import ./ci.nix { inherit self system; };
      };
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
          generated-stage-contract = pkgs.runCommand "clean-room-stage-contract" {
            buildInputs = builtins.attrValues ci.hydraJobs.ci-pr-fast ++ builtins.attrValues ci.hydraJobs.ci-candidate;
          } ''touch $out'';
        });
      hydraJobs = forAllSystems (system: (consumer system).hydraJobs);
      packages = forAllSystems (system:
        { nix-eval-jobs = nixpkgs.legacyPackages.${system}.nix-eval-jobs; });
    };
}
