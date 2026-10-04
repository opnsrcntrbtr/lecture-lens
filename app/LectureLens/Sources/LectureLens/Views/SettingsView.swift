import AppKit
import SwiftUI

/// Configuration (brief J1/J5): the keys in .env the toolkit reads, edited
/// in place (comments and unknown keys preserved, previous file kept as .env.bak).
/// Only keys listed here are editable — secrets in .env are never shown.
struct SettingsView: View {
    @Bindable var model: AppModel

    struct Field: Identifiable {
        let key: String
        let label: String
        let help: String
        var kind: Kind = .text
        var id: String { key }
        enum Kind { case text, model, toggle, number(ClosedRange<Double>, Double) }
    }

    private let groups: [(String, [Field])] = [
        ("Models (oMLX on 127.0.0.1:8001)", [
            Field(key: "OMLX_MODEL", label: "Text model", help: "Answers, notes and flashcards.", kind: .model),
            Field(key: "OMLX_VL_MODEL", label: "Vision model", help: "Describes slides and diagrams. Empty = same as the text model.", kind: .model),
            Field(key: "OMLX_JUDGE_MODEL", label: "Judge model", help: "Loaded only during DeepEval runs, then swapped back. Must differ from the text model.", kind: .model),
            Field(key: "OMLX_VL_THINK", label: "Vision thinking", help: "Off reads slides faster with the same accuracy in the baseline eval.", kind: .toggle),
        ]),
        ("Capture", [
            Field(key: "SP_VISUAL_CHANGE_THRESHOLD", label: "Visual change threshold",
                  help: "How different a frame must be to be kept. Lower keeps more slides.", kind: .number(0.01...0.5, 0.01)),
            Field(key: "SP_IDLE_CAPTURE_MS", label: "Idle capture interval (ms)",
                  help: "How often a still screen is re-captured.", kind: .number(1000...60000, 1000)),
            Field(key: "STUDY_VL_MAX_EDGE", label: "Slide image max edge (px)",
                  help: "Slides are downscaled to this before the vision model sees them.", kind: .number(512...2048, 128)),
            Field(key: "STUDY_AUDIO_DEVICE", label: "Lecture audio device", help: "Name of the app-audio tap device used for transcripts."),
        ]),
    ]

    @State private var env = EnvFile()
    @State private var values: [String: String] = [:]
    @State private var dirty = false
    @State private var status: String? = nil
    @State private var studyPath = UserDefaults.standard.string(forKey: "studyPath") ?? ""

    private var availableModels: [String] {
        var m = model.omlx?.loaded_models ?? []
        for k in ["OMLX_MODEL", "OMLX_VL_MODEL", "OMLX_JUDGE_MODEL"] {
            if let v = values[k], !v.isEmpty, !m.contains(v) { m.append(v) }
        }
        return m
    }

    var body: some View {
        Form {
            ForEach(groups, id: \.0) { title, fields in
                Section(title) {
                    ForEach(fields) { f in row(f) }
                }
            }
            if let j = values["OMLX_JUDGE_MODEL"], !j.isEmpty, j == values["OMLX_MODEL"] {
                Label("The judge is the same model as the generator — eval scores will be biased in its favour.",
                      systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
            }
            Section("Toolkit") {
                LabeledContent("Study folder") {
                    HStack {
                        TextField("~/lecture-lens", text: $studyPath).textFieldStyle(.roundedBorder)
                        Button("Choose…") { choose() }
                    }
                }
                Text("Currently \(Paths.study.path)").font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Open .env in editor") { NSWorkspace.shared.open(Paths.envFile) }
                    Button("Open study folder") { NSWorkspace.shared.open(Paths.study) }
                }
            }
            Section("About permissions") {
                Text("This app is ad-hoc signed. After each rebuild macOS may ask again for Screen Recording and Microphone. Sign into Xcode with your Apple ID to get a free Development certificate; build_app.sh then signs with it and grants persist across rebuilds.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Settings")
        .toolbar {
            if let status { Text(status).font(.caption).foregroundStyle(.secondary) }
            Button("Revert") { load() }.disabled(!dirty)
            Button("Save") { save() }.keyboardShortcut("s").buttonStyle(.borderedProminent).disabled(!dirty)
        }
        .task { load() }
    }

    @ViewBuilder private func row(_ f: Field) -> some View {
        let binding = Binding<String>(get: { values[f.key] ?? "" },
                                      set: { values[f.key] = $0; dirty = true; status = nil })
        VStack(alignment: .leading, spacing: 3) {
            switch f.kind {
            case .text:
                TextField(f.label, text: binding)
            case .model:
                LabeledContent(f.label) {
                    HStack {
                        TextField("", text: binding).textFieldStyle(.roundedBorder).font(.body.monospaced())
                        Menu("") {
                            ForEach(availableModels, id: \.self) { m in Button(m) { binding.wrappedValue = m } }
                            if availableModels.isEmpty { Text("oMLX not reachable") }
                        }.menuStyle(.borderlessButton).fixedSize()
                    }
                }
            case .toggle:
                Toggle(f.label, isOn: Binding(get: { ["1", "true", "on", "yes"].contains(binding.wrappedValue.lowercased()) },
                                              set: { binding.wrappedValue = $0 ? "1" : "0" }))
            case .number(let range, let step):
                LabeledContent(f.label) {
                    HStack {
                        TextField("", text: binding).textFieldStyle(.roundedBorder).frame(width: 90).monospacedDigit()
                        Stepper("", value: Binding(get: { Double(binding.wrappedValue) ?? range.lowerBound },
                                                   set: { binding.wrappedValue = Self.fmt($0, step) }),
                                in: range, step: step).labelsHidden()
                    }
                }
            }
            Text(f.help).font(.caption).foregroundStyle(.secondary)
        }
    }

    static func fmt(_ v: Double, _ step: Double) -> String {
        step >= 1 ? String(Int(v.rounded())) : String(format: "%.2f", v)
    }

    private func load() {
        env = EnvFile()
        values = [:]
        for (_, fields) in groups { for f in fields { values[f.key] = env.value(f.key) ?? "" } }
        dirty = false
    }

    private func save() {
        UserDefaults.standard.set(studyPath, forKey: "studyPath")
        var e = EnvFile()
        for (_, fields) in groups {
            for f in fields {
                let v = (values[f.key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                // Leave unset keys unset rather than writing KEY= lines.
                if v.isEmpty && e.value(f.key) == nil { continue }
                e.set(f.key, v)
            }
        }
        do {
            try e.save()
            env = e; dirty = false
            status = "Saved · restart capture for capture settings to apply"
            Task { await model.refreshDoctor(probeVision: false) }
        } catch {
            status = "Not saved: \(error.localizedDescription)"
        }
    }

    private func choose() {
        let p = NSOpenPanel()
        p.canChooseDirectories = true; p.canChooseFiles = false
        p.directoryURL = Paths.study
        if p.runModal() == .OK, let u = p.url { studyPath = u.path; dirty = true }
    }
}
