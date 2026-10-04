import SwiftUI

struct CaptureView: View {
    @Bindable var model: AppModel
    @State private var domains: [String] = []
    @State private var newDomain = ""
    @State private var probing = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                GroupBox("Health") {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(model.signals) { s in
                            VStack(alignment: .leading, spacing: 4) {
                                SignalRow(signal: s)
                                if let fix = s.fix, s.state == .down || s.state == .warn {
                                    Text("→ \(fix)").font(.caption).foregroundStyle(.secondary).padding(.leading, 95)
                                }
                            }
                        }
                        HStack {
                            Button("Check vision model now", systemImage: "eye") {
                                probing = true
                                Task { await model.refreshDoctor(probeVision: true); probing = false }
                            }.disabled(probing || model.judgeLoaded)
                            .help(model.judgeLoaded ? "Waits until the eval swaps the text model back" : "Sends one test slide to the vision model")
                            if probing { ProgressView().controlSize(.small) }
                            Spacer()
                            Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.refreshAll(probeVision: false) } }
                        }
                    }.padding(6)
                }
                permissions
                GroupBox("Allowed sites (portal mode)") {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(domains, id: \.self) { d in
                            HStack {
                                Image(systemName: "globe").foregroundStyle(.secondary)
                                Text(d).font(.body.monospaced())
                                Spacer()
                                Button("Remove", role: .destructive) { Task { await domain("remove", d) } }
                                    .buttonStyle(.borderless)
                            }
                        }
                        HStack {
                            TextField("domain.com or domain.com:subdomains", text: $newDomain)
                                .textFieldStyle(.roundedBorder).onSubmit { add() }
                            Button("Add") { add() }.disabled(newDomain.isEmpty)
                        }
                    }.padding(6)
                }
            }
            .padding(24)
        }
        .navigationTitle("Capture")
        .alert("Protected lecture video would go black", isPresented: $model.permissionBlock) {
            Button("Open Accessibility Settings") { Permissions.openAccessibilitySettings() }
            Button("Start anyway", role: .destructive) { Task { await model.startCapture(force: true) } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("screenpipe needs Accessibility to recognise the course portal player and pause screen capture there. macOS isn't applying Lecture Lens's Accessibility grant (a rebuild changes the app's signature). In Accessibility, select Lecture Lens, remove it with (−), add it again (+ → ~/Applications), then Start capture.")
        }
        .task { await loadDomains() }
    }

    private var header: some View {
        HStack(spacing: 14) {
            Image(systemName: model.mode.running ? "record.circle.fill" : "record.circle")
                .font(.system(size: 34)).foregroundStyle(model.mode.running ? .red : .secondary)
            VStack(alignment: .leading) {
                Text(model.mode.running ? "Capturing" : "Not capturing").font(.title2.bold())
                Text(model.mode.running ? "\(model.mode.mode.capitalized) mode\(model.mode.autoswitch ? " · switches to Live during Zoom meetings" : "")"
                                        : "Start capture before a lecture").foregroundStyle(.secondary)
            }
            Spacer()
            if model.busyCapture { ProgressView().controlSize(.small) }
            if model.mode.running {
                Picker("Mode", selection: Binding(get: { model.mode.pinned?.isEmpty == false ? model.mode.pinned! : "auto" },
                                                  set: { m in Task { await model.setMode(m) } })) {
                    Text("Auto").tag("auto"); Text("Portal").tag("portal"); Text("Live").tag("live")
                }.pickerStyle(.segmented).frame(width: 220)
                Button("Stop", systemImage: "stop.fill") { Task { await model.stopCapture() } }.controlSize(.large)
            } else {
                Button("Start capture", systemImage: "record.circle") { Task { await model.startCapture() } }
                    .controlSize(.large).buttonStyle(.borderedProminent)
            }
        }
        .disabled(model.busyCapture)
    }

    private var permissions: some View {
        GroupBox("Permissions") {
            VStack(alignment: .leading, spacing: 8) {
                Text("macOS grants recording to the app that launches screenpipe. Start capture from **this app**, then allow **Lecture Lens** under:")
                    .font(.callout)
                Label("Screen & System Audio Recording", systemImage: "rectangle.dashed.badge.record")
                Label("Microphone (only if you enable the mic)", systemImage: "mic")
                Label("Accessibility (on-screen text)", systemImage: "accessibility")
                HStack {
                    Button("Open Privacy & Security", systemImage: "lock.shield") { model.openPrivacySettings() }
                    Button("Relaunch app", systemImage: "arrow.clockwise.circle") { model.relaunch() }
                        .help("macOS tells a running app about a new Screen Recording grant only after it relaunches")
                    Text("Restart capture after granting.").font(.caption).foregroundStyle(.secondary)
                }
            }.padding(6).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func add() {
        let d = newDomain.trimmingCharacters(in: .whitespaces)
        guard !d.isEmpty else { return }
        newDomain = ""
        Task { await domain("add", d) }
    }

    private func domain(_ verb: String, _ d: String) async {
        _ = try? await ToolRunner.run("sp-domains", [verb, d], timeout: 90)
        await loadDomains()
    }

    private func loadDomains() async {
        guard let r = try? await ToolRunner.run("sp-domains", ["list"], timeout: 10) else { return }
        domains = String(decoding: r.stdout, as: UTF8.self).split(separator: "\n").map(String.init)
            .filter { !$0.hasPrefix("(") && !$0.isEmpty }
    }
}
