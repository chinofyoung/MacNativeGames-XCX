# The shader translator, ready-made

`totk_xlate` is the program the game starts to translate its shaders (`totk_xlate --service`): the Ryujinx shader
translator (`vendor/ryujinx`, MIT) with this project's patches (`games/xcx/tools/totk_xlate/patches`) and the service
(`games/xcx/tools/totk_xlate/Service.cs`: Maxwell code -> SPIR-V -> SPIRV-Cross -> Metal Shading Language and its
reflection file), compiled ahead of time with .NET NativeAOT for arm64 macOS. It is here so that building the app needs
no .NET and the app needs no .NET runtime and no Python. (The name comes from the Tears of the Kingdom port it was first
written for; this copy was taken from it, through the Bayonetta 3 port, when PR #62 was merged.)

- Built with `games/xcx/tools/totk_xlate/publish_native.sh` from the sources in this repository (.NET SDK 10.0.401).
  The script writes its result here, replacing this binary and its SHA-256.
- `totk_xlate.sha256` is its SHA-256; `make shader-tools` (and so `make app`) refuses to use a different file.
- NativeAOT builds are not byte-for-byte reproducible across folders (paths end up in the binary), so a rebuild gets a
  different hash; what matters is that it translates the same. Check a rebuild by replaying a shader cache through it
  and comparing every MSL, SPIR-V and reflection file with the previous build. The build in this folder gave the same
  output as the .NET translator with the Python service for all 7,570 shader variants of the Tears of the Kingdom
  development cache.
