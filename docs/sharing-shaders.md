# Sharing shaders

Help every player's copy of Xenoblade Chronicles X draw the whole picture.

## 1. What this is

A game draws its picture with many small programs called shaders. A Mac makes the ones it needs while you play. An
iPhone or iPad cannot do that, so it can only use the shaders that were prepared when the app was built. If one is
missing, the game skips that part of the picture, and you may see a character without their clothes.

Nobody has played every corner of the game, so nobody's build has every shader. That is why there is a shared list. When
you play, the game notes the shaders it met that the list does not have yet. If you share them, they join the list, and
everyone's next build prepares them in advance. The more people share, the fewer gaps there are.

## 2. What you share and what you don't

You share a short text file. Each line is a fingerprint of a shader and how it is set up. That is all.

- No game files, no pieces of the game, no saves and no personal information.
- Nobody can rebuild the game from the list. It only says which shaders exist.
- Each player's build makes the real shaders from their own copy of the game. Nothing from the game is ever uploaded or
  downloaded.

You can open the file in any text editor before you send it.

## 3. Your first build

Nothing to set up. When you build the app, the build downloads the newest shared list. If you are offline, it uses the
last list it saved, or the one that comes with the project (`python3 build_app.py ... --offline` skips the download).
Then it finds those shaders in your own game files and prepares them, for the Mac and for the iPhone. The window and the
log show this as the step "shaders: prepare".

What to expect:

- **Mac:** the first preparation took about a minute on an M1 for about 12,400 shaders. A rebuild with nothing new takes
  about 2 seconds.
- **iPhone and iPad:** the first iPhone build after this also prepares the shaders for the phone. That took about 28
  minutes once. Later builds reuse that work. The app grew from 86 MB to 159 MB.
- **A few parts may still be missing at first.** The list only has what players have found so far. On the Mac, anything
  missing is made while you play. On the iPhone it is skipped until it is on the list. See "Fixing missing parts now"
  below.

## 4. How to share

You need a free GitHub account (see the questions at the end). Sharing is one file and one form.

### On a Mac

1. Play. The game saves the new shaders it meets in
   `~/Library/Application Support/XCNative/XCX/shader-list-contribution.txt`.
2. When the file has 50 new shaders, the game asks before it starts: "Share", "Not now" or "Never". After that it asks
   again every 500 more. "Not now" asks again after 500 more new shaders. "Never" stops the questions (the game still
   notes them).
3. Choose "Share". Your browser opens the GitHub form and Finder shows the file.
4. Drag `shader-list-contribution.txt` into the form's file box, pick the game version and "Mac", and press "Submit new
   issue".

The builder window also shows a "Share N new shaders" button after a build. It does the same as "Share".

To do it by hand, open this page, fill in the form, and attach the file from the folder above:
https://github.com/chinofyoung/XC-Native/issues/new?template=shader-list.yml

### On an iPhone or iPad

The phone keeps its own file. Open the Files app, then On My iPhone, then Xenoblade Chronicles X, and find
`shader-list-contribution.txt`. To share it by hand:

1. Send the file to your Mac (AirDrop works, or connect with a cable and use Finder's file sharing).
2. Open the form linked above, drag the file in, pick the game version and "iPhone" (or "iPad"), and submit.

If you build the iOS app from a Mac with the phone connected, the Mac can fetch the file for you. Set `XCX_IOS_DEVICE`
to the phone's id before the build:

```sh
xcrun devicectl list devices
XCX_IOS_DEVICE=<id> python3 build_app.py "<game file>" -k <prod.keys> --platform ios
```

The build then copies the phone's file to the Mac and merges it into the Mac's file, so the "Share" button or reminder
counts those shaders too. (If your app has a team name in its bundle id, also set `XCX_BUNDLE_ID` to it, see
section 6.) If the phone is not there, the build only prints a warning and goes on.

## 5. What happens after you share

1. Within a few minutes an automatic check reads your file. It accepts only lines of the expected shape, so it cannot
   add anything else to the list.
2. If it is fine, the new shaders are added to the shared list, a comment thanks you, and the issue is closed.
3. Your next build, and everyone else's, downloads the list and prepares those shaders too.

You do not need to do anything else, and nobody needs to approve it by hand.

## 6. Fixing missing parts right now (iPhone and iPad)

If something is missing on the phone and you do not want to wait for the list, you can send the missing shaders to the
phone yourself from your Mac. Connect the phone, unlock it, and run this from the project folder:

```sh
python3 games/xcx/ios/tools/fill_shaders.py --work XCX_build --device <id> --bundle-id <bundle id>
```

- `--work` is the work folder of your build (`XCX_build` in the project's top folder unless you picked another).
- `<id>` is the phone's id from `xcrun devicectl list devices`.
- `<bundle id>` is the app's name on the phone. An app you installed with your own Apple ID has your team in it, like
  `org.xcnative.xcx.<team>`. The install step prints it. Without the right one the tool cannot find the app.

Restart the game afterwards. It picks up the new shaders when it starts. Later builds include them as well. If you have
not installed Apple's Metal Toolchain, the tool still works, and the phone prepares the shaders a little later. You can
install the toolchain with `xcodebuild -downloadComponent MetalToolchain`.

## 7. Questions

**Is this legal and safe?** The list holds identifiers only, never game files (see section 2). You still need your own
copy of the game, and every build is made from it. Look at the file before sending it if you like.

**Do I need a GitHub account?** Yes, a free one. It is how you attach the file. The account must be at least a week old.

**Why was my share not added?** The automatic check leaves a comment that says why, and usually closes the issue. The
rules are:

- The account is less than 7 days old. This keeps spam out. Send it again when the account is a week old.
- You already shared today. One share per account per day (a day ends at midnight UTC, which may not match your local
  time). This issue stays open. Edit it tomorrow and it will be added.
- It has more than 2,000 new shaders. Split it into smaller parts over several days.
- The issue was already added on an earlier day. An issue can only add shaders on the day it was first added. Open a new
  issue to share more.

If many people share at once, you may be asked to edit the issue (any small change) to try again.

Two other comments are not mistakes. "Nothing new" means every shader in your file was already on the list. "No shaders
were found" means the file did not come with the issue. Drag the file into the form.

**Does it work for both game versions?** Yes, 1.0.1 and 2.0.0. The list holds shaders of both, and a build uses only the
ones that belong to its version. Pick the version you played in the form.

**What if I don't want to share?** That is fine. Everything works without it. Choose "Never" on the Mac, or ignore the
file. You still get everything others have shared.

More about the project: https://github.com/chinofyoung/XC-Native
