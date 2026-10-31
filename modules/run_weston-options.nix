# Gemeinsame Optionen fuer das Home-Manager-Modul (run_weston.nix) und das
# NixOS-Modul (run_weston-nixos.nix). Die Optionen werden von
# run_weston-config.nix in eine config.toml uebersetzt.
{ lib, pkgs, ... }:

let
  inherit (lib) mkOption types;

  entryOptions = {
    name = mkOption {
      type = types.str;
      description = "Name des Eintrags, wird der TOML-Schluessel (z. B. \"niri\").";
    };

    enable = mkOption {
      type = types.bool;
      default = true;
      description = "Mit false laesst sich ein Standardeintrag entfernen.";
    };

    command = mkOption {
      type = types.listOf types.str;
      example = [ "niri" ];
      description = ''
        Argumentliste des Programms. Keine Shell-Auswertung, jedes Element ist
        genau ein Argument.
      '';
    };

    requires = mkOption {
      type = types.listOf types.str;
      default = [ ];
      example = [ "niri" ];
      description = "Namen der Abhaengigkeiten (Compositoren oder Programme).";
    };

    socket = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "wayland-weston";
      description = "Erwarteter Wayland-Socket; sonst wird er dynamisch ermittelt.";
    };

    socketDetection = mkOption {
      type = types.nullOr (types.enum [ "auto" "cmdline" "log" "scan" "none" ]);
      default = null;
      description = "Wie der Socket gefunden wird (Standard: auto).";
    };

    readyCheck = mkOption {
      type = types.nullOr (types.enum [ "process" "socket" "log" "command" "exit" ]);
      default = null;
      description = "Bereitschaftspruefung (Compositor-Standard: socket, sonst process).";
    };

    readyPattern = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = ''listening on Wayland socket: (?P<socket>\S+)'';
      description = "Regex fuer readyCheck = \"log\"; Gruppe \"socket\" liefert den Socket.";
    };

    readyCommand = mkOption {
      type = types.nullOr (types.listOf types.str);
      default = null;
      description = "Pruefbefehl fuer readyCheck = \"command\" (Exit-Code 0 = bereit).";
    };

    backend = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "x11";
      description = "Backend des Compositors; \"x11\" entfernt WAYLAND_DISPLAY.";
    };

    startupTimeout = mkOption {
      type = types.nullOr (types.either types.int types.float);
      default = null;
      description = "Start-Timeout in Sekunden (Standard aus settings.startupTimeout).";
    };

    stopOnExit = mkOption {
      type = types.nullOr types.bool;
      default = null;
      description = "Beim Beenden des Managers mitbeenden (Standard aus settings.stopOnExit).";
    };

    env = mkOption {
      type = types.attrsOf types.str;
      default = { };
      example = { ELECTRON_OZONE_PLATFORM_HINT = "auto"; };
      description = "Zusaetzliche Umgebungsvariablen fuer diesen Eintrag.";
    };

    unsetEnv = mkOption {
      type = types.listOf types.str;
      default = [ ];
      example = [ "DISPLAY" ];
      description = ''
        Vom Host geerbte Variablen, die entfernt werden. Mit ["DISPLAY"]
        startet ein Wayland-Programm nicht in der X11-Sitzung des Hosts.
      '';
    };
  };
in
{
  options.programs.runWeston = {
    enable = mkOption {
      type = types.bool;
      default = true;
      description = "run_weston (Weston Session Manager) installieren und konfigurieren.";
    };

    package = mkOption {
      type = types.package;
      default = pkgs.callPackage ../run_weston/package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ../run_weston/package.nix { }";
      description = "Paket, das das Programm run_weston bereitstellt.";
    };

    extraPackages = mkOption {
      type = types.listOf types.package;
      default = [ ];
      example = lib.literalExpression "[ pkgs.weston pkgs.niri pkgs.foot pkgs.dms ]";
      description = ''
        Programme, die run_weston starten soll. Sie landen auf dem PATH des
        Managers, damit Befehlslisten ohne absolute Pfade funktionieren.
      '';
    };

    installConfig = mkOption {
      type = types.bool;
      default = true;
      description = "Die aus den Optionen erzeugte config.toml installieren.";
    };

    configFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = ''
        Eigene config.toml statt der generierten verwenden. Mit null wird die
        Datei aus settings, compositors, programs und unknown erzeugt.
      '';
    };

    settings = {
      defaultCompositor = mkOption {
        type = types.str;
        default = "weston";
        description = "Compositor fuer 'run --env default ...'.";
      };

      startupTimeout = mkOption {
        type = types.either types.int types.float;
        default = 15;
        description = "Standard-Start-Timeout in Sekunden.";
      };

      healthCheckInterval = mkOption {
        type = types.either types.int types.float;
        default = 1;
        description = "Intervall der Statuspruefung in Sekunden.";
      };

      killTimeout = mkOption {
        type = types.either types.int types.float;
        default = 5;
        description = "Wartezeit zwischen SIGTERM und SIGKILL in Sekunden.";
      };

      reuseExisting = mkOption {
        type = types.bool;
        default = true;
        description = "Bereits laufende Instanzen wiederverwenden.";
      };

      stopOnExit = mkOption {
        type = types.bool;
        default = true;
        description = "Verwaltete Programme beim Beenden des Managers beenden.";
      };

      allowUnknownPrograms = mkOption {
        type = types.bool;
        default = false;
        description = "Unbekannte Befehle in der Standardumgebung starten.";
      };
    };

    compositors = mkOption {
      type = types.listOf (types.submodule { options = entryOptions; });
      default = [ ];
      example = lib.literalExpression ''
        [
          {
            name = "weston";
            command = [ "weston" "--backend=x11" "--socket=wayland-weston" ];
            socket = "wayland-weston";
            backend = "x11";
            stopOnExit = false;
          }
        ]
      '';
      description = ''
        Compositoren. Die Standardeintraege (weston) sind immer enthalten; ein
        Eintrag mit gleichem Namen ersetzt sie, enable = false entfernt sie.
      '';
    };

    programs = mkOption {
      type = types.listOf (types.submodule { options = entryOptions; });
      default = [ ];
      example = lib.literalExpression ''
        [
          { name = "niri"; command = [ "niri" ]; requires = [ "weston" ]; }
          { name = "discord"; command = [ "discord" ]; requires = [ "niri" ]; unsetEnv = [ "DISPLAY" ]; }
        ]
      '';
      description = ''
        Programme (inkl. weiterer Compositoren wie niri). Die Standardeintraege
        (niri, dms, foot, alacritty, discord, spotify, wayland-info) sind immer
        enthalten; ein Eintrag mit gleichem Namen ersetzt sie, enable = false
        entfernt sie.
      '';
    };

    unknown = {
      allow = mkOption {
        type = types.bool;
        default = false;
        description = "Unbekannte Programme ohne 'run --env' starten.";
      };

      env = mkOption {
        type = types.nullOr types.str;
        default = "niri";
        description = "Standardumgebung fuer unbekannte Programme.";
      };

      readyCheck = mkOption {
        type = types.enum [ "process" "socket" "log" "command" "exit" ];
        default = "process";
        description = "Bereitschaftspruefung fuer unbekannte Programme.";
      };
    };
  };
}
