{ lib, stdenv, python3, makeWrapper }:

stdenv.mkDerivation (finalAttrs: {
  pname = "run-weston";
  version = "0.1.0";

  src = lib.cleanSource ./.;

  nativeBuildInputs = [ makeWrapper python3 ];

  dontBuild = true;
  dontConfigure = true;

  installPhase = ''
    runHook preInstall

    mkdir -p $out/lib $out/bin $out/share/run_weston $out/share/doc/run-weston
    cp -r weston_session $out/lib/
    find $out/lib -name '__pycache__' -type d -prune -exec rm -rf {} +

    install -m644 README.md $out/share/doc/run-weston/README.md

    PYTHONPATH=$out/lib ${python3.interpreter} -c \
      'from weston_session.default_config import DEFAULT_CONFIG; print(DEFAULT_CONFIG, end="")' \
      > $out/share/run_weston/config.toml

    makeWrapper ${python3.interpreter} $out/bin/run_weston \
      --add-flags "-m weston_session" \
      --prefix PYTHONPATH : "$out/lib" \
      --set-default RUN_WESTON_DEFAULT_CONFIG "$out/share/run_weston/config.toml"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    $out/bin/run_weston --version
    PYTHONPATH=$out/lib ${python3.interpreter} -c \
      'from weston_session.config import parse_config; from weston_session.default_config import DEFAULT_CONFIG; parse_config(DEFAULT_CONFIG)'
    runHook postInstallCheck
  '';

  meta = {
    description = "Weston Session Manager: nested Weston in X11 with TUI process management";
    longDescription = ''
      Starts Weston with the X11 backend inside an existing X11 session (e.g. Qtile)
      or reuses a running instance, then starts compositors and Wayland programs
      according to a configurable dependency graph.
    '';
    homepage = "https://github.com/mattiasploesch-cpu/nixos-dotfiles";
    license = lib.licenses.mit;
    mainProgram = "run_weston";
    platforms = lib.platforms.linux;
  };
})
