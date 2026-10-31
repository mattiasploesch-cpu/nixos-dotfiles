# run_weston - Weston Session Manager

Startet Weston verschachtelt in einer X11-Sitzung (Qtile), verwendet eine
bereits laufende Weston-Instanz weiter und verwaltet Wayland-Compositoren
(Niri) sowie Programme (DMS, foot, ...) ueber eine curses-TUI - inklusive
konfigurierbarer Abhaengigkeiten, Ready-Checks, Prozessgruppen und Logs.

Kein externer Python-Ballast: nur Standardbibliothek (Python >= 3.11 fuer
`tomllib`), keine `Textual`-Abhaengigkeit.

## Installation (flake)

Das Paket liegt im Repo: `run_weston/package.nix`, das Modul in
`modules/run_weston.nix`. Beides wird von `flake.nix` exportiert.

```nix
# flake.nix des Systems
inputs.dots.url = "github:mattiasploesch-cpu/nixos-dotfiles";

# Home-Manager
imports = [ inputs.dots.homeManagerModules.run_weston ];
programs.runWeston = {
  enable = true;
  extraPackages = with pkgs; [ weston niri foot alacritty discord spotify ];

  settings.startupTimeout = 20;

  # Programme als Liste; name ist der Schluessel in der config.toml.
  programs = [
    {
      name = "alacritty";
      command = [ "alacritty" ];
      requires = [ "niri" ];
      readyCheck = "process";
      unsetEnv = [ "DISPLAY" ];   # nicht in der X11-Sitzung des Hosts starten
    }
    {
      name = "spotify";
      command = [ "spotify" ];
      requires = [ "niri" ];
      startupTimeout = 25;
      unsetEnv = [ "DISPLAY" ];
      env.ELECTRON_OZONE_PLATFORM_HINT = "auto";
    }
  ];
};

# oder NixOS (configuration.nix)
imports = [ inputs.dots.nixosModules.run_weston ];
programs.runWeston = {
  enable = true;
  extraPackages = with pkgs; [ weston niri foot ];
};
```

Alternativ nur das Paket:

```nix
environment.systemPackages = [ inputs.dots.packages.${pkgs.system}.run-weston ];
# oder: nix run github:mattiasploesch-cpu/nixos-dotfiles
```

Optionen: `enable`, `package`, `extraPackages`, `installConfig`, `configFile`,
`settings`, `compositors`, `programs` (Listen, siehe oben), `unknown`.

Die `config.toml` wird aus diesen Optionen erzeugt (`pkgs.formats.toml`).
Die Standardeintraege (weston; niri, dms, foot, alacritty, discord, spotify,
wayland-info) sind immer enthalten, ein Eintrag mit gleichem `name` ersetzt sie,
`enable = false` entfernt ihn. `configFile = ...` verwendet stattdessen eine
eigene Datei.

`installConfig = true` legt die Datei unter `~/.config/run_weston/`
(Home-Manager) bzw. `/etc/run_weston/config.toml` (NixOS) ab. Der Manager nutzt
`~/.config/run_weston/config.toml`, wenn sie existiert, sonst die
Systemkonfiguration aus `/etc/run_weston/config.toml`, und legt andernfalls
die Standardkonfiguration an.

Die Standardbelegung (rund 60 Wayland-Programme mit passenden Argumenten und
Umgebungen) steht in `run_weston/default-config.toml`. Dieselbe Datei liefert
das Paket aus und lesen die Module per `lib.importTOML` - es gibt also nur eine
Quelle. Eigene Eintraege in `programs` ueberschreiben nur ihren Namen.

### Sunshine-Audio (optional)

```nix
imports = [ inputs.dots.homeManagerModules.sunshine_audio ];
services.sunshineAudio.enable = true;   # Sink "sunshine" + Loopback als User-Service
```

Der Service wird vom Modul aktiviert (kein Handstart). In
`~/.config/sunshine/sunshine.conf` gehoert dazu `audio_sink = sunshine`.

## Start

```bash
run_weston              # TUI (startet im Terminal, Weston startet im Hintergrund)
run_weston doctor       # Systempruefung
run_weston status       # Konfiguration + laufende verwaltete Prozesse
run_weston check        # Konfiguration validieren
run_weston logs niri    # Log eines Programms
```

Aus dem Repo ohne Nix-Build: `./run_weston/bin/run_weston`.

## TUI

```
╔════════════════════════════════════════════════════════════════════╗
║                     WESTON SESSION MANAGER 0.1.0                   ║
╠════════════════════════════════════════════════════════════════════╣
║ WESTON: READY   NIRI: READY   ACTIVE DISPLAY: wayland-1            ║
╠════════════════════════════════════════════════════════════════════╣
║ PROZESSE                     ║ Ausgabe                            ║
║ [01] weston      READY       ║ > dms run                          ║
║ [02] niri        READY       ║ dms gestartet (PID 4242)           ║
║ [03] dms         RUNNING     ║ niri ist bereit, Socket wayland-1  ║
╠══════════════════════════════╩═════════════════════════════════════╣
║ > _                                                                ║
║ [k NUM] Kill  [logs NUM] Logs  [refresh] Status  [exit] Beenden    ║
╚════════════════════════════════════════════════════════════════════╝
```

Kommandos: `<programm> [args]`, `run --env <name> <cmd...>`, `k <nummer>`,
`logs [nummer|name]`, `status`, `doctor`, `reload`, `refresh`, `clear`, `help`,
`exit`. `Tab` vervollstaendigt, Pfeiltasten blaettern im Verlauf,
`PgUp`/`PgDn` scrollen die Ausgabe. `Ctrl+C` beendet nichts - Prozesse gehen
nur ueber `k NUM` oder `exit` (mit `stop_on_exit`) zu Ende.

Starts laufen immer in Worker-Threads, die TUI bleibt bedienbar.

## Konfiguration

`~/.config/run_weston/config.toml` (wird beim ersten Start angelegt):

```toml
[settings]
default_compositor = "weston"
startup_timeout = 15
health_check_interval = 1
kill_timeout = 5
reuse_existing = true
stop_on_exit = true
allow_unknown_programs = false

[compositors.weston]
command = ["weston", "--backend=x11", "--socket=wayland-weston"]
socket = "wayland-weston"
backend = "x11"
socket_detection = "cmdline"
ready_check = "socket"
stop_on_exit = false          # Sitzung ueberlebt den Manager

[programs.niri]
command = ["niri"]
requires = ["weston"]
socket_detection = "log"
ready_pattern = 'listening on Wayland socket: (?P<socket>\S+)'
ready_check = "log"
startup_timeout = 20

[programs.dms]
command = ["dms", "run"]
requires = ["niri"]
ready_check = "process"

[unknown]
allow = false
env = "niri"
ready_check = "process"
```

Compositoren und Programme teilen sich ein Schema:

| Feld | Bedeutung |
| --- | --- |
| `command` | Argumentliste, wird ohne Shell ausgefuehrt (kein Injection-Risiko) |
| `requires` | Namen weiterer Eintraege, rekursiv aufgeloest |
| `backend` | `x11`, `wayland`, ... - `x11` entfernt geerbtes `WAYLAND_DISPLAY` |
| `socket` | erwarteter Socketname |
| `socket_detection` | `auto`, `cmdline`, `log`, `scan`, `none` |
| `ready_pattern` | Regex auf das Log, benannte Gruppe `socket` erlaubt Socket-Erkennung |
| `ready_check` | `process`, `socket`, `log`, `command`, `exit` |
| `ready_command` | Pflicht bei `ready_check = "command"` |
| `startup_timeout` | Timeout in Sekunden |
| `env` | zusaetzliche Umgebungsvariablen (haben Vorrang vor `unset_env`) |
| `unset_env` | geerbte Variablen entfernen, z. B. `["DISPLAY"]` |
| `stop_on_exit` | ueberschreibt `[settings] stop_on_exit` |

`unset_env = ["DISPLAY"]` ist fuer Wayland-Programme wichtig: der Manager erbt
`DISPLAY=:0` von der X11-Sitzung, sonst wuerde z. B. Spotify (Electron) auf dem
Host statt in Weston/Niri erscheinen. Mit entferntem `DISPLAY` und gesetztem
`WAYLAND_DISPLAY` nutzen Spotify und Discord Wayland.

Zyklische Abhaengigkeiten, unbekannte `requires`, ungueltige Werte und
TOML-Syntaxfehler werden mit Position (Zeile) gemeldet; bei Fehlern startet der
Manager nichts. `reload` laedt die Datei ohne Neustart neu (bei Fehlern bleibt
die alte Konfiguration aktiv).

## Verhalten

- Bereits laufendes Weston/Niri wird ueber `/proc` + Socket-Probe erkannt und
  wiederverwendet; es wird nie ein zweiter Compositor gestartet. Das gilt fuer
  Compositoren und fuer Eintraege mit `ready_check = "socket"`/`"log"` (Niri
  steht in der Regel unter `[programs.niri]`). Reine Programme wie `dms` oder
  `foot` werden nie faelschlich als "schon laufend" uebernommen.
- Fremde Prozesse werden nie beendet. Vom Manager gestartete Prozesse laufen in
  eigener Prozessgruppe (`start_new_session=True`), werden per SIGTERM und nach
  `kill_timeout` per SIGKILL beendet; PID-Recycling wird ueber die Prozess-
  Startzeit aus `/proc/<pid>/stat` erkannt.
- Nach einem Manager-Absturz werden uebernommene Prozesse aus dem State-File
  als "extern" gefuehrt und nicht beendet.
- Ready-Checks: `process` (laeuft noch), `socket` (verbindbar), `log` (Muster
  im Log, optional Socketnamen), `command` (Pruefbefehl Exit 0), `exit`
  (Programm endet mit 0).
- Logs: `~/.local/state/run_weston/logs/` (`manager.log`, ein Log pro Programm).
- State: `~/.local/state/run_weston/state/managed.json`.

## Tests

```bash
cd run_weston
python -m unittest discover -s tests -t .   # oder: pytest tests
```

Die Tests starten echte, aber winzige Hilfsprozesse (Python-Skripte mit
eigenen Unix-Sockets) und brauchen weder Weston noch Niri. Abgedeckt sind u. a.
Konfigurationsparser, Zyklen, Weston-Erkennung/-Start/-Fehler, dynamische
Niri-Socket-Erkennung, Startreihenfolge inkl. Umgebungen, Ready-Checks,
Prozessgruppen, Kindprozesse, Logs, Nicht-Blockieren der TUI und das
Uebernehmen von Prozessen aus einem frueheren Lauf.

## Integrationstest

`doctor` ist gefahrlos. Ein vollstaendiger Sitzungstest (Weston + Niri + DMS)
wird nur vom Benutzer in seiner X11-Sitzung gestartet, weil er Fenster oeffnet;
er laeuft nicht in dieser Umgebung. Ein bereits laufendes Weston wird dabei
wiederverwendet, nie beendet.

## Grenzen

- RDP/VNC und echte DRM-Backends sind nicht getestet (Auftrag: X11).
- Weston 16 und Niri 26: das Logmuster fuer Niri ist konfigurierbar, weil
  Niri-Versionen unterschiedlich loggen; Fallback ist die Socket-Erkennung
  ueber offene Dateien des Prozesses.
- Electron-Apps brauchen meist `--ozone-platform=wayland` (Spotify zusaetzlich
  `--disable-gpu`): ohne das Flag weichen sie ohne `DISPLAY` auf X11 aus und
  beenden sich still mit Exit 1, mit Flag scheitert im verschachtelten Weston
  sonst der Chromium-GPU-Prozess.
- Bei mehreren vorhandenen Sitzungen waehlt der Manager die zusammenpassende
  Instanz: ein laufendes Niri bestimmt per `WAYLAND_DISPLAY` sein Weston, und
  ein wiederverwendetes Niri muss auf dem gewaehlten Weston haengen. Sonst
  warnt er explizit vor getrennten Desktops.
- DMS ist nicht Teil von nixpkgs; es muss ueber `extraPackages` oder eine
  eigene Package-Quelle bereitgestellt werden.
