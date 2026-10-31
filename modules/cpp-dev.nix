# Global C/C++ graphics toolchain: SDL3 with its satellite libraries, Vulkan,
# GLM and friends, plus CMake/Ninja/compilers. Installed system-wide so that a
# plain `cmake -S . -B build` works in every terminal, in Neovim/clangd and in
# GUI tools such as CLion — no nix-shell, no per-project shell.nix.
{ pkgs, ... }:
let
  libs = with pkgs; [
    # SDL3 and the official satellites
    sdl3
    sdl3-image
    sdl3-ttf
    sdl3-mixer
    sdl3-net
    sdl3-shadercross

    # Vulkan stack
    vulkan-headers
    vulkan-loader
    vulkan-tools
    vulkan-validation-layers
    vulkan-utility-libraries
    vulkan-memory-allocator
    vk-bootstrap
    glslang
    shaderc
    spirv-tools
    spirv-headers

    # math / windowing / asset loading
    glm
    glfw
    glew
    stb
    imgui
    assimp
    bullet
    libGL
    wayland
    wayland-protocols
    libxkbcommon
    freetype
    harfbuzz
  ];

  tools = with pkgs; [
    cmake
    ninja
    pkg-config
    gcc
    clang-tools # clangd, clang-format, clang-tidy
    gdb
    valgrind
    ccache
  ];

  # /run/current-system/sw only links the default output of a package. For
  # these libraries the headers, the CMake config and the .pc file live in the
  # dev/lib outputs, so they have to be collected into one environment
  # explicitly. Without this, find_package(SDL3) fails even though sdl3 is
  # listed in environment.systemPackages.
  stack = pkgs.buildEnv {
    name = "cpp-dev-stack";
    paths = libs ++ tools;
    extraOutputsToInstall = [ "out" "lib" "dev" "bin" ];
    ignoreCollisions = true;
  };
in
{
  environment.systemPackages = [ stack ];

  # Everything that resolves libraries at build time gets the stack prefix.
  # CMAKE_PREFIX_PATH is inherited by terminals, Neovim and CLion alike.
  environment.sessionVariables = {
    CMAKE_PREFIX_PATH = "${stack}";
    PKG_CONFIG_PATH = "${stack}/lib/pkgconfig";
    CMAKE_GENERATOR = "Ninja";
  };
}
