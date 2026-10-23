#include "game_patches.h"
#include "game_version.h"
#include <TargetConditionals.h>
#include <libkern/OSCacheControl.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The player's exefs patches (the mechanism is Bayonetta 3's; the port's own 60 fps patch is built in, below),
   applied to main after relocation and before its text is sealed. scripts/prepare_patches.py turns each .pchtxt into patches/<slug>.tsv ("offset new orig",
   hex), already checked against the original image and the rewrite; here every site is checked again against the loaded
   bytes, and a mismatch stops the start (a wrong version or a rewritten word must never be patched).
   XCX_PATCHES=<slug>,<slug>,... picks them (from the environment: the launcher passes it on as it is; unset or empty:
   none). */
static int hexbytes(const char *s, uint8_t *out, size_t cap, size_t *n) {
    size_t k = 0;
    for (; s[0] && s[1] && s[0] != '\t' && s[0] != '\n'; s += 2) {
        unsigned v; if (k == cap || sscanf(s, "%2x", &v) != 1) return 0;
        out[k++] = (uint8_t)v;
    }
    *n = k; return k > 0;
}
static void apply_file(Module *m, const char *slug) {
    char path[512]; snprintf(path, sizeof path, "patches/%s.tsv", slug);
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "[xcx] patch %s: %s missing (scripts/prepare_patches.py)\n", slug, path); runtime_fail("game patch table missing"); }
    char line[4096]; unsigned applied = 0;
    while (fgets(line, sizeof line, f)) {
        if (line[0] == '#' || line[0] == '\n') continue;
        uint64_t off = strtoull(line, NULL, 0); char *a = strchr(line, '\t'), *b = a ? strchr(a + 1, '\t') : NULL;
        uint8_t now[1024], was[1024]; size_t nn, nw;
        if (!a || !b || !hexbytes(a + 1, now, sizeof now, &nn) || !hexbytes(b + 1, was, sizeof was, &nw) || nn != nw)
            runtime_fail("malformed game patch table");
        if (off + nn > m->text_end && off < m->ro_off) runtime_fail("game patch in the rewrite area");
        if (off + nn > m->file_size) runtime_fail("game patch beyond main");
        if (memcmp(m->base + off, was, nw)) { fprintf(stderr, "[xcx] patch %s at main+%#llx: original bytes differ\n", slug, (unsigned long long)off); runtime_fail("game patch does not match this game version"); }
        memcpy(m->base + off, now, nn); sys_icache_invalidate(m->base + off, nn); applied++;
    }
    fclose(f);
    fprintf(stderr, "[xcx] game patch %s: %u sites\n", slug, applied);
}
/* 60 fps (XCX_PATCHES=60fps; off unless named; iOS: the Settings switch, ios/Sources/main.m). Found in
   the CHANGELOG entry "60 fps patch (experimental)" (main 2.0.0; 1.0.1 has the same code 0xfd40 bytes further on):
   - The cap is the game's own. At start-up it calls SetVsyncCount(2) (v200 main+0x13a2fd8, from main+0x13db540:
     mov w0, #2). The vsync count N sets the frame limiter at the end of every frame (main+0x13a9a50: spin on the system
     tick until period x N x 0.997 has passed since the frame began; period = the display's 1/60 s) and the frame time
     the game simulates with, GetFrameTime() (main+0x13a250c: period x N; N = 0 counts as 2). The present interval stays 1
     (nvnWindowSetPresentInterval is never called), so the host's 60 Hz present base never held the game: it presents
     every 33.2 ms on its own.
   - Site 1, the vsync count: the mov becomes a bl to a veneer that loads N from the host page (+4). With N = 1 the
     limiter ends at 16.6 ms, so the game runs at up to 60 fps.
   - Site 2, GetFrameTime: its first instruction branches to a veneer. When the host page's mode (+8) is 1 it returns the
     measured frame time (+0, seconds: runtime_frame_step_update at every present, the mean of the last 4 present
     intervals, each held to 1/60..1/20 s); mode 0 runs the original instruction and goes back. Without this the game
     simulates 1/60 s for every frame at N = 1, so any frame slower than 16.7 ms slows the game down (measured: walking
     6.4 m/s at 44 fps against 8.8 m/s at 30 fps). The game's other per-frame steps are listed in that entry.
   The host page is the writable 16 KiB page the loader reserves right after main's image (main's size + 0x4000).
   Veneers go in the zero padding between main's text and its read-only data. Every original word is checked first.
   XCX_FPS60=0 with the patch keeps N = 2 and the original GetFrameTime (A/B in one build; iOS's "off"). On iOS code pages
   cannot be written at run time: ios/tools/build_ipa.py exports main's text with these sites patched (TOTK_EXPORT_IOS)
   and the device only checks that the patched words are there; the Settings switch picks the page's values. */
#if defined(XCX_GAME_V200)
#define FPS60_VSYNC_SITE 0x13db540ULL       /* mov w0, #2; bl SetVsyncCount (main+0x13a2fd8) */
#define FPS60_FRAME_TIME 0x13a250cULL       /* GetFrameTime() */
#else
#define FPS60_VSYNC_SITE 0x13eb280ULL       /* mov w0, #2; bl SetVsyncCount (main+0x13b2d18) */
#define FPS60_FRAME_TIME 0x13b224cULL       /* GetFrameTime() */
#endif
#if TARGET_OS_IPHONE
#define CODE_PATCHED_AT_BUILD 1
#else
#define CODE_PATCHED_AT_BUILD 0
#endif
typedef struct { _Atomic uint32_t frame_bits; _Atomic int32_t vsync; _Atomic int32_t realtime; } Fps60Page;
static Fps60Page *fps60_page;
static int fps60_on;

static uint32_t enc_adrp(unsigned rd, uint64_t site, uint64_t target) {   /* offsets in main; main's base is page aligned */
    int64_t pages = (int64_t)(target >> 12) - (int64_t)(site >> 12);
    if (pages < -(1 << 20) || pages >= (1 << 20)) runtime_fail("60 fps: host page outside ADRP range");
    uint32_t imm = (uint32_t)pages & 0x1fffff;
    return 0x90000000u | ((imm & 3u) << 29) | ((imm >> 2) << 5) | (rd & 31u);
}
static uint32_t enc_branch(uint64_t site, uint64_t target, int link) {
    int64_t delta = (int64_t)target - (int64_t)site;
    if ((delta & 3) || delta < -(1 << 27) || delta >= (1 << 27)) runtime_fail("60 fps: branch outside range");
    return (link ? 0x94000000u : 0x14000000u) | ((uint32_t)(delta >> 2) & 0x3ffffffu);
}
static uint32_t word_at(const Module *m, uint64_t off) { uint32_t w; memcpy(&w, m->base + off, 4); return w; }
/* Mac: check the original word, then write. iOS: the embedded text must already hold the patched word. */
static void fps60_put(Module *m, uint64_t off, uint32_t original, uint32_t word, const char *what) {
    uint32_t have = word_at(m, off);
    if (CODE_PATCHED_AT_BUILD) {
        if (have != word) { fprintf(stderr, "[xcx] 60 fps %s main+%#llx: embedded %08x, expected patched %08x\n", what, (unsigned long long)off, have, word);
                            runtime_fail("embedded main text was not patched for 60 fps at build time (ios/tools/build_ipa.py)"); }
        return;
    }
    if (have != original) { fprintf(stderr, "[xcx] 60 fps %s main+%#llx: found %08x, expected %08x\n", what, (unsigned long long)off, have, original);
                            runtime_fail("60 fps patch does not match this game version"); }
    memcpy(m->base + off, &word, 4);
}
static void apply_fps60(Module *m) {
    const uint64_t page = (m->file_size + 0x3fff) & ~0x3fffULL;
    if (page + 0x4000 != m->size) runtime_fail("60 fps: host page layout");
    const uint64_t vsync = FPS60_VSYNC_SITE, ft = FPS60_FRAME_TIME;
    /* Checked only: the call after the mov goes to SetVsyncCount, and GetFrameTime's next two words are its prologue. */
    if (!CODE_PATCHED_AT_BUILD && word_at(m, vsync + 4) != 0x97ff1ea5u) runtime_fail("60 fps: SetVsyncCount call differs");
    if (word_at(m, ft + 4) != 0xf9000bf3u || word_at(m, ft + 8) != 0x910003fdu) runtime_fail("60 fps: GetFrameTime prologue differs");
    const uint64_t v0 = (m->text_end + 15) & ~15ULL, end = (m->text_end + 0xfff) & ~0xfffULL;
    const uint64_t v1 = v0 + 16;   /* veneer 1: 3 words (16 bytes slot); veneer 2: 8 words */
    const uint32_t ven_vsync[3] = {enc_adrp(0, v0, page), 0xb9400400u /* ldr w0, [x0, #4] */, 0xd65f03c0u /* ret */};
    const uint32_t ven_ft[8] = {enc_adrp(16, v1, page),
                                0xb9400a11u,                 /* ldr w17, [x16, #8]: mode */
                                0x34000071u,                 /* cbz w17, +12 (the original path) */
                                0xbd400200u,                 /* ldr s0, [x16]: measured frame time */
                                0xd65f03c0u,                 /* ret */
                                0xa9be7bfdu,                 /* stp x29, x30, [sp, #-0x20]!  (GetFrameTime's first word) */
                                enc_branch(v1 + 24, ft + 4, 0), 0};
    if (v1 + sizeof ven_ft > end) runtime_fail("60 fps: veneers reach read-only data");
    for (unsigned i = 0; i < 3; i++) fps60_put(m, v0 + 4 * i, 0, ven_vsync[i], "veneer (vsync count)");
    for (unsigned i = 0; i < 7; i++) fps60_put(m, v1 + 4 * i, 0, ven_ft[i], "veneer (frame time)");
    fps60_put(m, vsync, 0x52800040u, enc_branch(vsync, v0, 1), "vsync count (mov w0, #2)");
    fps60_put(m, ft, 0xa9be7bfdu, enc_branch(ft, v1, 0), "GetFrameTime");
    if (!CODE_PATCHED_AT_BUILD) sys_icache_invalidate(m->base + v0, (size_t)(v1 + 28 - v0));
    if (!CODE_PATCHED_AT_BUILD) { sys_icache_invalidate(m->base + vsync, 4); sys_icache_invalidate(m->base + ft, 4); }
    fps60_page = (Fps60Page *)(void *)(m->base + page);
    const char *e = getenv("XCX_FPS60"); fps60_on = !(e && !strcmp(e, "0"));
    float sixtieth = 1.0f / 60.0f; uint32_t bits; memcpy(&bits, &sixtieth, 4);
    atomic_store(&fps60_page->frame_bits, bits);
    atomic_store(&fps60_page->vsync, fps60_on ? 1 : 2);
    atomic_store(&fps60_page->realtime, fps60_on ? 1 : 0);
    fprintf(stderr, "[xcx] game patch 60fps (%s): %s; vsync count main+%#llx, frame time main+%#llx, veneers main+%#llx..+%#llx%s\n",
            TOTK_GAME_VERSION, fps60_on ? "on (vsync count 1, measured frame time)" : "off by XCX_FPS60=0 (vsync count 2, original frame time)",
            (unsigned long long)vsync, (unsigned long long)ft, (unsigned long long)v0, (unsigned long long)(v1 + 28),
            CODE_PATCHED_AT_BUILD ? " (embedded at build time)" : "");
}

void runtime_game_patches(Module *main_module) {
    const char *list = getenv("XCX_PATCHES");
    if (!list || !*list) { fprintf(stderr, "[xcx] game patches: none (XCX_PATCHES)\n"); return; }
    char buf[1024]; snprintf(buf, sizeof buf, "%s", list);
    for (char *save = NULL, *s = strtok_r(buf, ",", &save); s; s = strtok_r(NULL, ",", &save)) {
        for (const char *c = s; *c; c++) if (!((*c >= 'a' && *c <= 'z') || (*c >= '0' && *c <= '9') || *c == '-')) runtime_fail("XCX_PATCHES: bad patch name");
        if (!strcmp(s, "60fps")) { if (!fps60_page) apply_fps60(main_module); continue; }   /* built in (above), not a table */
        if (CODE_PATCHED_AT_BUILD) runtime_fail("XCX_PATCHES: only 60fps is available on iOS");
        apply_file(main_module, s);
    }
}

/* The measured frame time GetFrameTime returns while the 60 fps patch is on: the mean of the last 4 intervals between
   the game's presents, each held to 1/60..1/20 s (at most 1.5 times the step of the original 30 fps). A gap of more than
   0.25 s (loading, the window in the background) is not a frame: the samples start again and the last value stays.
   "framestep <ms>" in the input file pins it (diagnostics; 0 = measured again). */
static _Atomic uint32_t pinned_step_us;
static void frame_step_store(float seconds) { uint32_t bits; memcpy(&bits, &seconds, 4); atomic_store_explicit(&fps60_page->frame_bits, bits, memory_order_release); }
void runtime_frame_step_pin(double ms) {
    uint32_t us = ms > 0 ? (uint32_t)(ms * 1000.0 + 0.5) : 0;
    atomic_store(&pinned_step_us, us);
    if (fps60_page && fps60_on && us) frame_step_store((float)(us / 1e6));
    fprintf(stderr, "[xcx] frame step %s%s\n", us ? "pinned (diagnostics)" : "measured again", fps60_page && fps60_on ? "" : " -- but the 60 fps patch is off, nothing changes");
}
void runtime_frame_step_update(uint64_t now_ns) {
    static uint64_t last; static double samples[4], sum_all; static unsigned count, next, frames, clamped;
    if (!fps60_page || !fps60_on || !now_ns) return;
    if (atomic_load_explicit(&pinned_step_us, memory_order_relaxed)) { last = now_ns; return; }
    if (!last) { last = now_ns; return; }
    double dt = (double)(now_ns - last) * 1e-9; last = now_ns;
    if (dt > 0.25) { count = next = 0; return; }
    if (dt < 1.0 / 60.0) dt = 1.0 / 60.0;
    if (dt > 1.0 / 20.0) { dt = 1.0 / 20.0; clamped++; }
    samples[next++ % 4] = dt; if (count < 4) count++;
    double sum = 0; for (unsigned i = 0; i < count; i++) sum += samples[i];
    frame_step_store((float)(sum / count));
    sum_all += dt; frames++;
    if (frames == 1800) {   /* about every 30 s at 60 fps */
        fprintf(stderr, "[xcx] 60 fps frame step: mean %.2f ms over %u frames (%.1f fps), %u held at 50 ms\n", sum_all / frames * 1e3, frames, frames / sum_all, clamped);
        frames = clamped = 0; sum_all = 0;
    }
}
int runtime_output_res(unsigned *w, unsigned *h) { (void)w; (void)h; return 0; }
uint64_t runtime_output_res_extra_bytes(void) { return 0; }
float runtime_aspect_now(void) { return 0; }
