// TOTK: what a player's game adds to the shared shader list (docs/sharing-shaders.md).
// The prepared seed holds every variant the list names, so a variant the Mac has to translate while playing, or that an
// iPhone / iPad misses, is one the list does not have yet. Such a variant is written once to the player's contribution
// file (TOTK_SHADER_CONTRIB, set by app/launcher.m and ios/Sources/main.m; unset in development runs, which then write
// nothing) as a shader-list V row: the shader key and the bind state, no game data. The Mac launcher asks the player,
// before the game starts, to send the file in a GitHub issue: first at TOTK_CONTRIB_FIRST rows, then every
// TOTK_CONTRIB_STEP more (an iPhone / iPad never asks during play). After a build the builder window offers the same
// (scripts/contribution.py). Header-only: the runtime and the Mac launcher share it.
#pragma once
#import <Foundation/Foundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef TOTK_CONTRIB_FIRST
#define TOTK_CONTRIB_FIRST 50   // rows before the first reminder
#endif
#ifndef TOTK_CONTRIB_STEP
#define TOTK_CONTRIB_STEP 500   // more rows before each next one
#endif
#ifndef GAME_CONTRIB_TEMPLATE   /* the issue form, label and title prefix of this game's list (runtime/game_version.h sets Breath of the Wild's) */
#define GAME_CONTRIB_TEMPLATE "shader-list.yml"
#define GAME_CONTRIB_TITLE "Shader list:"   /* the title prefix of the issue the reminder opens on the form; the shader-list workflow (process_shader_issue.py) takes issues titled so */
#endif
#define TOTK_CONTRIB_REPO "chinofyoung/XC-Native"   // where the shared list is kept (games/xcx/assets/shader_list.tsv)
#define TOTK_CONTRIB_ISSUE_URL "https://github.com/" TOTK_CONTRIB_REPO "/issues/new?template=" GAME_CONTRIB_TEMPLATE
/* The reminder (app/launcher.m); %lu is the row count. */
#define TOTK_CONTRIB_PROMPT "You found %lu new shaders. Sharing them lets everyone's next build draw these parts of the game. Share now?"
#define TOTK_CONTRIB_SHARE "Share"
#define TOTK_CONTRIB_NOT_NOW "Not now"
#define TOTK_CONTRIB_NEVER "Never"

static inline NSString *totk_contrib_path(void) {
    const char *e = getenv("TOTK_SHADER_CONTRIB");
    return e && *e ? @(e) : nil;
}

/* The rows already in the file: "<shader>\t<deps>", one per shader and bind state. */
static inline NSMutableSet<NSString *> *totk_contrib_read(NSString *path) {
    NSMutableSet *rows = [NSMutableSet set];
    NSString *text = path ? [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil] : nil;
    for (NSString *line in [text componentsSeparatedByString:@"\n"])
        if ([line hasPrefix:@"V\t"]) [rows addObject:[line substringFromIndex:2]];
    return rows;
}

/* The rows before a reminder: TOTK_CONTRIB_FIRST for the first one, TOTK_CONTRIB_STEP more for each next one.
   TOTK_CONTRIB_THRESHOLD=<n> from the environment sets both, for checks of the reminder (1: at the next start). */
static inline NSUInteger totk_contrib_threshold(BOOL first) {
    const char *e = getenv("TOTK_CONTRIB_THRESHOLD");
    unsigned long long n = e && *e ? strtoull(e, NULL, 10) : 0;
    return n ? (NSUInteger)n : first ? TOTK_CONTRIB_FIRST : TOTK_CONTRIB_STEP;
}
/* The reminder is due when the file holds TOTK_CONTRIB_FIRST rows and no reminder was shown yet, or TOTK_CONTRIB_STEP
   rows more than at the last one (<file>.reminded), unless the player chose "Never" ("never" there). Returns the row
   count then, else 0. "Not now" is totk_contrib_reminded: the next reminder comes TOTK_CONTRIB_STEP rows later. */
static inline NSUInteger totk_contrib_due(NSString *path) {
    if (!path) return 0;
    NSString *last = [NSString stringWithContentsOfFile:[path stringByAppendingString:@".reminded"] encoding:NSUTF8StringEncoding error:nil];
    if ([last hasPrefix:@"never"]) return 0;
    NSUInteger rows = totk_contrib_read(path).count;
    NSUInteger at = last ? (NSUInteger)strtoull(last.UTF8String, NULL, 10) : 0;
    return rows >= at + totk_contrib_threshold(last == nil) ? rows : 0;
}
/* The shader-list issue form filled in with the title, the game version (2.0.0 or 1.0.1) and the device, as
   scripts/contribution.py issue_url() makes it. */
static inline NSString *totk_contrib_query(NSString *text) {   // percent-encoded but for A-Z a-z 0-9 - . _ ~
    NSCharacterSet *plain = [NSCharacterSet characterSetWithCharactersInString:
                             @"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"];
    return [text stringByAddingPercentEncodingWithAllowedCharacters:plain] ?: @"";
}
static inline NSURL *totk_contrib_issue_url(NSUInteger rows, NSString *version, NSString *device) {
    NSString *title = [NSString stringWithFormat:@"Shader list: %lu shaders", (unsigned long)rows];
    return [NSURL URLWithString:[NSString stringWithFormat:@TOTK_CONTRIB_ISSUE_URL "&title=%@&version=%@&device=%@",
                                 totk_contrib_query(title), totk_contrib_query(version), totk_contrib_query(device)]];
}
static inline void totk_contrib_reminded(NSString *path, NSUInteger rows) {
    [[NSString stringWithFormat:@"%lu\n", (unsigned long)rows] writeToFile:[path stringByAppendingString:@".reminded"]
                                                                 atomically:YES encoding:NSUTF8StringEncoding error:nil];
}
/* "Never": no more reminders for this file. The game still notes the shaders. */
static inline void totk_contrib_never(NSString *path) {
    [@"never\n" writeToFile:[path stringByAppendingString:@".reminded"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
}

/* Compact JSON with keys in code-point order, as scripts/shader_list.py canonical() writes it (Python's json.dumps with
   sort_keys; NSJSONWritingSortedKeys would put "9" before "10"). Deps hold only dictionaries and strings. */
static inline void totk_contrib_json(NSMutableString *out, id value) {
    if ([value isKindOfClass:[NSDictionary class]]) {
        NSArray *keys = [[value allKeys] sortedArrayUsingComparator:^NSComparisonResult(NSString *a, NSString *b) { return [a compare:b options:NSLiteralSearch]; }];
        [out appendString:@"{"];
        for (NSUInteger i = 0; i < keys.count; i++) {
            if (i) [out appendString:@","];
            totk_contrib_json(out, keys[i]); [out appendString:@":"]; totk_contrib_json(out, value[keys[i]]);
        }
        [out appendString:@"}"];
        return;
    }
    NSString *text = [value description];
    [out appendString:@"\""];
    for (NSUInteger i = 0; i < text.length; i++) {
        unichar c = [text characterAtIndex:i];
        if (c == '"' || c == '\\') [out appendFormat:@"\\%C", c];
        else if (c < 0x20 || c > 0x7f) [out appendFormat:@"\\u%04x", c];
        else [out appendFormat:@"%C", c];
    }
    [out appendString:@"\""];
}

/* Adds the shader with these bind-state answers or translator deps (only tex / img / attr / fout are kept), once.
   Not thread-safe: the runtime calls it under its translation lock. */
static inline void totk_contrib_add(NSString *shader, NSDictionary *deps) {
    static NSString *path; static NSMutableSet<NSString *> *rows; static int off;
    if (off) return;
    if (!rows) {
        path = totk_contrib_path();
        if (!path) { off = 1; return; }
        rows = totk_contrib_read(path);
    }
    NSMutableDictionary *kept = [NSMutableDictionary dictionary];
    for (NSString *kind in @[@"tex", @"img", @"attr", @"fout"])
        if ([deps[kind] isKindOfClass:[NSDictionary class]]) kept[kind] = deps[kind];
    NSMutableString *row = [NSMutableString stringWithFormat:@"%@\t", shader];
    totk_contrib_json(row, kept);
    if ([rows containsObject:row]) return;
    FILE *f = fopen(path.fileSystemRepresentation, "a");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    if (ftell(f) == 0)
        fputs("# Shaders this game met that the shared shader list does not have yet (docs/sharing-shaders.md).\n"
              "# Only shader keys and bind states: no game data, saves or personal information.\n"
              "# Send it in a GitHub issue: " TOTK_CONTRIB_ISSUE_URL "\n", f);
    fprintf(f, "V\t%s\n", row.UTF8String);
    fclose(f);
    [rows addObject:row];
}
