// A hidden hook for checking the window by script (UI scripting needs an accessibility permission a test run may not
// have). Used only when XCX_BUILDER_TEST_GAME or _COMMANDS is set (build_app.py --gui passes XCX_BUILDER_TEST_* on):
//   XCX_BUILDER_TEST_GAME, _UPDATE, _FIRMWARE, _KEYS, _TITLE_KEYS, _OUT, _WORK  the fields (paths)
//   XCX_BUILDER_TEST_PLATFORM      macos | ios
//   XCX_BUILDER_TEST_NO_TEAM=1     the install section behaves as if no Apple Development certificate existed
//   XCX_BUILDER_TEST_DELETE_WORK=1 turn on "Delete the work folder when the build finishes" for this run only
//   XCX_BUILDER_TEST_BUILD=1       accept the notice for this run only and start the build once the check passes
//   XCX_BUILDER_TEST_CANCEL_AT=<step name>  press Cancel when that step starts
//   XCX_BUILDER_TEST_STATUS=<file> write the window's state as JSON (window number, phase, steps) on every change
//   XCX_BUILDER_TEST_COMMANDS=<file>  lines appended to it are run once each: "shot <path.png>" (the key window draws
//                                  itself into a PNG, no screen recording permission needed), "log" (open the log),
//                                  "notice" (show the notice sheet), "update" (show the update / save warning sheet),
//                                  "size <w> <h>", "build", "cancel", "quit", "scroll <id>" (build | result |
//                                  install), "devices" (look for devices again)
// With _GAME (test mode) nothing is written to UserDefaults, so the remembered paths and notice stay as they were.
import AppKit
import Foundation

@MainActor
enum TestHook {
    static func start(_ model: BuildModel) {
        let env = ProcessInfo.processInfo.environment
        if let commands = env["XCX_BUILDER_TEST_COMMANDS"] { watch(commands, model) }
        guard let game = env["XCX_BUILDER_TEST_GAME"] else { return }
        model.persist = false
        InstallModel.shared.simulateNoTeam = env["XCX_BUILDER_TEST_NO_TEAM"] == "1"
        model.legalAccepted = env["XCX_BUILDER_TEST_BUILD"] == "1"
        for field in Field.allCases { model.set(field, nil) }
        if let raw = env["XCX_BUILDER_TEST_PLATFORM"], let p = Platform(rawValue: raw) { model.platform = p }
        model.setFromEnvironment(.game, game)
        model.setFromEnvironment(.update, env["XCX_BUILDER_TEST_UPDATE"])
        model.setFromEnvironment(.firmware, env["XCX_BUILDER_TEST_FIRMWARE"])
        model.setFromEnvironment(.keys, env["XCX_BUILDER_TEST_KEYS"])
        model.setFromEnvironment(.titleKeys, env["XCX_BUILDER_TEST_TITLE_KEYS"])
        model.setFromEnvironment(.output, env["XCX_BUILDER_TEST_OUT"])
        model.setFromEnvironment(.work, env["XCX_BUILDER_TEST_WORK"])
        model.deleteWork = env["XCX_BUILDER_TEST_DELETE_WORK"] == "1"
        let statusPath = env["XCX_BUILDER_TEST_STATUS"]
        let cancelAt = env["XCX_BUILDER_TEST_CANCEL_AT"]
        var started = false, cancelled = false
        model.onPhaseChange = { _ in
            if env["XCX_BUILDER_TEST_BUILD"] == "1", !started, model.canBuild {
                started = true
                model.build()
            }
            if let statusPath { write(model, to: statusPath) }
        }
        // the step events do not change the phase: poll for the cancel point and keep the status file current
        Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in
            MainActor.assumeIsolated {
                if let cancelAt, !cancelled, model.steps.contains(where: { $0.id == cancelAt && $0.status == .running }) {
                    cancelled = true
                    model.cancel()
                }
                if let statusPath { write(model, to: statusPath) }
            }
        }
        if model.requirementsState != .checking { model.checkRequirements() }
    }

    static var commandsDone = 0
    static var showLog: (() -> Void)?
    static var showNotice: (() -> Void)?
    static var showUpdate: (() -> Void)?
    static var scrollTo: ((String) -> Void)?

    static func watch(_ path: String, _ model: BuildModel) {
        Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in
            MainActor.assumeIsolated {
                guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return }
                let lines = text.split(separator: "\n").map(String.init)
                while commandsDone < lines.count {
                    run(lines[commandsDone], model)
                    commandsDone += 1
                }
            }
        }
    }

    static func run(_ command: String, _ model: BuildModel) {
        let parts = command.split(separator: " ", maxSplits: 1).map(String.init)
        switch parts.first {
        case "shot": if parts.count == 2 { shot(to: parts[1]) }
        case "size":   // "size <width> <height>": the window's content size
            let wh = parts.count == 2 ? parts[1].split(separator: " ").compactMap { Double($0) } : []
            if wh.count == 2, let window = NSApp.windows.first(where: { $0.isVisible }) {
                window.setContentSize(NSSize(width: wh[0], height: wh[1]))
            }
        case "log": showLog?()
        case "notice": showNotice?()
        case "update": showUpdate?()
        case "scroll": if parts.count == 2 { scrollTo?(parts[1]) }
        case "devices": InstallModel.shared.refresh()
        case "build": model.build()
        case "cancel": model.cancel()
        case "quit": NSApp.terminate(nil)
        default: break
        }
    }

    /// The window drawn into a PNG by the app itself: its own window through CGWindowListCreateImage (looked up at run
    /// time; an app may capture its own windows without the screen recording permission), else its layers.
    static func shot(to path: String) {
        // the key window: the notice sheet when it is open
        guard let window = NSApp.keyWindow ?? NSApp.windows.first(where: { $0.isVisible && $0.contentView != nil })
        else { return }
        typealias Capture = @convention(c) (CGRect, UInt32, UInt32, UInt32) -> Unmanaged<CGImage>?
        var image: CGImage?
        if let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "CGWindowListCreateImage") {
            let capture = unsafeBitCast(symbol, to: Capture.self)
            // CGRectNull, kCGWindowListOptionIncludingWindow, kCGWindowImageBoundsIgnoreFraming
            image = capture(.null, 1 << 3, UInt32(window.windowNumber), 1 << 0)?.takeRetainedValue()
        }
        if image == nil, let view = window.contentView?.superview, let layer = view.layer {
            let scale = window.backingScaleFactor
            let size = view.bounds.size
            if let ctx = CGContext(data: nil, width: Int(size.width * scale), height: Int(size.height * scale),
                                   bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                   bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) {
                ctx.scaleBy(x: scale, y: scale)
                layer.render(in: ctx)
                image = ctx.makeImage()
            }
        }
        guard let image else { return }
        try? NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?
            .write(to: URL(fileURLWithPath: path))
    }

    static func write(_ model: BuildModel, to path: String) {
        let phase: [String: Any]
        switch model.phase {
        case .idle: phase = ["phase": "idle"]
        case .running: phase = ["phase": "running"]
        case .cancelling: phase = ["phase": "cancelling"]
        case .done(let out): phase = ["phase": "done", "out": out]
        case .failed(let step, let message, let log, _):
            phase = ["phase": "failed", "step": step.map { $0 as Any } ?? NSNull(), "message": message,
                     "log": log.map { $0 as Any } ?? NSNull()]
        case .cancelled(let step): phase = ["phase": "cancelled", "step": step.map { $0 as Any } ?? NSNull()]
        }
        let state: [String: Any] = [
            "window": NSApp.windows.first(where: { $0.isVisible })?.windowNumber ?? 0,
            "requirements": "\(model.requirementsState)",
            "canBuild": model.canBuild,
            "deleteWork": model.deleteWork,
            "detected": Dictionary(uniqueKeysWithValues: model.detected.map { ($0.key.rawValue, $0.value.text) }),
            "buildVersion": model.buildVersion.map { $0 as Any } ?? NSNull(),
            "installed": model.installed.map { $0.version as Any } ?? NSNull(),
            "updatesOlderApp": model.updatesOlderApp,
            "missing": model.missing,
            "installTeams": InstallModel.shared.teams.map { $0 as Any } ?? NSNull(),
            "installDevices": InstallModel.shared.devices.map { "\($0.name) — \($0.model)" },
            "workDeleted": model.workDeleted,
            "status": phase,
            "steps": model.steps.map {
                ["step": $0.id, "status": $0.status.rawValue, "seconds": $0.seconds.map { $0 as Any } ?? NSNull()]
            },
        ]
        if let data = try? JSONSerialization.data(withJSONObject: state, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
        }
    }
}
