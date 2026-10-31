{
  description = "Selected Home Manager dotfiles";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }: let
    systems = [ "x86_64-linux" "aarch64-linux" ];
    forAllSystems = nixpkgs.lib.genAttrs systems;
  in {
    packages = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      run-weston = pkgs.callPackage ./run_weston/package.nix { };
      default = self.packages.${system}.run-weston;
    });

    homeManagerModules = {
      qtile = { ... }: {
        xdg.configFile."qtile".source = self + "/qtile";
      };
      change_bg = import ./modules/change_bg.nix;
      run_weston = import ./modules/run_weston.nix;
      sunshine_audio = import ./modules/sunshine_audio.nix;
    };

    nixosModules = {
      run_weston = import ./modules/run_weston-nixos.nix;
    };
  };
}
