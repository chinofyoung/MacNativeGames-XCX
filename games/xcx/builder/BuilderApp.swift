// The window that builds Xenoblade Chronicles X for the Mac (or the experimental iPhone / iPad IPA) from the player's
// own files. It is a front end for build_app.py: `python3 build_app.py --gui` compiles it (xcrun swiftc, no Xcode
// project) and opens it with --script <build_app.py> --python <the Python that ran it>.
import AppKit
import SwiftUI

@main
struct BuilderApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = BuildModel.shared

    var body: some Scene {
        Window("Xenoblade Chronicles X Builder", id: "main") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 620, idealWidth: 680, minHeight: 640, idealHeight: 860)
        }
        .defaultSize(width: 700, height: 900)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.activate()
        MainActor.assumeIsolated { TestHook.start(BuildModel.shared) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        MainActor.assumeIsolated {
            let model = BuildModel.shared
            guard model.isRunning else { return .terminateNow }
            let alert = NSAlert()
            alert.messageText = "A build is running"
            alert.informativeText = "Quitting cancels it. The work folder keeps every finished step, so building again "
                + "later continues where this build stopped."
            alert.addButton(withTitle: "Cancel Build and Quit")
            alert.addButton(withTitle: "Keep Building")
            guard alert.runModal() == .alertFirstButtonReturn else { return .terminateCancel }
            model.stopForQuit()
            return .terminateNow
        }
    }
}

/// --script and --python from the command line (build_app.py --gui), else the ones the last launch was given.
struct Launch {
    let script: String?
    let python: String?

    static func fromCommandLine() -> Launch {
        let args = CommandLine.arguments
        func value(_ name: String) -> String? {
            guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
            return args[i + 1]
        }
        let defaults = UserDefaults.standard
        if let script = value("--script"), let python = value("--python") {
            defaults.set(script, forKey: "launch.script")
            defaults.set(python, forKey: "launch.python")
            return Launch(script: script, python: python)
        }
        let script = defaults.string(forKey: "launch.script")
        let python = defaults.string(forKey: "launch.python")
        if let script, let python, FileManager.default.fileExists(atPath: script),
           FileManager.default.isExecutableFile(atPath: python) {
            return Launch(script: script, python: python)
        }
        return Launch(script: nil, python: nil)
    }
}
