# Uebersetzt die Moduloptionen in die TOML-Struktur von config.toml.
#
# Standardbelegung: run_weston/default-config.toml (dieselbe Datei, die das
# Paket ausliefert). Eigene Eintraege kommen dazu bzw. ersetzen einen
# Standardeintrag mit gleichem Namen; enable = false entfernt ihn.
{ lib }:

cfg:

let
  inherit (lib) filterAttrs;

  catalog = lib.importTOML ../run_weston/default-config.toml;

  # TOML benutzt snake_case, die Moduloptionen camelCase.
  rename = {
    socket_detection = "socketDetection";
    ready_check = "readyCheck";
    ready_pattern = "readyPattern";
    ready_command = "readyCommand";
    startup_timeout = "startupTimeout";
    stop_on_exit = "stopOnExit";
    unset_env = "unsetEnv";
  };

  camelise =
    attrs:
    builtins.listToAttrs (
      map
        (
          key:
          {
            name = rename.${key} or key;
            value = attrs.${key};
          }
        )
        (builtins.attrNames attrs)
    );

  fromCatalog =
    table:
    map (name: camelise (table.${name} // { inherit name; })) (builtins.attrNames table);

  defaultCompositors = fromCatalog (catalog.compositors or { });
  defaultPrograms = fromCatalog (catalog.programs or { });

  # Die Katalogeintraege sind absichtlich knapp; fehlende Felder bekommen hier
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
