
{
  description = "Versioned dotfiles for Home Manager";

  outputs = { self }: {
    homeManagerModules.dotfiles = { ... }: {
      xdg.configFile."qtile".source = "${self}/qtile";
      xdg.configFile."alacritty/alacritty.toml".source =
        "${self}/alacritty/alacritty.toml";
    };
  };
}
