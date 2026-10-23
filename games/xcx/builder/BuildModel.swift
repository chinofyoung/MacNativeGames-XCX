// The builder's state: the chosen files (remembered in UserDefaults), the requirement check, and the build, which is
// build_app.py run with --progress json. Its stdout carries one JSON event a line (plan, step, start, done, error,
// cancelled, work-deleted; requirement and requirements for --check-requirements), its stderr the human log. Only paths are passed
// to it; key files are never read here, and build_app.py never prints key values.
import AppKit
import Foundation

enum Platform: String, CaseIterable, Identifiable {
    case macos, ios
    var id: String { rawValue }
    var label: String { self == .macos ? "Mac" : "iPhone and iPad (experimental)" }
    var suffix: String { self == .macos ? ".app" : ".ipa" }
}

enum StepStatus: String {
    case pending, running, built, reused, checked, failed, cancelled
}

struct Step: Identifiable {
    let id: String
    var status: StepStatus = .pending
    var seconds: Double?
    var started: Date?
}

struct Requirement: Identifiable {
    let id: String
    let ok: Bool
    let message: String
    let fixes: [String]
}

enum Phase: Equatable {
    case idle
    case running
    case cancelling
    case done(out: String)
    case failed(step: String?, message: String, log: String?, fixes: [String])
    case cancelled(step: String?)
}

enum RequirementsState: Equatable {
    case unknown, checking, met, missing, failed(String)
}

/// The files the player chooses; each one is remembered under its key.
enum Field: String, CaseIterable {
    case game, update, firmware, keys, titleKeys, output, work
}

let appName = "Xenoblade Chronicles X"
let iosWarning = "Experimental: shaders are not pre-translated, so most of the picture is missing. An iPhone or iPad "
    + "cannot translate shaders while playing, and this port does not translate them ahead of time yet. The IPA carries "
    + "only the shader variants this Mac has already translated; every other draw is skipped."

@MainActor
final class BuildModel: ObservableObject {
    static let shared = BuildModel()

    let launch = Launch.fromCommandLine()
    /// Test mode (TestHook): fields come from the environment and nothing is written to UserDefaults.
    var persist = true

    @Published var platform: Platform = .macos {
        didSet {
            guard platform != oldValue else { return }
            if persist { UserDefaults.standard.set(platform.rawValue, forKey: "platform") }
            if let out = paths[.output] {   // a chosen output keeps its name, with the platform's extension
                set(.output, out.deletingPathExtension().appendingPathExtension(String(platform.suffix.dropFirst())))
            }
            checkRequirements()
        }
    }
    @Published private(set) var paths: [Field: URL] = [:]
    @Published private(set) var gameIsNSP = false

    @Published private(set) var requirements: [Requirement] = []
    @Published private(set) var requirementsState: RequirementsState = .unknown

    @Published private(set) var steps: [Step] = []
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var log = ""
    @Published var legalAccepted = UserDefaults.standard.bool(forKey: "legalAccepted")
    /// --delete-work: remove the work folder after a successful build (off by default, remembered).
    @Published var deleteWork = UserDefaults.standard.bool(forKey: "deleteWork") {
        didSet { if persist && deleteWork != oldValue { UserDefaults.standard.set(deleteWork, forKey: "deleteWork") } }
    }
    /// The last build deleted its work folder (the work-deleted event).
    @Published private(set) var workDeleted = false

    private var process: Process?
    private var checkProcess: Process?
    private var stdoutBuffer = Data()
    private var stderrBuffer = Data()
    private var lastErrorLines: [String] = []
    private var killTimer: Timer?
    var onPhaseChange: ((Phase) -> Void)?

    init() {
        let defaults = UserDefaults.standard
        if let raw = defaults.string(forKey: "platform"), let p = Platform(rawValue: raw) { platform = p }
        for field in Field.allCases {
            if let path = defaults.string(forKey: "path.\(field.rawValue)") { paths[field] = URL(fileURLWithPath: path) }
        }
        gameIsNSP = Self.isNSP(paths[.game])
    }

    // MARK: files

    func set(_ field: Field, _ url: URL?) {
        paths[field] = url
        if field == .game { gameIsNSP = Self.isNSP(url) }
        guard persist else { return }
        if let url { UserDefaults.standard.set(url.path, forKey: "path.\(field.rawValue)") }
        else { UserDefaults.standard.removeObject(forKey: "path.\(field.rawValue)") }
    }

    func setFromEnvironment(_ field: Field, _ path: String?) {
        if let path, !path.isEmpty { set(field, URL(fileURLWithPath: path).standardizedFileURL) }
    }

    /// An NSP starts with PFS0 (build_app.py's input_kind, which goes by the contents, not the name).
    static func isNSP(_ url: URL?) -> Bool {
        guard let url, let handle = try? FileHandle(forReadingFrom: url) else { return false }
        defer { try? handle.close() }
        return (try? handle.read(upToCount: 4)) == Data("PFS0".utf8)
    }

    var defaultOutput: URL? {
        paths[.game]?.deletingLastPathComponent().appendingPathComponent(appName + platform.suffix)
    }
    var output: URL? { paths[.output] ?? defaultOutput }
    /// build_app.py's default: XCX_build in the folder that holds build_app.py (the repository root), whatever the game file.
    var defaultWork: URL? {
        launch.script.map { URL(fileURLWithPath: $0).deletingLastPathComponent().appendingPathComponent("XCX_build") }
    }

    /// What has to be chosen before a build can start.
    var missing: [String] {
        var list: [String] = []
        if paths[.game] == nil { list.append("the game file") }
        if gameIsNSP && paths[.update] == nil { list.append("the 1.0.1 update NSP") }
        if paths[.keys] == nil { list.append("prod.keys") }
        return list
    }

    var isRunning: Bool { phase == .running || phase == .cancelling }
    var canBuild: Bool { missing.isEmpty && !isRunning && launch.script != nil && requirementsState == .met }

    // MARK: requirements

    func checkRequirements() {
        guard let script = launch.script, let python = launch.python else {
            requirementsState = .failed("This window was opened without build_app.py. Open it with: python3 build_app.py --gui")
            return
        }
        checkProcess?.terminate()
        requirementsState = .checking
        requirements = []
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [script, "--check-requirements", "--platform", platform.rawValue, "--progress", "json"]
        p.currentDirectoryURL = URL(fileURLWithPath: script).deletingLastPathComponent()
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { proc in
            let data = out.fileHandleForReading.readDataToEndOfFile()
            let status = proc.terminationStatus
            Task { @MainActor in self.finishRequirements(proc, data, status) }
        }
        checkProcess = p
        do { try p.run() } catch { requirementsState = .failed("Could not run \(python): \(error.localizedDescription)") }
    }

    private func finishRequirements(_ proc: Process, _ data: Data, _ status: Int32) {
        guard proc === checkProcess else { return }   // a newer check replaced this one
        checkProcess = nil
        var rows: [Requirement] = []
        var ok: Bool?
        for line in data.split(separator: UInt8(ascii: "\n")) {
            guard let event = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any] else { continue }
            switch event["event"] as? String {
            case "requirement":
                rows.append(Requirement(id: event["name"] as? String ?? "?", ok: event["ok"] as? Bool ?? false,
                                        message: event["message"] as? String ?? "", fixes: event["fixes"] as? [String] ?? []))
            case "requirements":
                ok = event["ok"] as? Bool
            default: break
            }
        }
        requirements = rows
        switch ok {
        case true?: requirementsState = .met
        case false?: requirementsState = .missing
        case nil: requirementsState = .failed("The requirement check did not finish (exit \(status)).")
        }
        onPhaseChange?(phase)
    }

    // MARK: the build

    func arguments() -> [String]? {
        guard let script = launch.script, let game = paths[.game], let keys = paths[.keys], let output else { return nil }
        var args = [script, game.path, "-k", keys.path]
        if let url = paths[.update], gameIsNSP { args += ["--update", url.path] }
        if let url = paths[.firmware] { args += ["--firmware", url.path] }
        if let url = paths[.titleKeys] { args += ["--title-keys", url.path] }
        if let url = paths[.work] { args += ["--work", url.path] }
        args += ["-o", output.path, "--platform", platform.rawValue, "--progress", "json"]
        if deleteWork { args.append("--delete-work") }
        return args
    }

    func build() {
        guard canBuild, let python = launch.python, let args = arguments() else { return }
        steps = []
        log = ""
        workDeleted = false
        lastErrorLines = []
        stdoutBuffer = Data()
        stderrBuffer = Data()
        phase = .running
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = args
        p.currentDirectoryURL = URL(fileURLWithPath: args[0]).deletingLastPathComponent()
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        out.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            Task { @MainActor in self.received(data, stderr: false) }
        }
        err.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            Task { @MainActor in self.received(data, stderr: true) }
        }
        p.terminationHandler = { proc in
            let status = proc.terminationStatus
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            let restOut = out.fileHandleForReading.readDataToEndOfFile()
            let restErr = err.fileHandleForReading.readDataToEndOfFile()
            Task { @MainActor in
                self.received(restOut, stderr: false)
                self.received(restErr, stderr: true)
                self.finished(status)
            }
        }
        process = p
        appendLog("$ build_app.py \(args.dropFirst().map(Self.quoted).joined(separator: " "))\n")
        do {
            try p.run()
        } catch {
            process = nil
            setPhase(.failed(step: nil, message: "Could not run \(python): \(error.localizedDescription)", log: nil, fixes: []))
        }
    }

    /// Cancel: SIGTERM, which build_app.py turns into a clean stop of the running step (its whole process group);
    /// SIGKILL only if it is still there after 30 s.
    func cancel() {
        guard let p = process, phase == .running else { return }
        setPhase(.cancelling)
        appendLog("\n--- cancelling ---\n")
        p.terminate()
        killTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { _ in
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        }
    }

    func stopForQuit() {
        guard let p = process, p.isRunning else { return }
        p.terminate()
        let deadline = Date().addingTimeInterval(15)
        while p.isRunning && Date() < deadline { usleep(100_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
    }

    private func received(_ data: Data, stderr: Bool) {
        guard !data.isEmpty else { return }
        if stderr {
            stderrBuffer.append(data)
            while let nl = stderrBuffer.firstIndex(of: UInt8(ascii: "\n")) {
                let line = String(decoding: stderrBuffer[stderrBuffer.startIndex..<nl], as: UTF8.self)
                stderrBuffer.removeSubrange(stderrBuffer.startIndex...nl)
                appendLog(line + "\n")
                if !line.trimmingCharacters(in: .whitespaces).isEmpty {
                    lastErrorLines = Array((lastErrorLines + [line]).suffix(8))
                }
            }
        } else {
            stdoutBuffer.append(data)
            while let nl = stdoutBuffer.firstIndex(of: UInt8(ascii: "\n")) {
                let line = Data(stdoutBuffer[stdoutBuffer.startIndex..<nl])
                stdoutBuffer.removeSubrange(stdoutBuffer.startIndex...nl)
                if let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any] { handle(event) }
                else { appendLog(String(decoding: line, as: UTF8.self) + "\n") }
            }
        }
    }

    private func handle(_ event: [String: Any]) {
        switch event["event"] as? String {
        case "plan":
            steps = (event["steps"] as? [String] ?? []).map { Step(id: $0) }
        case "step":
            guard let name = event["step"] as? String, let status = StepStatus(rawValue: event["status"] as? String ?? "")
            else { return }
            if !steps.contains(where: { $0.id == name }) { steps.append(Step(id: name)) }
            let i = steps.firstIndex { $0.id == name }!
            steps[i].status = status
            if status == .running { steps[i].started = Date() } else { steps[i].seconds = event["seconds"] as? Double }
        case "work-deleted":
            workDeleted = true
        case "done":
            setPhase(.done(out: event["out"] as? String ?? output?.path ?? ""))
        case "error":
            setPhase(.failed(step: event["step"] as? String, message: event["message"] as? String ?? "the build failed",
                             log: event["log"] as? String, fixes: event["fixes"] as? [String] ?? []))
        case "cancelled":
            setPhase(.cancelled(step: event["step"] as? String))
        default:
            break
        }
    }

    private func finished(_ status: Int32) {
        process = nil
        killTimer?.invalidate()
        killTimer = nil
        for i in steps.indices where steps[i].status == .running { steps[i].status = .cancelled }
        switch phase {
        case .running, .cancelling:   // no final event: killed, or an error before the progress mode began
            if status == 0, let out = output?.path { setPhase(.done(out: out)) }
            else if status == 130 || status == 15 || status == 9 || phase == .cancelling { setPhase(.cancelled(step: nil)) }
            else {
                setPhase(.failed(step: nil, message: lastErrorLines.joined(separator: "\n").isEmpty
                                 ? "build_app.py stopped (exit \(status))" : lastErrorLines.joined(separator: "\n"),
                                 log: nil, fixes: []))
            }
        default:
            onPhaseChange?(phase)
        }
    }

    private func setPhase(_ new: Phase) {
        phase = new
        onPhaseChange?(new)
    }

    private func appendLog(_ text: String) {
        log += text
        if log.utf8.count > 400_000 { log = "…\n" + String(log.suffix(300_000)) }
    }

    static func quoted(_ s: String) -> String {
        s.contains(where: { " '\"$&;()".contains($0) }) ? "'\(s.replacingOccurrences(of: "'", with: "'\\''"))'" : s
    }

    // MARK: the result

    func openApp(_ path: String) { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
    func showInFinder(_ path: String) { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }

    func acceptLegal() {
        legalAccepted = true
        if persist { UserDefaults.standard.set(true, forKey: "legalAccepted") }
    }
}
