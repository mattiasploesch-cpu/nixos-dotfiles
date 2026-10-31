# NixOS-Modul. Die Home-Manager-Variante ist modules/run_weston.nix.
#
# Die erzeugte Datei landet unter /etc/run_weston/config.toml. Der Manager
# verwendet sie, solange der Benutzer keine eigene
# ~/.config/run_weston/config.toml angelegt hat.
{ config, lib, pkgs, ... }:

let
  cfg = config.programs.runWeston;

  generatedConfig = (pkgs.formats.toml { }).generate "run_weston-config.toml" (
    import ./run_weston-config.nix { inherit lib; } cfg
  );
in
{
  imports = [ ./run_weston-options.nix ];

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ] ++ cfg.extraPackages;

    environment.etc."run_weston/config.toml" = lib.mkIf cfg.installConfig {
      source = if cfg.configFile != null then cfg.configFile else generatedConfig;
    };
  };
}
