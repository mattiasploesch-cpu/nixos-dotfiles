# Uebersetzt die Moduloptionen in die TOML-Struktur von config.toml.
#
# Die Standardeintraege spiegeln run_weston/weston_session/default_config.py.
# Eigene Eintraege kommen dazu bzw. ersetzen einen Standardeintrag mit
# gleichem Namen; enable = false entfernt ihn.
{ lib }:

cfg:

let
  inherit (lib) filterAttrs;

  defaultCompositors = [
    {
      name = "weston";
      command = [ "weston" "--backend=x11" "--socket=wayland-weston" ];
      socket = "wayland-weston";
      backend = "x11";
      socketDetection = "cmdline";
      readyCheck = "socket";
      startupTimeout = 15;
      stopOnExit = false;
    }
  ];

  defaultPrograms = [
    {
      name = "niri";
      command = [ "niri" ];
      requires = [ "weston" ];
      socketDetection = "log";
      readyPattern = "listening on Wayland socket: (?P<socket>\\S+)";
      readyCheck = "log";
      startupTimeout = 20;
    }
    {
      name = "dms";
      command = [ "dms" "run" ];
      requires = [ "niri" ];
      readyCheck = "process";
      startupTimeout = 15;
    }
    {
      name = "foot";
      command = [ "foot" ];
      requires = [ "niri" ];
      readyCheck = "process";
      unsetEnv = [ "DISPLAY" ];
    }
    {
      name = "alacritty";
      command = [ "alacritty" ];
      requires = [ "niri" ];
      readyCheck = "process";
      startupTimeout = 15;
      unsetEnv = [ "DISPLAY" ];
    }
    {
      name = "discord";
      command = [ "discord" "--ozone-platform=wayland" ];
      requires = [ "niri" ];
      readyCheck = "process";
      startupTimeout = 20;
      unsetEnv = [ "DISPLAY" ];
    }
    {
      name = "spotify";
      # --disable-gpu: im verschachtelten Weston scheitert sonst der
      # Chromium-GPU-Prozess ("GPU process isn't usable").
      command = [ "spotify" "--ozone-platform=wayland" "--disable-gpu" ];
      requires = [ "niri" ];
      readyCheck = "process";
      startupTimeout = 20;
      unsetEnv = [ "DISPLAY" ];
    }
    {
      name = "wayland-info";
      command = [ "wayland-info" ];
      requires = [ "weston" ];
      readyCheck = "exit";
      startupTimeout = 10;
    }
  ];

  # Die Defaults oben sind absichtlich knapp; fehlende Felder bekommen hier
  # dieselben Vorgaben wie in den Moduloptionen.
  normalise =
    item:
    {
      enable = true;
      requires = [ ];
      socket = null;
      socketDetection = null;
      readyCheck = null;
      readyPattern = null;
      readyCommand = null;
      backend = null;
      startupTimeout = null;
      stopOnExit = null;
      env = { };
      unsetEnv = [ ];
    }
    // item;

  # Leere Werte werden weggelassen, damit der Python-Parser seine eigenen
  # Standardwerte je Eintragsart (socket/process) anwenden kann.
  clean =
    attrs:
    filterAttrs (_: value: value != null && value != [ ] && value != { }) attrs;

  entry =
    value:
    clean {
      command = value.command;
      requires = value.requires;
      socket = value.socket;
      socket_detection = value.socketDetection;
      ready_check = value.readyCheck;
      ready_pattern = value.readyPattern;
      ready_command = value.readyCommand;
      backend = value.backend;
      startup_timeout = value.startupTimeout;
      stop_on_exit = value.stopOnExit;
      env = value.env;
      unset_env = value.unsetEnv;
    };

  merge =
    defaults: extra:
    let
      active = builtins.filter (item: item.enable) (
        map normalise (defaults ++ extra)
      );
      keyed = builtins.foldl' (acc: item: acc // { ${item.name} = item; }) { } active;
    in
    builtins.mapAttrs (_: entry) keyed;
in
clean {
  settings = clean {
    default_compositor = cfg.settings.defaultCompositor;
    startup_timeout = cfg.settings.startupTimeout;
    health_check_interval = cfg.settings.healthCheckInterval;
    kill_timeout = cfg.settings.killTimeout;
    reuse_existing = cfg.settings.reuseExisting;
    stop_on_exit = cfg.settings.stopOnExit;
    allow_unknown_programs = cfg.settings.allowUnknownPrograms;
  };

  compositors = merge defaultCompositors cfg.compositors;

  programs = merge defaultPrograms cfg.programs;

  unknown = clean {
    allow = cfg.unknown.allow;
    env = cfg.unknown.env;
    ready_check = cfg.unknown.readyCheck;
  };
}
