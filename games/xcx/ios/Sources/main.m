/* The iPhone / iPad app of the Xenoblade Chronicles X port: restored from the original project's games/totk/ios/Sources/
   main.m and adapted for XCX (names, the game data layout of games/xcx, no shared shader list). UIKit owns the window; the
   game runs on its own thread through the loader's main (runtime/loader.c, built with -Dmain=totk_host_main) and draws
   into the view's Metal layer (runtime/frontend_ios.m). EXPERIMENTAL: an iPhone or iPad cannot translate shaders at run
   time, and this port has no up-front shader translation yet, so a draw whose shader the app does not carry is skipped. */
#import <UIKit/UIKit.h>
#import <AVFAudio/AVFAudio.h>
#import <QuartzCore/CAMetalLayer.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <math.h>
#include "runtime.h"
#include "dmsh_chain.h"
#include "romfs_config.h"   // ROMFS_IMAGE_SIZE: the build's image (generated, <work>/build/<v>)
#include "game_version.h"   // TOTK_GAME_DIR (XCX_VERSION_FLAGS: -DXCX_GAME_V101 / V200)
#import "touch_pad.h"

int totk_host_main(int argc, char **argv);
void runtime_ios_attach_layer(CAMetalLayer *layer, UIView *host);
/* The original project's runtime restarts its audio output after an interruption; this runtime has no such call yet
   (runtime/audio_mixer.c). This weak stand-in only says so; a runtime that defines it takes its place at link time. */
__attribute__((weak)) void runtime_audio_output_restart(int recreate) {
    fprintf(stderr,"[xcx-audio] this runtime cannot restart its audio output (recreate=%d); sound may stay off until the game is opened again\n",recreate);
}
static char *host_argv[3];
static NSString *g_no_game_data;   // set when there is no RomFS image to play from (didFinishLaunching)
static void *run_host(void *unused) { (void)unused; return (void *)(intptr_t)totk_host_main(3, host_argv); }
/* The desktop keyboard-overlay shortcut has no counterpart on iOS. */
void runtime_shortcut_help_toggle(void) {}

@interface XCXMetalView : UIView @end
@implementation XCXMetalView
+ (Class)layerClass { return CAMetalLayer.class; }
@end

@interface XCXGameController : UIViewController @property(nonatomic,strong) XCXMetalView *gameView; @end
@implementation XCXGameController
- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.blackColor;
    self.gameView = [[XCXMetalView alloc] initWithFrame:self.view.bounds];
    self.gameView.autoresizingMask = UIViewAutoresizingFlexibleWidth|UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.gameView];
    runtime_ios_attach_layer((CAMetalLayer *)self.gameView.layer, self.gameView);
    XCXTouchPad *pad=[[XCXTouchPad alloc] initWithFrame:self.view.bounds];pad.autoresizingMask=UIViewAutoresizingFlexibleWidth|UIViewAutoresizingFlexibleHeight;[self.view addSubview:pad];
}
- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    static BOOL started; if (started) return; started = YES;
    if (g_no_game_data) {
        UILabel *say = [[UILabel alloc] initWithFrame:CGRectInset(self.view.bounds, 40, 40)];
        say.autoresizingMask = UIViewAutoresizingFlexibleWidth|UIViewAutoresizingFlexibleHeight;
        say.text = g_no_game_data; say.textColor = UIColor.whiteColor; say.numberOfLines = 0; say.textAlignment = NSTextAlignmentCenter;
        [self.view addSubview:say];
        return;
    }
    /* Playback category: plays with the ring / silent switch off and routes to the speaker. Without an active session the
       game's CoreAudio output stays on a null sink; activating it in didFinishLaunching blocked the main thread before the
       log file was opened (the original project's findings). Interruptions: audio_observe. */
    AVAudioSession *audio = AVAudioSession.sharedInstance;
    NSError *catErr = nil, *actErr = nil;
    [audio setCategory:AVAudioSessionCategoryPlayback mode:AVAudioSessionModeDefault options:0 error:&catErr];
    BOOL activated = [audio setActive:YES error:&actErr];
    fprintf(stderr, "[xcx-audio] audio session category=%s active=%d route=%s err=%s%s\n",
            audio.category.UTF8String ?: "?", (int)activated, audio.currentRoute.outputs.firstObject.portType.UTF8String ?: "?",
            catErr ? catErr.localizedDescription.UTF8String : "none", actErr ? actErr.localizedDescription.UTF8String : "");
    pthread_attr_t attr; pthread_attr_init(&attr); pthread_attr_setstacksize(&attr, 64u << 20);
    pthread_t thread; pthread_create(&thread, &attr, run_host, NULL);
    pthread_detach(thread); pthread_attr_destroy(&attr);
}
- (BOOL)prefersHomeIndicatorAutoHidden { return YES; }
- (BOOL)prefersStatusBarHidden { return YES; }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations { return UIInterfaceOrientationMaskLandscape; }
@end

/* iOS 27 ends apps linked against its SDK that keep the window in the app delegate, so the window belongs to a scene. */
@interface XCXSceneDelegate : UIResponder <UIWindowSceneDelegate> @property(nonatomic,strong) UIWindow *window; @end
@implementation XCXSceneDelegate
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
    (void)session; (void)options;
    if (![scene isKindOfClass:UIWindowScene.class]) return;
    self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
    self.window.rootViewController = [XCXGameController new]; [self.window makeKeyAndVisible];
}
@end

/* Game audio after interruptions: a call, Siri or an alarm takes the audio session and the system stops the game's output
   unit. When an interruption ends the session is made active again (and the output restarted when the runtime can);
   becoming active does the same, for an interruption whose "ended" never came, and turns the idle timer off again. */
static BOOL g_audio_interrupted;
static void audio_resume(int recreate, const char *why) {
    NSError *err = nil;
    if (![AVAudioSession.sharedInstance setActive:YES error:&err]) fprintf(stderr,"[xcx-audio] audio session not active again (%s): %s\n",why,err.localizedDescription.UTF8String);
    g_audio_interrupted = NO;
    runtime_audio_output_restart(recreate);
}
static void audio_observe(void) {
    NSNotificationCenter *nc = NSNotificationCenter.defaultCenter; NSOperationQueue *main = NSOperationQueue.mainQueue;
    [nc addObserverForName:AVAudioSessionInterruptionNotification object:nil queue:main usingBlock:^(NSNotification *n) {
        NSUInteger type = [n.userInfo[AVAudioSessionInterruptionTypeKey] unsignedIntegerValue];
        fprintf(stderr,"[xcx-audio] audio session interruption %s\n",type == AVAudioSessionInterruptionTypeBegan ? "began" : "ended");
        if (type == AVAudioSessionInterruptionTypeBegan) g_audio_interrupted = YES; else audio_resume(0,"interruption ended");
    }];
    [nc addObserverForName:AVAudioSessionMediaServicesWereResetNotification object:nil queue:main usingBlock:^(NSNotification *n) {
        (void)n; fprintf(stderr,"[xcx-audio] media services were reset\n"); audio_resume(1,"media services reset");
    }];
    [nc addObserverForName:UIApplicationDidBecomeActiveNotification object:nil queue:main usingBlock:^(NSNotification *n) {
        (void)n; UIApplication.sharedApplication.idleTimerDisabled = YES;
        if (g_audio_interrupted) audio_resume(0,"became active during an interruption");
    }];
}

@interface XCXApp : UIResponder <UIApplicationDelegate> @end
@implementation XCXApp
- (UISceneConfiguration *)application:(UIApplication *)application configurationForConnectingSceneSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
    (void)application; (void)options;
    UISceneConfiguration *c = [[UISceneConfiguration alloc] initWithName:@"Default" sessionRole:session.role];
    c.delegateClass = XCXSceneDelegate.class; return c;
}
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    (void)options;
    // Controller input does not reset the idle timer; auto-lock would suspend the game mid-play.
    application.idleTimerDisabled = YES;
    audio_observe();
    NSString *preferred = NSLocale.preferredLanguages.firstObject;   // as the Mac launcher (app/launcher.m)
    if (preferred.length) setenv("TOTK_LOCALE", preferred.UTF8String, 1);
    NSFileManager *fm = NSFileManager.defaultManager;
    NSString *docs = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory,NSUserDomainMask,YES).firstObject;
    NSString *res = [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"GameData"];
    for (NSString *name in @[@"save", @"temp", @"shader-cache", @"game-slot", @"GameData"]) [fm createDirectoryAtPath:[docs stringByAppendingPathComponent:name] withIntermediateDirectories:YES attributes:nil error:nil];
    NSString *log = [docs stringByAppendingPathComponent:@"log.txt"];
    if (freopen(log.fileSystemRepresentation,"w",stderr)) setvbuf(stderr,NULL,_IOLBF,0);
    /* The app carries the RomFS index (GameData/romfs/runtime-index.bin, bound to the build by its SHA-256); the 14.3 GB
       image is installed once into Documents/GameData (romfs.bin, or romfs.bin.000, .001, ... in parts), by
       ios/tools/install_device.py --game-data or by copying it there in Finder / the Files app. Reinstalling the app keeps
       it. This runtime reads the index and the image from one folder (runtime/romfs.c TOTK_ROMFS), so the app's index is
       copied next to the image (a few kilobytes) when it differs. Excluded from iCloud backups. The runtime hashes the
       image once against the build's pin and keeps a record (TOTK_ROMFS_PROOF_DIR). */
    NSString *gameData = [docs stringByAppendingPathComponent:@"GameData"];
    /* Finder's file sharing (the device › Files) copies into the top of Documents: a romfs.bin put there is moved into
       GameData (a rename on the same volume, instant). */
    unsigned long long (^sizeOf)(NSString *) = ^unsigned long long (NSString *path) { return [[fm attributesOfItemAtPath:path error:nil] fileSize]; };
    NSString *dropped = [docs stringByAppendingPathComponent:@"romfs.bin"], *placed = [gameData stringByAppendingPathComponent:@"romfs.bin"];
    BOOL partial = NO;   // a romfs.bin still being copied (shorter than the build's image)
    if ([fm fileExistsAtPath:dropped] && ![fm fileExistsAtPath:placed]) {
        if (sizeOf(dropped) != ROMFS_IMAGE_SIZE) partial = YES;
        else { NSError *moveErr = nil;
               if (![fm moveItemAtPath:dropped toPath:placed error:&moveErr]) fprintf(stderr,"[xcx] could not move romfs.bin into GameData: %s\n",moveErr.localizedDescription.UTF8String); }
    }
    if ([fm fileExistsAtPath:placed] && sizeOf(placed) != ROMFS_IMAGE_SIZE) partial = YES;
    BOOL image = [fm fileExistsAtPath:placed] ||
                 [fm fileExistsAtPath:[gameData stringByAppendingPathComponent:@"romfs.bin.000"]];
    NSString *mark = [NSString stringWithContentsOfFile:[gameData stringByAppendingPathComponent:@"romfs.sha256"] encoding:NSUTF8StringEncoding error:nil];
    BOOL copying = partial || (mark && [mark stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length != 64);   // install_device.py writes the hash last
    if (image && !copying) {
        [[NSURL fileURLWithPath:gameData isDirectory:YES] setResourceValue:@YES forKey:NSURLIsExcludedFromBackupKey error:nil];
        NSString *from = [res stringByAppendingPathComponent:@"romfs/runtime-index.bin"], *to = [gameData stringByAppendingPathComponent:@"runtime-index.bin"];
        NSData *want = [NSData dataWithContentsOfFile:from];
        if (want && ![[NSData dataWithContentsOfFile:to] isEqualToData:want] && ![want writeToFile:to atomically:YES])
            fprintf(stderr, "[xcx] could not copy the RomFS index to %s\n", to.UTF8String);
        setenv("TOTK_ROMFS",gameData.fileSystemRepresentation,1);
    } else {
        /* No image to play from: said on the screen instead of the game ending at once (runtime_fail exits). */
        NSString *app = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleDisplayName"] ?: @"Xenoblade Chronicles X";
        g_no_game_data = copying || image
            ? [NSString stringWithFormat:@"The game data (romfs.bin, %.1f GB) is still being copied to Files › On My iPhone › %@. Open the game again when it is done.", ROMFS_IMAGE_SIZE / 1e9, app]
            : [NSString stringWithFormat:@"No game data. Copy romfs.bin from the Mac's work folder (extracted/" TOTK_GAME_DIR "/romfs/romfs.bin) to "
                                          "this app: with games/xcx/ios/tools/install_device.py --game-data, or in Finder (the device › Files: "
                                          "drag romfs.bin onto %@), then open the game again.", app];
        fprintf(stderr, "[xcx] no game image: %s\n", g_no_game_data.UTF8String);
    }
    setenv("TOTK_OPERATION_MODE","handheld",1);
    // Game language: the Settings app (Settings.bundle › TOTK_LANG) stores the choice in our defaults; empty means the
    // system language. iOS does not honour Info.plist LSEnvironment, so the value is applied here; overwrite=0 keeps
    // devicectl --environment-variables winning when testing from the Mac.
    NSString *lang = [NSUserDefaults.standardUserDefaults stringForKey:@"TOTK_LANG"];
    if (lang.length>0&&lang.length<8) setenv("TOTK_LANG",lang.UTF8String,0);
    setenv("TOTK_ROMFS_PATCHES","0",1);   // XCX has no build-made RomFS overlays (runtime/romfs.c)
    setenv("TOTK_ROMFS_MODS","0",1);
    setenv("TOTK_SHARED_FONTS",[res stringByAppendingPathComponent:@"shared-fonts"].fileSystemRepresentation,1);
    setenv("TOTK_SHADER_SEED_DIR",[res stringByAppendingPathComponent:@"shader-seed"].fileSystemRepresentation,1);
    /* One folder per translation chain, as on the Mac (app/launcher.m): the runtime refuses translations of another
       chain; other chains' folders are only cache and are removed in the background. */
    NSString *cacheRoot = [docs stringByAppendingPathComponent:@"shader-cache"], *chainDir = @"rt-" DMSH_CHAIN;
    NSString *cache = [cacheRoot stringByAppendingPathComponent:chainDir];
    const char *testCache = getenv("TOTK_SHADER_CACHE");   // test launches may name another folder in Documents
    if (testCache && *testCache && !strstr(testCache, "..")) cache = [docs stringByAppendingPathComponent:@(testCache)];
    [fm createDirectoryAtPath:cache withIntermediateDirectories:YES attributes:nil error:nil];
    setenv("TOTK_SHADER_CACHE",cache.fileSystemRepresentation,1);
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{
        NSFileManager *files = [NSFileManager new]; NSUInteger removed = 0;
        for (NSString *name in [files contentsOfDirectoryAtPath:cacheRoot error:nil] ?: @[]) {
            if ([name isEqualToString:chainDir]) continue;
            if ([files removeItemAtPath:[cacheRoot stringByAppendingPathComponent:name] error:nil]) removed++;
        }
        if (removed) fprintf(stderr,"[shader-cache] removed %lu entries of earlier translation chains\n",(unsigned long)removed);
    });
    // The variants the device missed (totk_shader_contrib.h), in the Files app; nothing is sent anywhere.
    setenv("TOTK_SHADER_CONTRIB",[docs stringByAppendingPathComponent:@"shader-list-contribution.txt"].fileSystemRepresentation,1);
    setenv("DM_NVN_SHMAP",[res stringByAppendingPathComponent:@"shader-seed/shader_map.tsv"].fileSystemRepresentation,1);
    setenv("DM_MSL_DIR",[res stringByAppendingPathComponent:@"shader-seed"].fileSystemRepresentation,1);
    // Same player environment as the Mac launcher (app/launcher.m); the bundle is read-only, so state goes to Documents.
    setenv("TOTK_PROFILE_DIR",[docs stringByAppendingPathComponent:@"save"].fileSystemRepresentation,1);
    setenv("TOTK_GAME_SLOT_DIR",[docs stringByAppendingPathComponent:@"game-slot"].fileSystemRepresentation,1);
    setenv("TOTK_ROMFS_PROOF_DIR",docs.fileSystemRepresentation,1);   // hash the 14 GB RomFS once, not on every launch
    for (NSString *name in @[@"TOTK_GPU_ALLOW_SUBMIT",@"TOTK_GPU_WIDEN",@"TOTK_GPU_BLOCKRT",@"TOTK_GPU_MIDCLEAR"]) setenv(name.UTF8String,"1",1);
    setenv("DM_PRESENT_LINK_FALLBACK","1",1);
    setenv("TOTK_SLOT_OWNER","user",0); setenv("TOTK_SLOT_PURPOSE","player",0);
    unsetenv("TOTK_AUTOLOAD"); unsetenv("TOTK_HEADLESS"); unsetenv("XCX_PATCHES");   // code patches are fixed at build time on iOS
    chdir(docs.fileSystemRepresentation);
    host_argv[0] = strdup("XCX"); host_argv[1] = strdup(res.fileSystemRepresentation); host_argv[2] = strdup("boot");
    return YES;   // the window is made when the window scene connects (XCXSceneDelegate)
}
@end

int main(int argc, char **argv) { @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass(XCXApp.class)); } }
