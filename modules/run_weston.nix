# Home-Manager-Modul. Fuer configuration.nix gibt es
# modules/run_weston-nixos.nix mit denselben Optionen.
{ config, lib, pkgs, ... }:

let
  cfg = config.programs.runWeston;

  # Die config.toml wird aus den Optionen erzeugt; configFile ueberschreibt sie.
  generatedConfig = (pkgs.formats.toml { }).generate "run_weston-config.toml" (
    import ./run_weston-config.nix { inherit lib; } cfg
  );
in
{
  imports = [ ./run_weston-options.nix ];

  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ] ++ cfg.extraPackages;

    xdg.configFile."run_weston/config.toml" = lib.mkIf cfg.installConfig {
      source = if cfg.configFile != null then cfg.configFile else generatedConfig;
    };
  };
}
