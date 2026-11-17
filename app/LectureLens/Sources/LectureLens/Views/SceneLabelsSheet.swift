import AppKit
import SwiftUI

// MARK: contract (`lecture scene-labels list|set`, eval/golden/scene_labels.jsonl)

struct SceneLabels: Decodable, Sendable {
    let ok: Bool; let error: String?; let labels: [Row]?; let confirmed: Int?; let classes: [String]?
    struct Row: Decodable, Sendable, Identifiable {
        let slide_id: String; let lecture: String; let t_abs: String; let thumb: String; let label: String; let source: String; let confirmed: Bool
        var id: String { slide_id }
    }
}

enum SceneText {
    static let names = ["slide": "Slide", "shared_document": "Shared document", "gallery": "Gallery", "break": "Break", "other": "Other"]
    static func progress(_ s: SceneLabels) -> String { "\(s.confirmed ?? 0) of \(s.labels?.count ?? 0) checked" }
}

/// CT-5: correct the vision model's pre-labels for the keyframe test set. A correction (or a
/// confirmation of the pre-label) marks the frame as checked.
struct SceneLabelsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var s: SceneLabels?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Scene labels").font(.scaled(.headline))
                if let s { Text(SceneText.progress(s)).font(.scaled(.caption)).foregroundStyle(.secondary) }
                Spacer()
                Button("Done") { dismiss() }
            }
            Text("What is the main content of each kept frame? Pre-labels come from the vision model; confirm or correct each one.")
                .font(.scaled(.caption)).foregroundStyle(.secondary)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(s?.labels ?? []) { r in
                        HStack(alignment: .top, spacing: 12) {
                            if let img = NSImage(contentsOfFile: r.thumb) {
                                Image(nsImage: img).resizable().scaledToFit().frame(width: 320).border(.quaternary)
                            }
                            VStack(alignment: .leading, spacing: 6) {
                                Text("\(r.lecture.prefix(8)) · \(r.t_abs.dropFirst(11).prefix(8))").font(.scaled(.caption2)).foregroundStyle(.tertiary)
                                Picker("", selection: Binding(get: { r.label }, set: { set(r.slide_id, $0) })) {
                                    ForEach(s?.classes ?? [], id: \.self) { c in Text(SceneText.names[c] ?? c).tag(c) }
                                }.pickerStyle(.radioGroup).labelsHidden()
                                if !r.confirmed { Button("Confirm \(SceneText.names[r.label] ?? r.label)") { set(r.slide_id, r.label) }.font(.scaled(.caption)) }
                                else { Label("checked", systemImage: "checkmark").font(.scaled(.caption2)).foregroundStyle(.green) }
                            }
                        }
                    }
                }
            }
        }
        .padding(20).frame(minWidth: 680, minHeight: 560)
        .task { s = await run(["scene-labels", "list"]) }
    }

    private func set(_ id: String, _ label: String) {
        Task { if let r = await run(["scene-labels", "set", "--id", id, "--label", label]), r.ok { s = r } }
    }

    private func run(_ args: [String]) async -> SceneLabels? {
        try? await ToolRunner.json(SceneLabels.self, "lecture", args + ["--json"], timeout: 30)
    }
}
