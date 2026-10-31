#!/usr/bin/env bash
set -euo pipefail

manifest="${CHANGE_BG_MANIFEST:?CHANGE_BG_MANIFEST ist nicht gesetzt}"
config_file="${CHANGE_BG_CONFIG:-${CHANGE_BG_DEFAULT_CONFIG:-/etc/nixos/configuration.nix}}"
preview_cmd="${CHANGE_BG_PREVIEW:?CHANGE_BG_PREVIEW ist nicht gesetzt}"
custom_dir="${XDG_DATA_HOME:-$HOME/.local/share}/change_bg/custom"

if ! command -v fzf >/dev/null 2>&1; then
  echo "fzf ist nicht im PATH." >&2
  exit 1
fi

tmpdir=$(mktemp -d)
ueberzug_pidfile="$tmpdir/ueberzug.pid"
ueberzug_launcher_pid=""
CHANGE_BG_UEBERZUG_SOCKET="${CHANGE_BG_UEBERZUG_SOCKET:-}"
export CHANGE_BG_UEBERZUG_SOCKET

PICK_KIND=""
PICK_NAME=""
PICK_PATH=""
PICK_EXPR=""
PICK_FILE=""
PICK_REL=""

cleanup() {
  if [ -n "${CHANGE_BG_UEBERZUG_SOCKET:-}" ] && [ -S "$CHANGE_BG_UEBERZUG_SOCKET" ]; then
    ueberzugpp cmd -s "$CHANGE_BG_UEBERZUG_SOCKET" -a remove -i change_bg_preview >/dev/null 2>&1 || true
  fi
  if [ -n "${ueberzug_launcher_pid:-}" ]; then
    kill "$ueberzug_launcher_pid" >/dev/null 2>&1 || true
    wait "$ueberzug_launcher_pid" 2>/dev/null || true
  fi
  rm -rf "$tmpdir"
}
trap cleanup EXIT

start_preview_daemon() {
  if [ -n "${CHANGE_BG_UEBERZUG_SOCKET:-}" ]; then
    return 0
  fi
  if [ -z "${DISPLAY:-}" ] || [ ! -t 1 ]; then
    return 0
  fi
  if ! command -v ueberzugpp >/dev/null 2>&1; then
    return 0
  fi

  ueberzugpp layer --output x11 --no-stdin --silent --pid-file "$ueberzug_pidfile" &
  ueberzug_launcher_pid=$!

  for _ in $(seq 1 100); do
    [ -s "$ueberzug_pidfile" ] && break
    sleep 0.05
  done
  if [ ! -s "$ueberzug_pidfile" ]; then
    return 0
  fi

  local pid sock
  pid=$(cat "$ueberzug_pidfile")
  for sock in \
    "${TMPDIR:-/tmp}/ueberzugpp-$pid.socket" \
    "/tmp/ueberzugpp-$pid.socket" \
    "${XDG_RUNTIME_DIR:-/tmp}/ueberzugpp-$pid.socket"; do
    if [ -S "$sock" ]; then
      CHANGE_BG_UEBERZUG_SOCKET="$sock"
      export CHANGE_BG_UEBERZUG_SOCKET
      return 0
    fi
  done
}

pick_package() {
  local entries="$tmpdir/packages"
  local name path expr line kind label rest

  : > "$entries"
  printf 'CUSTOM\t＋ Eigenes Bild (URL/Pfad)\0' >> "$entries"
  while IFS=$'\t' read -r name path expr; do
    [ -n "$name" ] || continue
    printf 'PKG\t%s\t%s\t%s\0' "$name" "$path" "$expr" >> "$entries"
  done < "$manifest"

  if ! fzf --read0 --print0 --delimiter=$'\t' --with-nth=2 \
      --height=80% --reverse --border \
      --prompt='Wallpaper-Paket > ' \
      --header='Enter: öffnen · Esc: abbrechen' \
      < "$entries" > "$tmpdir/selection"; then
    PICK_KIND="cancel"
    return 1
  fi

  IFS= read -r -d '' line < "$tmpdir/selection" || true
  IFS=$'\t' read -r kind label rest <<< "$line"
  case "$kind" in
    CUSTOM)
      PICK_KIND="custom"
      return 0
      ;;
    PKG)
      PICK_KIND="pkg"
      PICK_NAME="$label"
      IFS=$'\t' read -r PICK_PATH PICK_EXPR <<< "$rest"
      return 0
      ;;
    *)
      PICK_KIND="cancel"
      return 1
      ;;
  esac
}

pick_file_from_dir() {
  local dir="$1"
  local list="$tmpdir/custom-list"
  local sel name path

  find "$dir" -mindepth 1 -maxdepth 1 -type f ! -name '.*' \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
       -o -iname '*.webp' -o -iname '*.gif' -o -iname '*.bmp' \
       -o -iname '*.avif' -o -iname '*.tif' -o -iname '*.tiff' \) \
    -printf '%f\t%p\0' | sort -z > "$list"

  if [ ! -s "$list" ]; then
    echo "Keine Bilder in $dir gefunden." >&2
    return 1
  fi

  if ! fzf --read0 --print0 --delimiter=$'\t' --with-nth=1 \
      --height=80% --reverse --border \
      --prompt='Bild > ' \
      --preview="$preview_cmd {2}" \
      --preview-window='right,60%,border-left' \
      < "$list" > "$tmpdir/custom-sel"; then
    return 1
  fi

  IFS= read -r -d '' sel < "$tmpdir/custom-sel" || true
  IFS=$'\t' read -r name path <<< "$sel"
  [ -n "$path" ] || return 1
  printf '%s\n' "$path"
}

add_custom() {
  local input src name_hint hash base dest
  local tmpimg="$tmpdir/download"

  printf 'URL oder Pfad: ' >&2
  IFS= read -r input || return 1
  input="${input#"${input%%[![:space:]]*}"}"
  input="${input%"${input##*[![:space:]]}"}"
  [ -n "$input" ] || return 1

  if [[ "$input" =~ ^https?:// ]]; then
    if [[ "$input" =~ ^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$ ]]; then
      input="https://raw.githubusercontent.com/${BASH_REMATCH[1]}/${BASH_REMATCH[2]}/${BASH_REMATCH[3]}/${BASH_REMATCH[4]}"
    elif [[ "$input" =~ ^https://github\.com/([^/]+)/([^/]+)/raw/([^/]+)/(.+)$ ]]; then
      input="https://raw.githubusercontent.com/${BASH_REMATCH[1]}/${BASH_REMATCH[2]}/${BASH_REMATCH[3]}/${BASH_REMATCH[4]}"
    fi

    if ! curl -fsSL --retry 2 --max-time 60 -o "$tmpimg" "$input"; then
      echo "Download fehlgeschlagen: $input" >&2
      return 1
    fi
    src="$tmpimg"
    name_hint=$(basename "${input%%\?*}")
  else
    input="${input/#\~/$HOME}"
    input=$(realpath -m -- "$input")
    if [ -d "$input" ]; then
      src=$(pick_file_from_dir "$input") || return 1
      name_hint=$(basename "$src")
    elif [ -f "$input" ]; then
      src="$input"
      name_hint=$(basename "$src")
    else
      echo "Pfad nicht gefunden: $input" >&2
      return 1
    fi
  fi

  if ! magick identify "${src}[0]" >/dev/null 2>&1; then
    echo "Das ist keine lesbare Bilddatei: $input" >&2
    echo "Bei GitHub bitte eine Raw- oder Blob-URL zu einer Bilddatei verwenden." >&2
    return 1
  fi

  mkdir -p "$custom_dir"
  hash=$(printf '%s' "$input" | sha256sum | cut -c1-8)
  base="${name_hint%.*}"
  base=$(printf '%s' "$base" | tr -cs 'A-Za-z0-9._-' '_' | sed -e 's/^_*//' -e 's/_*$//')
  [ -n "$base" ] || base="custom"
  dest="$custom_dir/${base}-${hash}.jpg"

  if ! magick "${src}[0]" -auto-orient -background black -alpha remove -alpha off \
      -quality 95 "jpeg:$dest"; then
    echo "Konvertierung nach JPEG fehlgeschlagen." >&2
    return 1
  fi

  PICK_FILE="$dest"
  return 0
}

pick_image() {
  local root="$1"
  local pkg_name="$2"
  local current="$root"
  local entries="$tmpdir/entries"
  local d f line kind label path rel header

  while true; do
    : > "$entries"
    printf 'ROOT\t⬅ Zur Paketauswahl\0' >> "$entries"
    printf 'CUSTOM\t＋ Eigenes Bild (URL/Pfad)\0' >> "$entries"
    if [ "$current" != "$root" ]; then
      printf 'PARENT\t⬆ Übergeordneter Ordner\0' >> "$entries"
    fi

    while IFS= read -r -d '' d; do
      printf 'DIR\t[dir] %s/\t%s\0' "$(basename "$d")" "$d" >> "$entries"
    done < <(find "$current" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -print0 | sort -z)

    while IFS= read -r -d '' f; do
      printf 'FILE\t[img] %s\t%s\0' "$(basename "$f")" "$f" >> "$entries"
    done < <(find "$current" -mindepth 1 -maxdepth 1 -type f ! -name '.*' \
      \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
         -o -iname '*.webp' -o -iname '*.gif' -o -iname '*.bmp' \
         -o -iname '*.avif' -o -iname '*.tif' -o -iname '*.tiff' \) \
      -print0 | sort -z)

    rel="${current#"$root"}"
    [ -n "$rel" ] || rel="/"
    header="Paket: $pkg_name · Ordner: $rel"

    if ! fzf --read0 --print0 --delimiter=$'\t' --with-nth=2 \
        --height=90% --reverse --border \
        --prompt='Wallpaper > ' \
        --header="$header" \
        --preview="$preview_cmd {3}" \
        --preview-window='right,60%,border-left' \
        < "$entries" > "$tmpdir/selection"; then
      PICK_KIND="cancel"
      return 1
    fi

    IFS= read -r -d '' line < "$tmpdir/selection" || true
    IFS=$'\t' read -r kind label path <<< "$line"
    case "$kind" in
      ROOT)
        PICK_KIND="root"
        return 0
        ;;
      PARENT)
        if [ "$current" != "$root" ]; then
          current=$(dirname "$current")
          [ "$current" -ef "$root" ] && current="$root"
        fi
        ;;
      CUSTOM)
        if add_custom; then
          PICK_KIND="custom"
          return 0
        fi
        ;;
      DIR)
        current="$path"
        ;;
      FILE)
        PICK_KIND="file"
        PICK_FILE="$path"
        PICK_REL="${path#"$root"/}"
        return 0
        ;;
      *)
        PICK_KIND="cancel"
        return 1
        ;;
    esac
  done
}

apply_block() {
  local block="$1"
  local tmp_cfg="$tmpdir/config"
  local tmp_final="$tmpdir/config.new"

  cp "$config_file" "$tmp_cfg"

  # inputs in die Modul-Argumente aufnehmen, falls noch nicht vorhanden.
  perl -0pi -e 's/^(\{\s*config\s*,\s*lib\s*,\s*pkgs\s*,)(?!\s*inputs)/$1 inputs,/ms;' "$tmp_cfg"

  # Einmalig die bestehende xwallpaper-Zeile markieren.
  if ! grep -q '# >>> change_bg >>>' "$tmp_cfg"; then
    perl -0pi -e 's/^([ \t]*)(.*xwallpaper.*--zoom.*)$/$1# >>> change_bg >>>\n$1$2\n$1# <<< change_bg <<</m;' "$tmp_cfg"
    if ! grep -q '# >>> change_bg >>>' "$tmp_cfg"; then
      echo "Fehler: Konnte die change_bg-Marker in $config_file nicht setzen." >&2
      echo "Bitte die xwallpaper-Zeile in configuration.nix prüfen." >&2
      return 1
    fi
  fi

  if ! grep -q '# <<< change_bg <<<' "$tmp_cfg"; then
    echo "Fehler: change_bg-Marker in $config_file sind unvollständig." >&2
    return 1
  fi

  # Marker-Block durch die neue Auswahl ersetzen.
  awk -v blockfile="$block" '
    /# >>> change_bg >>>/ {
      while ((getline line < blockfile) > 0) print line
      close(blockfile)
      skip = 1
      next
    }
    /# <<< change_bg <<</ { skip = 0; next }
    !skip { print }
  ' "$tmp_cfg" > "$tmp_final"

  if [ -w "$config_file" ]; then
    cat "$tmp_final" > "$config_file"
  else
    sudo cp "$tmp_final" "$config_file"
  fi
}

write_config_input() {
  local pkg_name="$1"
  local pkg_expr="$2"
  local rel="$3"
  local rel_escaped block="$tmpdir/block"

  rel_escaped=$(printf '%s' "$rel" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\${/\\${/g')

  cat > "$block" <<EOF
      # >>> change_bg >>>
      # change_bg: $pkg_name/$rel
      (sleep 2; \${pkgs.xwallpaper}/bin/xwallpaper --zoom "\${
        pkgs.runCommand "change-bg-wallpaper.jpg"
          { nativeBuildInputs = [ pkgs.imagemagick ]; }
          ''magick "\${$pkg_expr + "/$rel_escaped"}[0]" -auto-orient -background black -alpha remove -alpha off -quality 95 "jpeg:\$out"''
      }") &
      # <<< change_bg <<<
EOF

  apply_block "$block"
}

write_config_custom() {
  local file="$1"
  local file_escaped block="$tmpdir/block"

  file_escaped=$(printf '%s' "$file" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\${/\\${/g')

  cat > "$block" <<EOF
      # >>> change_bg >>>
      # change_bg: custom $file
      (sleep 2; \${pkgs.xwallpaper}/bin/xwallpaper --zoom "$file_escaped") &
      # <<< change_bg <<<
EOF

  apply_block "$block"
}

print_summary() {
  local label="$1"
  local detail="$2"

  printf '\nAuswahl:   %s\n' "$label"
  printf 'Wallpaper: %s\n' "$detail"
  printf 'Config:    %s\n' "$config_file"
  printf '\nDie Config ist geschrieben. Führe jetzt nrs aus, um sie zu übernehmen.\n'
  printf 'Das Wallpaper selbst wird beim nächsten Login/Session-Start gesetzt.\n'
}

start_preview_daemon

while true; do
  if ! pick_package; then
    exit 0
  fi

  case "$PICK_KIND" in
    custom)
      if add_custom; then
        write_config_custom "$PICK_FILE" || exit 1
        print_summary "Eigenes Bild" "$PICK_FILE"
        exit 0
      fi
      ;;
    pkg)
      pkg_name="$PICK_NAME"
      pkg_path="$PICK_PATH"
      pkg_expr="$PICK_EXPR"

      while true; do
        if ! pick_image "$pkg_path" "$pkg_name"; then
          exit 0
        fi

        case "$PICK_KIND" in
          root)
            break
            ;;
          custom)
            write_config_custom "$PICK_FILE" || exit 1
            print_summary "Eigenes Bild" "$PICK_FILE"
            exit 0
            ;;
          file)
            if ! magick identify "${PICK_FILE}[0]" >/dev/null 2>&1; then
              echo "Bild kann nicht gelesen werden: $PICK_FILE" >&2
              continue
            fi
            write_config_input "$pkg_name" "$pkg_expr" "$PICK_REL" || exit 1
            print_summary "$pkg_name" "$PICK_REL"
            exit 0
            ;;
        esac
      done
      ;;
  esac
done
