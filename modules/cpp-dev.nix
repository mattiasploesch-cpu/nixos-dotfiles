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
in
{
  environment.systemPackages = libs ++ tools;

  # CMake already derives /run/current-system/sw from the cmake binary, but
  # setting the prefix explicitly keeps bundled CMake builds (CLion) working.
  environment.sessionVariables = {
    CMAKE_PREFIX_PATH = "/run/current-system/sw";
  };
}
