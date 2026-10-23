// The optional "Install on my iPhone or iPad" part of the window: a front end for games/xcx/ios/tools/install_device.py,
// run with --progress json (--list-devices for the device picker and the Apple Development teams; then the install:
// profile, sign, install, game data part N/M). Only paths and the device identifier are passed; nothing is signed or
// installed until the player presses Install.
import AppKit
import Foundation

struct Device: Identifiable, Hashable {
    let id: String            // devicectl identifier
    let name: String
    let model: String
    let os: String?
    let tunnel: String?
}

enum InstallPhase: Equatable {
    case idle, running, done(expires: String?), failed(step: String?, message: String, hint: String?), cancelled
}

@MainActor
final class InstallModel: ObservableObject {
    static let shared = InstallModel()

    @Published private(set) var devices: [Device] = []
    @Published private(set) var teams: [String]?          // nil: not looked up yet
    @Published private(set) var listing = false
    @Published private(set) var listError: String?
    @Published var device: String? {                       // the chosen device's identifier
        didSet { if device != oldValue, let device { UserDefaults.standard.set(device, forKey: "install.device") } }
    }
    @Published var copyGameData = UserDefaults.standard.object(forKey: "install.gameData") as? Bool ?? true {
        didSet { UserDefaults.standard.set(copyGameData, forKey: "install.gameData") }
    }
    @Published private(set) var phase: InstallPhase = .idle
    @Published private(set) var steps: [Step] = []
    @Published private(set) var part: (Int, Int)?
    @Published private(set) var log = ""
    /// Test hook: pretend no Apple Development certificate exists (XCX_BUILDER_TEST_NO_TEAM=1).
    var simulateNoTeam = false

    private var process: Process?
    private var listProcess: Process?
    private var buffer = Data()

    var script: String? {
        BuildModel.shared.launch.script.map {
            URL(fileURLWithPath: $0).deletingLastPathComponent().appendingPathComponent("games/xcx/ios/tools/install_device.py").path
        }
    }
    var isRunning: Bool { phase == .running }
    var team: String? { teams?.count == 1 ? teams?.first : nil }

    func refresh() {
        guard let script, let python = BuildModel.shared.launch.python else { listError = "Open this window with python3 build_app.py --gui"; return }
        listProcess?.terminate()
        listing = true
        listError = nil
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [script, "--list-devices", "--progress", "json"]
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        p.terminationHandler = { proc in
            let data = out.fileHandleForReading.readDataToEndOfFile()
            let errText = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            Task { @MainActor in self.finishList(proc, data, errText) }
        }
        listProcess = p
        do { try p.run() } catch { listing = false; listError = error.localizedDescription }
    }

    private func finishList(_ proc: Process, _ data: Data, _ errText: String) {
        guard proc === listProcess else { return }
        listProcess = nil
        listing = false
        for line in data.split(separator: UInt8(ascii: "\n")) {
            guard let e = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any] else { continue }
            switch e["event"] as? String {
            case "teams": teams = simulateNoTeam ? [] : (e["teams"] as? [String] ?? [])
            case "devices":
                devices = (e["devices"] as? [[String: Any]] ?? []).compactMap { d in
                    guard let id = d["identifier"] as? String else { return nil }
                    return Device(id: id, name: d["name"] as? String ?? "?", model: d["model"] as? String ?? "",
                                  os: d["os"] as? String, tunnel: d["tunnel"] as? String)
                }
            case "error":
                listError = [e["message"] as? String, e["hint"] as? String].compactMap { $0 }.joined(separator: " ")
            default: break
            }
        }
        if teams == nil && listError == nil { listError = errText.split(separator: "\n").suffix(3).joined(separator: "\n") }
        let remembered = UserDefaults.standard.string(forKey: "install.device")
        if device == nil || !devices.contains(where: { $0.id == device }) {
            device = devices.first(where: { $0.id == remembered })?.id ?? devices.first?.id
        }
        BuildModel.shared.onPhaseChange?(BuildModel.shared.phase)
    }

    /// The IPA at the output and the game data of the version it was built for (the work folder's merged RomFS).
    func gameData(_ model: BuildModel) -> URL? {
        guard let work = model.paths[.work] ?? model.defaultWork else { return nil }
        guard let version = model.ipaVersion else { return nil }   // the game data must be the IPA's version
        let url = work.appendingPathComponent("extracted/\(version)/romfs/romfs.bin")
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    var canInstall: Bool {
        let model = BuildModel.shared
        guard let ipa = model.output, model.platform == .ios else { return false }
        return !isRunning && !model.isRunning && device != nil && team != nil && FileManager.default.fileExists(atPath: ipa.path)
    }

    func install() {
        let model = BuildModel.shared
        guard canInstall, let script, let python = model.launch.python, let ipa = model.output, let device else { return }
        var args = [script, ipa.path, "--device", device, "--progress", "json"]
        if copyGameData, let data = gameData(model) { args += ["--game-data", data.path] }
        steps = []
        part = nil
        log = "$ install_device.py \(args.dropFirst().map(BuildModel.quoted).joined(separator: " "))\n"
        buffer = Data()
        phase = .running
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = args
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        out.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            Task { @MainActor in self.received(d) }
        }
        err.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            Task { @MainActor in self.log += String(decoding: d, as: UTF8.self) }
        }
        p.terminationHandler = { proc in
            let status = proc.terminationStatus
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            Task { @MainActor in
                self.process = nil
                if self.phase == .running { self.phase = status == 0 ? .done(expires: nil) : .failed(step: nil, message: "install_device.py stopped (exit \(status))", hint: nil) }
            }
        }
        process = p
        do { try p.run() } catch { phase = .failed(step: nil, message: error.localizedDescription, hint: nil) }
    }

    func cancel() { process?.terminate() }

    private func received(_ data: Data) {
        buffer.append(data)
        while let nl = buffer.firstIndex(of: UInt8(ascii: "\n")) {
            let line = Data(buffer[buffer.startIndex..<nl])
            buffer.removeSubrange(buffer.startIndex...nl)
            guard let e = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { continue }
            switch e["event"] as? String {
            case "plan": steps = (e["steps"] as? [String] ?? []).map { Step(id: $0) }
            case "step":
                guard let name = e["step"] as? String else { break }
                if !steps.contains(where: { $0.id == name }) { steps.append(Step(id: name)) }
                let i = steps.firstIndex { $0.id == name }!
                steps[i].status = (e["status"] as? String) == "running" ? .running : .built
                if steps[i].status == .running { steps[i].started = Date() }
            case "part": part = (e["part"] as? Int ?? 0, e["parts"] as? Int ?? 0)
            case "done": phase = .done(expires: e["expires"] as? String)
            case "error":
                for i in steps.indices where steps[i].status == .running { steps[i].status = .failed }
                phase = .failed(step: e["step"] as? String, message: e["message"] as? String ?? "failed", hint: e["hint"] as? String)
            case "cancelled": phase = .cancelled
            default: break
            }
        }
    }
}
