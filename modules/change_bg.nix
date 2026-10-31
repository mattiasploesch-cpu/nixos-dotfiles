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

  changeBg = pkgs.writeShellApplication {
    name = "change_bg";
    runtimeInputs = with pkgs; [
      chafa
      coreutils
      findutils
      fzf
      gawk
      gnugrep
      gnused
      perl
      xwallpaper
    ];
    text = ''
      set -euo pipefail

      manifest="${manifest}"
      config_file="''${CHANGE_BG_CONFIG:-${cfg.configFile}}"

      if ! command -v fzf >/dev/null 2>&1; then
        echo "fzf ist nicht im PATH." >&2
        exit 1
      fi

      # 1. Wallpaper-Paket auswählen
      source_line=$(
        fzf --delimiter=$'\t' --with-nth=1 \
            --height=80% --reverse --border \
            --prompt='Wallpaper-Paket > ' \
            --header='Enter: Paket öffnen · Esc: abbrechen' \
            < "$manifest"
      ) || exit 0

      IFS=$'\t' read -r pkg_name pkg_path pkg_expr <<< "$source_line"

      tmp=$(mktemp)
      tmp_cfg=$(mktemp)
      tmp_final=$(mktemp)
      trap 'rm -f "$tmp" "$tmp_cfg" "$tmp_final"' EXIT

      # 2. Bild im Paket auswählen, mit chafa-Vorschau
      find "$pkg_path" -type f \
        \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
           -o -iname '*.webp' -o -iname '*.bmp' -o -iname '*.gif' \) \
        -printf '%P\t%p\0' > "$tmp"

      selected=$(
        fzf --read0 --delimiter=$'\t' --with-nth=1 \
            --height=90% --reverse --border \
            --prompt='Wallpaper > ' \
            --header='Enter: auswählen · Esc: abbrechen' \
            --preview='chafa --format symbols --size 80x40 {2}' \
            --preview-window='right,60%,border-left' \
            < "$tmp"
      ) || exit 0

      IFS=$'\t' read -r rel full <<< "$selected"

      # Nix-String-Escaping für den relativen Pfad
      rel_escaped=$(printf '%s' "$rel" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')

      # Baut die Zeile, die in configuration.nix landet:
      # (sleep 2; <xwallpaper>/bin/xwallpaper --zoom "<inputs.name + /rel>") &
      dollar='$'
      wall_line=$(printf '      (sleep 2; %s{pkgs.xwallpaper}/bin/xwallpaper --zoom "%s{%s + "/%s"}") &' \
        "$dollar" "$dollar" "$pkg_expr" "$rel_escaped")

      # 3. Falls noch keine Marker existieren, einmalig configuration.nix
      #    vorbereiten: inputs in die Modul-Argumente aufnehmen und die
      #    bestehende xwallpaper-Zeile markieren.
      cp "$config_file" "$tmp_cfg"

      # inputs in die Modul-Argumente aufnehmen, falls noch nicht vorhanden.
      perl -0pi -e '
        s/^(\{\s*config,\s*lib,\s*pkgs,)(?!\s*inputs)/$1 inputs,/m;
      ' "$tmp_cfg"

      # Einmalig die bestehende xwallpaper-Zeile markieren.
      if ! grep -q '# >>> change_bg >>>' "$tmp_cfg"; then
        perl -0pi -e '
          s/^([ \t]*)(.*xwallpaper.*--zoom.*)$/$1# >>> change_bg >>>\n$1$2\n$1# <<< change_bg <<</m;
        ' "$tmp_cfg"

        if ! grep -q '# >>> change_bg >>>' "$tmp_cfg"; then
          echo "Fehler: Konnte die change_bg-Marker in $config_file nicht setzen." >&2
          echo "Bitte die xwallpaper-Zeile in configuration.nix prüfen." >&2
          exit 1
        fi
      fi

      # 4. Marker-Block durch die neue Auswahl ersetzen
      WALL_LINE="$wall_line" awk '
        /# >>> change_bg >>>/ {
          print "      # >>> change_bg >>>"
          print ENVIRON["WALL_LINE"]
          print "      # <<< change_bg <<<"
          skip = 1
          next
        }
        /# <<< change_bg <<</ { skip = 0; next }
        !skip { print }
      ' "$tmp_cfg" > "$tmp_final"

      # 5. configuration.nix schreiben (sudo nur wenn nötig)
      if [ -w "$config_file" ]; then
        cat "$tmp_final" > "$config_file"
      else
        sudo cp "$tmp_final" "$config_file"
      fi

      # 6. Sofort anwenden – kein nrs!
      xwallpaper --zoom "$full"

      printf '\nPaket:    %s\n' "$pkg_name"
      printf 'Wallpaper: %s\n' "$rel"
      printf 'Config:    %s\n' "$config_file"
      printf 'Nicht vergessen: nrs selbst ausführen.\n'
    '';
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
