/* Xenoblade Chronicles X: Definitive Edition, the game version this build runs, chosen at compile time by the Makefile's
   VERSION (make VERSION=v200 passes -DXCX_GAME_V200; scripts/app_bundle.py VERSIONS is the same table):
     v101  1.0.1  update v65536   main build ID 3F2425864CF226844511065F77AA3E36FB5FC33F (the default)
     v200  2.0.0  update v196608  main build ID EC4ED46BBC4A9EBBE7CE0AF7A4A8EBBC09E5724A
   No game-code address is defined on purpose: neither TotK's GAME_ADDR maps nor Bayonetta 3's addresses apply, so any
   leftover use is a compile error, and nothing here moves between 1.0.1 and 2.0.0 (the loader's tables are generated
   per version: <work>/build/<v>/runtime_tables.h). XCX addresses get named defines here when a patch needs one. */
#pragma once
#define GAME_XCX 1
#if defined(XCX_GAME_V200) && defined(XCX_GAME_V101)
#error "one game version: XCX_GAME_V101 or XCX_GAME_V200"
#elif defined(XCX_GAME_V200)
#define TOTK_GAME_VERSION "2.0.0"
#define TOTK_GAME_DIR "v200"
#else
#define TOTK_GAME_VERSION "1.0.1"
#define TOTK_GAME_DIR "v101"
#endif
#define GAME_TITLE_ID 0x0100453019aa8000ULL
