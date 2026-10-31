{ config, lib, pkgs, inputs, ... }:

let
  cfg = config.programs.changeBg;
  system = pkgs.stdenv.hostPlatform.system;

  defaultWallpaperInputs = [
    "gruvbox-wallpapers"
    "ahwxorg-walls"
    "gtheme-walls"
    "rosepine-walls"
    "dracula-walls"
    "alperhan-walls"
    "xnotkyo-walls"
    "twc-walls"
    "buckmanc-walls"
    "makccr-walls"
  ];

  # gruvbox-wallpapers is a flake; the others are plain source trees.
  sourceFor = name:
    if name == "gruvbox-wallpapers"
    then inputs.gruvbox-wallpapers.packages.${system}.mix
    else inputs.${name};

  # This is what gets written into configuration.nix. It deliberately uses
  # inputs.<name> + "/relative/path" instead of an absolute /nix/store path.
  exprFor = name:
    if name == "gruvbox-wallpapers"
    then "inputs.gruvbox-wallpapers.packages.${system}.mix"
    else "inputs.${name}";

  sources = map
    (name: {
      inherit name;
      path = sourceFor name;
      expr = exprFor name;
    })
    (lib.filter (name: builtins.hasAttr name inputs) cfg.wallpaperInputs);

  manifest = pkgs.writeText "change-bg-sources.tsv"
    (lib.concatStringsSep "\n"
      (map (s: "${s.name}\t${toString s.path}\t${s.expr}") sources)
    + "\n");

  changeBgPreview = pkgs.writeShellApplication {
    name = "change_bg_preview";
    runtimeInputs = with pkgs; [
      chafa
      coreutils
      ueberzugpp
    ];
    text = builtins.readFile ../scripts/change_bg_preview.sh;
  };

  changeBg = pkgs.writeShellApplication {
    name = "change_bg";
    runtimeInputs = with pkgs; [
      coreutils
      curl
      findutils
      fzf
      gawk
      gnugrep
      gnused
      imagemagick
      perl
      ueberzugpp
      xwallpaper
    ];
    text = ''
      export CHANGE_BG_MANIFEST="${manifest}"
      export CHANGE_BG_DEFAULT_CONFIG="${cfg.configFile}"
      export CHANGE_BG_PREVIEW="${changeBgPreview}/bin/change_bg_preview"
    '' + builtins.readFile ../scripts/change_bg.sh;
  };
in
{
  options.programs.changeBg = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Install the change_bg wallpaper picker.";
    };

    wallpaperInputs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = defaultWallpaperInputs;
      description = "Flake input names that contain wallpaper collections.";
    };

    configFile = lib.mkOption {
      type = lib.types.str;
      default = "/etc/nixos/configuration.nix";
      description = "NixOS configuration file containing the change_bg markers.";
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ changeBg ];
  };
}
