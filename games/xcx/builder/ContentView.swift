// The builder's window: platform, files, output, advanced work folder, prerequisites, then the build with its steps,
// log and result.
import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject private var model: BuildModel
    @State private var showLegal = false
    @State private var showAdvanced = false
    @State private var showLog = false

    var body: some View {
        ScrollViewReader { proxy in
            form
                .onChange(of: model.phase) { _, phase in
                    if case .failed = phase { showLog = true }
                    withAnimation {   // keep the steps, then the result, in view
                        if phase == .running { proxy.scrollTo("build", anchor: .top) }
                        else { proxy.scrollTo("result", anchor: .bottom) }
                    }
                }
        }
    }

    private var form: some View {
        Form {
            Section {
                Picker("Build for", selection: $model.platform) {
                    ForEach(Platform.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
                .disabled(model.isRunning)
                if model.platform == .ios {
                    Label(iosWarning, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                        .font(.callout)
                }
            } header: {
                Text("Build Xenoblade Chronicles X from your own files")
            } footer: {
                Text("Xenoblade Chronicles X: Definitive Edition, base game with the 1.0.1 update. The files are "
                     + "recognised by what is inside them, not by their names.")
            }

            Section("Your files") {
                FileRow(title: "Game file", detail: "XCI, or the base game NSP", field: .game, kind: .file)
                if model.gameIsNSP || model.paths[.update] != nil {
                    FileRow(title: "Update 1.0.1", detail: model.gameIsNSP ? "required for an NSP" : "only for a base NSP",
                            field: .update, kind: .file, optional: !model.gameIsNSP)
                }
                FileRow(title: "prod.keys", detail: "from your own Switch", field: .keys, kind: .file)
                FileRow(title: "title.keys", detail: "optional: NSPs without a common ticket", field: .titleKeys,
                        kind: .file, optional: true)
                FileRow(title: "Firmware folder", detail: model.gameIsNSP ? "needed for an NSP (the system fonts)"
                                                                          : "optional: an XCI carries the fonts",
                        field: .firmware, kind: .folder, optional: true)
            }

            Section("Output") {
                FileRow(title: model.platform == .macos ? "App" : "IPA", detail: "where the build is written",
                        field: .output, kind: .save, optional: true,
                        placeholder: model.defaultOutput?.path ?? "next to the game file")
                DisclosureGroup("Advanced", isExpanded: $showAdvanced) {
                    FileRow(title: "Work folder", detail: "keeps every step, so a rebuild takes seconds (about 16 GB)",
                            field: .work, kind: .folder, optional: true,
                            placeholder: model.defaultWork?.path ?? "XCX_build next to the game file")
                }
            }

            RequirementsSection()

            Section {
                BuildControls(showLegal: $showLegal)
                if !model.steps.isEmpty {
                    ForEach(model.steps) { StepRow(step: $0) }
                }
                ResultView()
                if !model.log.isEmpty {
                    DisclosureGroup("Log", isExpanded: $showLog) { LogView(text: model.log) }
                }
            } header: {
                Text("Build").id("build")
            } footer: {
                Color.clear.frame(height: 1).id("result")
            }
        }
        .formStyle(.grouped)
        .defaultScrollAnchor(.top)
        .sheet(isPresented: $showLegal) { LegalSheet(isPresented: $showLegal) }
        .onAppear {
            TestHook.showLog = { showLog = true }
            TestHook.showNotice = { showLegal = true }
            if model.requirementsState == .unknown { model.checkRequirements() }
        }
    }
}

// MARK: files

enum PickKind { case file, folder, save }

struct FileRow: View {
    @EnvironmentObject private var model: BuildModel
    let title: String
    let detail: String
    let field: Field
    let kind: PickKind
    var optional = false
    var placeholder: String?

    var body: some View {
        LabeledContent {
            HStack(spacing: 6) {
                Text(model.paths[field].map { ($0.path as NSString).abbreviatingWithTildeInPath }
                     ?? placeholder.map { ($0 as NSString).abbreviatingWithTildeInPath } ?? (optional ? "none" : "not chosen"))
                    .foregroundStyle(model.paths[field] == nil ? .secondary : .primary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(model.paths[field]?.path ?? "")
                    .frame(maxWidth: .infinity, alignment: .trailing)
                if model.paths[field] != nil && (optional || field == .output) {
                    Button { model.set(field, nil) } label: { Image(systemName: "xmark.circle.fill") }
                        .buttonStyle(.borderless)
                        .foregroundStyle(.secondary)
                        .help(placeholder == nil ? "Clear" : "Use the default")
                }
                Button("Choose…", action: choose)
            }
            .disabled(model.isRunning)
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func choose() {
        let start = model.paths[field] ?? model.paths[.game] ?? model.paths[.keys]
        if kind == .save {
            let panel = NSSavePanel()
            let suffix = String(model.platform.suffix.dropFirst())
            panel.title = model.platform == .macos ? "Where to write the app" : "Where to write the IPA"
            panel.nameFieldStringValue = (model.paths[field] ?? model.defaultOutput)?.lastPathComponent
                ?? appName + model.platform.suffix
            panel.directoryURL = (model.paths[field] ?? model.defaultOutput)?.deletingLastPathComponent()
            panel.allowedContentTypes = [model.platform == .macos ? .applicationBundle
                                                                  : UTType(filenameExtension: suffix) ?? .data]
            panel.canCreateDirectories = true
            guard panel.runModal() == .OK, var url = panel.url else { return }
            if url.pathExtension != suffix { url = url.appendingPathExtension(suffix) }
            model.set(field, url)
            return
        }
        let panel = NSOpenPanel()
        panel.title = "Choose \(title)"
        panel.canChooseFiles = kind == .file
        panel.canChooseDirectories = kind == .folder
        panel.canCreateDirectories = kind == .folder
        panel.allowsMultipleSelection = false
        panel.treatsFilePackagesAsDirectories = false
        if let start { panel.directoryURL = kind == .folder && model.paths[field] != nil ? start : start.deletingLastPathComponent() }
        guard panel.runModal() == .OK, let url = panel.url else { return }
        model.set(field, url)
    }
}

// MARK: requirements

struct RequirementsSection: View {
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        Section {
            switch model.requirementsState {
            case .unknown, .checking:
                HStack { ProgressView().controlSize(.small); Text("Checking this Mac…").foregroundStyle(.secondary) }
            case .failed(let message):
                Label(message, systemImage: "xmark.octagon.fill").foregroundStyle(.red)
            case .met, .missing:
                let missing = model.requirements.filter { !$0.ok }
                if missing.isEmpty {
                    Label("Everything the build needs is installed", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                }
                ForEach(missing) { req in
                    VStack(alignment: .leading, spacing: 4) {
                        Label(req.id, systemImage: "xmark.circle.fill").foregroundStyle(.red).bold()
                        Text(req.message).font(.callout).textSelection(.enabled)
                        ForEach(req.fixes, id: \.self) { CommandRow(command: $0) }
                    }
                    .padding(.vertical, 2)
                }
                DisclosureGroup("All checks (\(model.requirements.count))") {
                    ForEach(model.requirements) { req in
                        HStack(alignment: .firstTextBaseline) {
                            Image(systemName: req.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                                .foregroundStyle(req.ok ? .green : .red)
                            Text(req.id)
                            Spacer()
                            Text(req.ok ? req.message : "missing").font(.caption).foregroundStyle(.secondary)
                                .lineLimit(1).truncationMode(.middle)
                        }
                    }
                }
            }
        } header: {
            HStack {
                Text("Prerequisites")
                Spacer()
                Button("Check Again") { model.checkRequirements() }
                    .buttonStyle(.link)
                    .disabled(model.requirementsState == .checking || model.isRunning)
            }
        }
    }
}

struct CommandRow: View {
    let command: String
    @State private var copied = false

    var body: some View {
        HStack {
            Text(command)
                .font(.system(.callout, design: .monospaced))
                .textSelection(.enabled)
                .padding(.horizontal, 6).padding(.vertical, 3)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 4))
            Button(copied ? "Copied" : "Copy") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(command, forType: .string)
                copied = true
            }
            .controlSize(.small)
        }
    }
}

// MARK: build

struct BuildControls: View {
    @EnvironmentObject private var model: BuildModel
    @Binding var showLegal: Bool

    var body: some View {
        HStack {
            if model.isRunning {
                ProgressView().controlSize(.small)
                Text(model.phase == .cancelling ? "Cancelling…" : "Building…").foregroundStyle(.secondary)
                Spacer()
                Button("Cancel", role: .cancel) { model.cancel() }
                    .disabled(model.phase == .cancelling)
            } else {
                Text(hint).font(.callout).foregroundStyle(.secondary)
                Spacer()
                Button(model.platform == .macos ? "Build App" : "Build IPA") {
                    if model.legalAccepted { model.build() } else { showLegal = true }
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
                .disabled(!model.canBuild)
            }
        }
    }

    private var hint: String {
        if !model.missing.isEmpty { return "Choose " + model.missing.joined(separator: ", ") + "." }
        switch model.requirementsState {
        case .missing: return "Install the missing prerequisites first."
        case .checking, .unknown: return "Checking this Mac…"
        case .failed: return "The requirement check failed."
        case .met: return "A first build takes about 8 minutes and 16 GB; a rebuild reuses every finished step."
        }
    }
}

struct StepRow: View {
    let step: Step

    var body: some View {
        HStack {
            icon.frame(width: 18)
            Text(step.id).foregroundStyle(step.status == .pending ? .secondary : .primary)
            Spacer()
            if step.status == .running, let started = step.started {
                TimelineView(.periodic(from: started, by: 1)) { context in
                    Text(Self.format(context.date.timeIntervalSince(started))).monospacedDigit().foregroundStyle(.secondary)
                }
            } else if let seconds = step.seconds {
                Text(label).font(.caption).foregroundStyle(.secondary)
                Text(Self.format(seconds)).monospacedDigit().foregroundStyle(.secondary)
            }
        }
    }

    private var label: String {
        switch step.status {
        case .reused: return "reused"
        case .built: return "done"
        case .checked: return "checked"
        case .failed: return "failed"
        case .cancelled: return "cancelled"
        default: return ""
        }
    }

    @ViewBuilder private var icon: some View {
        switch step.status {
        case .pending: Image(systemName: "circle").foregroundStyle(.tertiary)
        case .running: ProgressView().controlSize(.small)
        case .built, .checked: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .reused: Image(systemName: "arrow.uturn.backward.circle.fill").foregroundStyle(.teal)
        case .failed: Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
        case .cancelled: Image(systemName: "stop.circle.fill").foregroundStyle(.orange)
        }
    }

    static func format(_ seconds: Double) -> String {
        seconds < 60 ? String(format: "%.1f s", seconds) : String(format: "%d:%02d", Int(seconds) / 60, Int(seconds) % 60)
    }
}

struct ResultView: View {
    @EnvironmentObject private var model: BuildModel

    var body: some View {
        switch model.phase {
        case .done(let out):
            VStack(alignment: .leading, spacing: 8) {
                Label(model.platform == .macos || out.hasSuffix(".app") ? "The app is ready" : "The IPA is ready",
                      systemImage: "checkmark.seal.fill")
                    .foregroundStyle(.green).bold()
                Text(out).font(.callout).textSelection(.enabled).lineLimit(2).truncationMode(.middle)
                if out.hasSuffix(".ipa") {
                    Text("Unsigned: sign and install it with your own Apple ID (games/xcx/ios/tools/install_device.py) "
                         + "or a sideloading tool. It holds no game image; the README says how to copy it over.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text("The app holds the game: keep it for your own use and never share it.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    if out.hasSuffix(".app") {
                        Button("Open App") { model.openApp(out) }.buttonStyle(.borderedProminent)
                    }
                    Button("Show in Finder") { model.showInFinder(out) }
                }
            }
            .padding(.vertical, 4)
        case .failed(let step, let message, let log, let fixes):
            VStack(alignment: .leading, spacing: 6) {
                Label(step.map { "Stopped at “\($0)”" } ?? "The build stopped", systemImage: "xmark.octagon.fill")
                    .foregroundStyle(.red).bold()
                Text(message).font(.callout).textSelection(.enabled)
                ForEach(fixes, id: \.self) { CommandRow(command: $0) }
                if let log {
                    Button("Show the step's log in Finder") { model.showInFinder(log) }
                }
            }
            .padding(.vertical, 4)
        case .cancelled(let step):
            Label("Cancelled" + (step.map { " during “\($0)”" } ?? "")
                  + ". Every finished step is kept: Build continues from there.", systemImage: "stop.circle.fill")
                .foregroundStyle(.orange)
        default:
            EmptyView()
        }
    }
}

struct LogView: View {
    let text: String

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                Text(text)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(6)
                Color.clear.frame(height: 1).id("end")
            }
            .frame(height: 220)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 6))
            .onAppear { proxy.scrollTo("end", anchor: .bottom) }
            .onChange(of: text) { _, _ in proxy.scrollTo("end", anchor: .bottom) }
        }
    }
}

// MARK: legal

struct LegalSheet: View {
    @EnvironmentObject private var model: BuildModel
    @Binding var isPresented: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Before you build", systemImage: "hand.raised.fill").font(.title2).bold()
            Text(LocalizedStringKey("**You must own the game.** Use this only with a copy of Xenoblade Chronicles X: "
                 + "Definitive Edition that you bought, and with the keys dumped from your own Nintendo Switch."))
            Text("Everything is made on this Mac from your own files; nothing from the game is downloaded. Your key "
                 + "files are read by the build and their values are never shown or logged.")
            Text(LocalizedStringKey("The app (or IPA) and the work folder hold the game, so they are for your own use "
                 + "only. **Never share the built app, the work folder or anything in it.**"))
            Text("This is an independent, non-commercial project, not affiliated with or endorsed by Nintendo or "
                 + "Monolith Soft.").font(.callout).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { isPresented = false }
                Button("I Own the Game: Build") {
                    model.acceptLegal()
                    isPresented = false
                    model.build()
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(width: 480)
        .fixedSize(horizontal: false, vertical: true)
    }
}
