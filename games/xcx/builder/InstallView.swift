// "Install on my iPhone or iPad" (optional, shown for the iPhone and iPad build): device, team, notes, progress.
import AppKit
import SwiftUI

struct InstallSection: View {
    @EnvironmentObject private var model: BuildModel
    @ObservedObject private var install = InstallModel.shared

    var body: some View {
        Section {
            Text("Signs the IPA with your own Apple ID and installs it over the cable (games/xcx/ios/tools/install_device.py). "
                 + "The 14 GB game data (romfs.bin) goes onto the device once; later installs keep it.")
                .font(.callout)
            notes
            LabeledContent("Device") {
                HStack {
                    if install.devices.isEmpty {
                        Text(install.listing ? "looking…" : "no paired iPhone or iPad").foregroundStyle(.secondary)
                    } else {
                        Picker("", selection: $install.device) {
                            ForEach(install.devices) { d in
                                Text("\(d.name) — \(d.model)" + (d.os.map { ", iOS \($0)" } ?? "")).tag(Optional(d.id))
                            }
                        }
                        .labelsHidden()
                    }
                    Button { install.refresh() } label: { Image(systemName: "arrow.clockwise") }
                        .help("Look for devices again")
                        .disabled(install.listing || install.isRunning)
                }
            }
            if install.devices.isEmpty && !install.listing {
                Text("Connect the iPhone or iPad with a cable, unlock it and tap Trust, then press the refresh button.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            team
            if let error = install.listError {
                Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange).font(.callout)
                    .textSelection(.enabled)
            }
            Toggle(isOn: $install.copyGameData) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Copy the game data")
                    Text(install.gameData(model).map { "\(model.ipaVersion.flatMap { versionDisplay[$0] } ?? ""): "
                                                        + "\(($0.path as NSString).abbreviatingWithTildeInPath), 14.3 GB in 1 GiB parts" }
                         ?? (model.ipaVersion == nil ? "the IPA's version is not known: build it first"
                                                     : "not in the work folder: build the IPA's version first"))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                }
            }
            .disabled(install.isRunning)
            HStack {
                Text(hint).font(.callout).foregroundStyle(.secondary)
                Spacer()
                if install.isRunning {
                    Button("Cancel", role: .cancel) { install.cancel() }
                } else {
                    Button("Install") { install.install() }.disabled(!install.canInstall)
                }
            }
            ForEach(install.steps) { step in
                HStack {
                    StepRow(step: step)
                    if step.id == "game data", let (n, m) = install.part { Text("part \(n)/\(m)").monospacedDigit().foregroundStyle(.secondary) }
                }
            }
            result
        } header: {
            Text("Install on my iPhone or iPad (optional)")
        }
        .onAppear { if install.teams == nil && !install.listing { install.refresh() } }
    }

    private var notes: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("Keep the device unlocked. If Xcode's Devices window (Window > Devices and Simulators) shows the device "
                  + "as Preparing, let it finish first.", systemImage: "lock.open")
            Label("Needs the iOS platform: Xcode > Settings > Components (the prerequisites check it).", systemImage: "square.and.arrow.down")
            Label("A free Apple ID signs for 7 days (a paid membership for a year): install again to renew; the saves stay.",
                  systemImage: "calendar")
        }
        .font(.caption).foregroundStyle(.secondary)
    }

    @ViewBuilder private var team: some View {
        if let teams = install.teams {
            if teams.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Label("No Apple Development certificate on this Mac", systemImage: "xmark.circle.fill").foregroundStyle(.red).bold()
                    Text("In Xcode: Settings > Accounts > add your Apple ID (+). Then select it > Manage Certificates > + > "
                         + "Apple Development. Then press the refresh button.").font(.callout)
                }
            } else if teams.count == 1 {
                Label("Signing team \(teams[0]) (Apple Development certificate)", systemImage: "checkmark.seal.fill")
                    .foregroundStyle(.green).font(.callout)
            } else {
                Label("Several teams (\(teams.joined(separator: ", "))): install with install_device.py --team <id> from Terminal.",
                      systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange).font(.callout)
            }
        }
    }

    private var hint: String {
        let ipa = model.output
        if ipa == nil || !FileManager.default.fileExists(atPath: ipa!.path) { return "Build the IPA first." }
        if install.teams?.isEmpty ?? true { return "Add an Apple Development certificate first." }
        if install.device == nil { return "Connect a device." }
        return "About 5 minutes; the first game data copy takes longer."
    }

    @ViewBuilder private var result: some View {
        switch install.phase {
        case .done(let expires):
            Label("Installed" + (expires.map { ". The signature lasts until \($0.prefix(16).replacingOccurrences(of: "T", with: " "))" } ?? "")
                  + ". First time on this device: Settings > General > VPN & Device Management > trust your Apple ID.",
                  systemImage: "checkmark.seal.fill").foregroundStyle(.green)
        case .failed(let step, let message, let hint):
            VStack(alignment: .leading, spacing: 4) {
                Label(step.map { "Stopped at “\($0)”" } ?? "The install stopped", systemImage: "xmark.octagon.fill")
                    .foregroundStyle(.red).bold()
                Text(message).font(.callout).textSelection(.enabled)
                if let hint { Text(hint).font(.callout).bold() }
            }
        case .cancelled:
            Label("Cancelled.", systemImage: "stop.circle.fill").foregroundStyle(.orange)
        default:
            EmptyView()
        }
    }
}
