{ description = "Selected Home Manager dotfiles"; outputs = { self }: { homeManagerModules.qtile = { ... }: { xdg.configFile."qtile".source = "${self}/qtile"; }; }; };
