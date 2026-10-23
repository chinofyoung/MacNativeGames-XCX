#!/usr/bin/env python3
"""The original project's Mac reminder for iPhone / iPad shader contributions, stubbed for Xenoblade Chronicles X.

In the original project (games/totk/ios/tools/shader_contrib_watch.py) a LaunchAgent copies each connected device's
Documents/shader-list-contribution.txt to the Mac and asks the player to send it in a GitHub issue, so the one shared
shader list grows and every later build carries those shaders. This port has no shared shader list and no issue form for
one yet, so there is nothing to send and this tool installs nothing; build_app.py --platform ios does not run it.

What to do instead with the shaders a device misses: `fill_shaders.py --work <work folder> --device <id>` translates them on
the Mac and pushes them back; the next `build_app.py --platform ios` also carries them in the app. The device still writes
its contribution file (Files app › On My iPhone › Xenoblade Chronicles X), which can be read by hand.

usage: shader_contrib_watch.py check | install | uninstall   (all three only say the above)
"""
import sys

MESSAGE = ('shader_contrib_watch.py: Xenoblade Chronicles X has no shared shader list yet, so there is nothing to remind '
           'about and nothing was installed. Use fill_shaders.py --work <work folder> --device <id> to translate the '
           'shaders a device missed.')

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in ('check', 'install', 'uninstall'):
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        sys.exit(2)
    print(MESSAGE)
