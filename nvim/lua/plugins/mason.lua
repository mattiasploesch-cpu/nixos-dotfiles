-- Every server and tool is installed by Home Manager (see modules/neovim.nix).
-- LazyVim otherwise asks Mason to download/build a long list at startup, which
-- fails for toolchains that are not installed and leaves broken Mason packages.
return {
  {
    "mason-org/mason-lspconfig.nvim",
    init = function()
      local ok, mason_lspconfig = pcall(require, "mason-lspconfig")
      if not ok then
        return
      end
      local setup = mason_lspconfig.setup
      mason_lspconfig.setup = function(opts)
        opts = opts or {}
        opts.ensure_installed = {}
        return setup(opts)
      end
    end,
  },
  {
    "mason-org/mason.nvim",
    opts = function(_, opts)
      opts = opts or {}
      opts.ensure_installed = {}
    end,
  },
}
