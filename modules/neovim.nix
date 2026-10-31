# Neovim plus every binary its LSPs, formatters, pickers and treesitter setup
# need. The Lua configuration lives in ../nvim and is linked read-only by Home
# Manager; Mason is intentionally not used for installs.
{ dotfiles }:
{ pkgs, ... }:
let
  tools = with pkgs; [
    # editor plumbing
    git
    ripgrep
    fd
    lazygit
    tree-sitter
    gcc
    gnumake
    unzip
    curl

    # Neovim / Lua configuration
    lua-language-server
    stylua

    # Nix
    nil
    nixd
    nixfmt

    # Python
    python3
    basedpyright
    ruff

    # Rust
    rustc
    cargo
    rust-analyzer

    # C / C++
    clang
    clang-tools
    cmake
    gdb

    # JavaScript / TypeScript / web
    nodejs
    typescript
    typescript-language-server
    vscode-langservers-extracted
    prettierd
    eslint

    # Shell, YAML, TOML, Markdown
    bash-language-server
    shellcheck
    shfmt
    yaml-language-server
    taplo
    marksman
  ];
in
{
  # Single source of truth: the checkout of this flake.
  xdg.configFile."nvim".source = dotfiles + "/nvim";

  programs.neovim = {
    enable = true;
    defaultEditor = true;
    viAlias = true;
    vimAlias = true;
    withPython3 = true;
    # Put the servers and formatters on Neovim's PATH. This is what replaces
    # Mason's runtime downloads.
    extraPackages = tools;
  };
}
