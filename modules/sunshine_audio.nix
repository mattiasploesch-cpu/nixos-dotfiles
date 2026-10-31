# Home-Manager-Modul: virtueller Audio-Sink fuer Sunshine.
#
# Problem: Sunshine laeuft als root (kms-Capture) und bekommt sonst kein Audio
# zu fassen; lokal hoert man nur am HDMI-Ausgang. Dieses Modul spiegelt den
# aktuellen Standard-Ausgang per Loopback in den Sink "sunshine", den Sunshine
# als audio_sink captured. Lokale Wiedergabe bleibt unveraendert.
#
# Der zugehoerige systemd-User-Service ist hier definiert und wird mit dem
# Profil aktiviert - es muss nichts von Hand gestartet werden.
{ config, lib, pkgs, ... }:

let
  cfg = config.services.sunshineAudio;

  script = pkgs.writeShellScript "sunshine-audio" ''
    set -eu
    export PATH="${lib.makeBinPath [
      pkgs.pulseaudio
      pkgs.coreutils
      pkgs.gnugrep
      pkgs.gawk
    ]}"

    # Auf pipewire-pulse warten (Login-Reihenfolge).
    tries=0
    until pactl info >/dev/null 2>&1; do
      tries=$((tries + 1))
      [ "$tries" -gt 60 ] && exit 0
      sleep 0.5
    done

    # Virtueller Sink, den Sunshine als audio_sink benutzt.
    if ! pactl list short sinks | awk -F'\t' '$2 == "${cfg.sinkName}" { found = 1 } END { exit !found }'; then
      pactl load-module module-null-sink \
        sink_name=${cfg.sinkName} \
        sink_properties=device.description=Sunshine
    fi

    # Spiegel des aktuellen Standard-Ausgangs (Quelle: dessen Monitor).
    if ! pactl list short modules | grep -q "sink=${cfg.sinkName}"; then
      default_sink="$(pactl get-default-sink)"
      pactl load-module module-loopback \
        source="$default_sink.monitor" \
        sink=${cfg.sinkName} \
        latency_msec=${toString cfg.latencyMs}
    fi
  '';
in
{
  options.services.sunshineAudio = {
    enable = lib.mkEnableOption ''
      virtuellen Audio-Sink fuer Sunshine; der Standard-Ausgang wird per
      Loopback hineingespiegelt, Sunshine captured ihn (audio_sink)
    '';

    sinkName = lib.mkOption {
      type = lib.types.str;
      default = "sunshine";
      description = ''
        Name des virtuellen Sinks. Muss zu audio_sink in
        ~/.config/sunshine/sunshine.conf passen.
      '';
    };

    latencyMs = lib.mkOption {
      type = lib.types.int;
      default = 30;
      description = "Latenz des Loopbacks in Millisekunden.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.user.services.sunshine-audio = {
      Unit = {
        Description = "Virtueller Audio-Sink fuer Sunshine";
        After = [ "pipewire-pulse.service" ];
        Wants = [ "pipewire-pulse.service" ];
      };
      Service = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${script}";
      };
      Install.WantedBy = [ "default.target" ];
    };
  };
}
