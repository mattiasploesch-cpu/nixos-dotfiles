"""Kanonische Standardkonfiguration fuer run_weston.

Diese Datei ist die einzige Quelle fuer `config.toml`: sie wird beim ersten
Start nach ~/.config/run_weston/config.toml geschrieben und vom Nix-Paket
zusaetzlich als share/run_weston/config.toml ausgeliefert.
"""

DEFAULT_CONFIG = '''# run_weston - Weston Session Manager
#
# Beim ersten Start wird diese Datei automatisch angelegt. Aenderungen werden
# mit "reload" in der TUI neu eingelesen, ein Neustart ist nicht noetig.
#
# Regel: "requires" listet Namen (Compositoren oder Programme). Der Manager
# startet alle Abhaengigkeiten rekursiv von unten nach oben, wartet auf deren
# Bereitschaft und gibt erst danach die Umgebung an das naechste Programm
# weiter. Zyklen werden beim Laden als Fehler gemeldet.
#
# Jeder Eintrag kann zusaetzlich:
#   env       = { KEY = "wert" }   Umgebungsvariablen setzen
#   unset_env = ["DISPLAY"]        vom Host geerbte Variablen entfernen
# Fuer Programme, die im Compositor laufen sollen, ist unset_env = ["DISPLAY"]
# wichtig: sonst wuerde z. B. Spotify das DISPLAY der X11-Sitzung (Qtile)
# erben und auf dem Host statt in Weston/Niri erscheinen.

[settings]
# Compositor, den "run --env default ..." verwendet.
default_compositor = "weston"
# Standard-Timeout in Sekunden, wenn ein Eintrag keinen eigenen angibt.
startup_timeout = 15
# Wie oft (Sekunden) Prozessstatus neu geprueft wird.
health_check_interval = 1
# Wartezeit zwischen SIGTERM und SIGKILL beim Beenden.
kill_timeout = 5
# Bereits laufende Instanzen (z. B. ein vorhandenes Weston) wiederverwenden.
reuse_existing = true
# Vom Manager gestartete Programme beim Beenden mitbeenden.
# Einzelne Eintraege koennen das mit stop_on_exit = false ueberschreiben.
stop_on_exit = true
# Unbekannte Programme ohne "run --env" nicht blind ausfuehren.
allow_unknown_programs = false

# ---------------------------------------------------------------------------
# Compositoren
# ---------------------------------------------------------------------------

[compositors.weston]
# Weston laeuft verschachtelt in der X11-Sitzung: --backend=x11 ist Pflicht,
# --backend=wayland wuerde eine nicht vorhandene Wayland-Sitzung voraussetzen.
command = ["weston", "--backend=x11", "--socket=wayland-weston"]
socket = "wayland-weston"
backend = "x11"
# Socket aus der Kommandozeile bzw. den offenen Dateien des Prozesses lesen.
socket_detection = "cmdline"
ready_check = "socket"
startup_timeout = 15
# Ein eigenes Weston ueberlebt das Beenden des Managers, damit die Sitzung
# beim naechsten Start wiederverwendet werden kann.
stop_on_exit = false

# ---------------------------------------------------------------------------
# Programme
# ---------------------------------------------------------------------------

[programs.niri]
command = ["niri"]
requires = ["weston"]
# Niri legt seinen Socket selbst an; der Name ist nicht vorhersagbar.
# Deshalb wird er aus dem Log ermittelt (Fallback: offene Sockets des Prozesses).
socket_detection = "log"
ready_pattern = 'listening on Wayland socket: (?P<socket>\\S+)'
ready_check = "log"
startup_timeout = 20

[programs.dms]
command = ["dms", "run"]
requires = ["niri"]
ready_check = "process"
startup_timeout = 15

[programs.foot]
command = ["foot"]
requires = ["niri"]
ready_check = "process"
unset_env = ["DISPLAY"]

[programs.alacritty]
# Argumente sind frei konfigurierbar, z. B. eine eigene Konfigurationsdatei:
#   command = ["alacritty", "--config-file", "/home/administrator/.config/alacritty/alacritty.toml"]
command = ["alacritty"]
requires = ["niri"]
ready_check = "process"
startup_timeout = 15
unset_env = ["DISPLAY"]

[programs.discord]
command = ["discord"]
requires = ["niri"]
ready_check = "process"
startup_timeout = 20
# Electron: ohne DISPLAY und mit Wayland-Socket nutzt Discord Wayland.
unset_env = ["DISPLAY"]
env = { ELECTRON_OZONE_PLATFORM_HINT = "auto" }

[programs.spotify]
command = ["spotify"]
requires = ["niri"]
ready_check = "process"
startup_timeout = 20
# Siehe discord: verhindert den Start in der X11-Sitzung des Hosts.
unset_env = ["DISPLAY"]
env = { ELECTRON_OZONE_PLATFORM_HINT = "auto" }

[programs.wayland-info]
command = ["wayland-info"]
requires = ["weston"]
# Diagnoseprogramm: gilt als erfolgreich, wenn es mit 0 beendet wird.
ready_check = "exit"
startup_timeout = 10

# Eigenes Programm hinzufuegen - einfach Block kopieren und anpassen.
#
# [programs.mein-programm]
# command = ["mein-programm", "--flag"]
# requires = ["niri"]
# ready_check = "process"
# unset_env = ["DISPLAY"]
# env = { MEINE_VARIABLE = "wert" }

# ---------------------------------------------------------------------------
# Unbekannte Programme
# ---------------------------------------------------------------------------

[unknown]
# false: unbekannte Befehle werden abgelehnt (kein blindes Ausfuehren).
# Erlaubt: "run --env niri mein-programm" nutzt eine definierte Umgebung.
allow = false
# Standard-Umgebung fuer "run --env default ..." und (falls allow = true)
# fuer unbekannte Befehle.
env = "niri"
ready_check = "process"
'''
