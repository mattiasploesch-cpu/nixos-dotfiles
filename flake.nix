
{
  description = "Versioned dotfiles for Home Manager";

  outputs = { self }: {
    homeManagerModules.dotfiles = { ... }: {
      xdg.configFile."qtile".source = "${self}/qtile";
        "${self}/alacritty/alacritty.toml";
    };
  };
}
