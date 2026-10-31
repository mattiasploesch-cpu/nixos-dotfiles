# NixOS-Modul. Die Home-Manager-Variante ist modules/run_weston.nix.
{ config, lib, pkgs, ... }:

let
  cfg = config.programs.runWeston;

  defaultPackage = pkgs.callPackage ../run_weston/package.nix { };
in
{
  options.programs.runWeston = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Install run_weston, the Weston session manager for nested Weston in X11.
      '';
    };

    package = lib.mkOption {
      type = lib.types.package;
      default = defaultPackage;
      defaultText = lib.literalExpression "pkgs.callPackage ../run_weston/package.nix { }";
      description = "Package that provides the run_weston binary.";
    };

    extraPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [ ];
      example = lib.literalExpression "[ pkgs.weston pkgs.niri pkgs.foot ]";
      description = ''
        Programs that run_weston should start. Weston is required, everything
        else (niri, foot, dms, ...) is optional.
      '';
    };

    installConfig = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Install /etc/run_weston/config.toml.";
    };

    configFile = lib.mkOption {
      type = lib.types.path;
      default = "${cfg.package}/share/run_weston/config.toml";
      defaultText = lib.literalMD "the packaged config.toml";
      description = "Config file to install as /etc/run_weston/config.toml.";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ] ++ cfg.extraPackages;

    environment.etc."run_weston/config.toml" = lib.mkIf cfg.installConfig {
      source = cfg.configFile;
    };
  };
}
