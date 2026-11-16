import SwiftUI

/// `lecture checker todo|label --json`: sentence and passage pairs to label (M-5). A checker
/// earns "supported" in the citation sheet only after it reaches 0.85 on these labels.
struct SupportPairs: Decodable, Sendable {
    let ok: Bool
    let error: String?
    let labelled: Int?
    let target: Int?
    let remaining: Int?
    let todo: [Pair]?
    struct Pair: Decodable, Sendable, Identifiable { let i: Int; let sentence: String; let passage: String; var id: Int { i } }

    static func labelArgs(i: Int, supports: Bool) -> [String] {
        ["checker", "label", "--json", "--i", String(i), "--label", supports ? "1" : "0"]
    }
}

struct SupportPairsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var state: SupportPairs?
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Citation labels").font(.scaled(.headline))
                if let s = state, let n = s.labelled, let t = s.target {
                    Text("\(n) of \(t) labelled").font(.scaled(.caption)).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            Text("Does the lecture passage state or directly imply the sentence? Same topic is not enough.")
                .font(.scaled(.caption)).foregroundStyle(.secondary)
            Divider()
            if let e = error {
                Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
            } else if let p = state?.todo?.first {
                VStack(alignment: .leading, spacing: 10) {
                    GroupBox("Sentence") { Text(p.sentence).font(.scaled(.callout)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                    GroupBox("Lecture passage") {
                        ScrollView { Text(p.passage).font(.scaled(.callout)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                            .frame(maxHeight: 220)
                    }
                    HStack {
                        Button("Supports", systemImage: "checkmark") { Task { await label(p.i, true) } }.keyboardShortcut("y", modifiers: [])
                        Button("Does not", systemImage: "xmark") { Task { await label(p.i, false) } }.keyboardShortcut("n", modifiers: [])
                        if busy { ProgressView().controlSize(.small) }
                        Spacer()
                        Text("Y / N").font(.scaled(.caption2)).foregroundStyle(.tertiary)
                    }
                    .disabled(busy)
                }
            } else if state != nil {
                Text("Nothing left to label. Run `lecture checker pairs --day <yyyymmdd>` for more, then `lecture checker calibrate`.")
                    .foregroundStyle(.secondary)
            } else {
                ProgressView().controlSize(.small)
            }
            Spacer(minLength: 0)
        }
        .padding(18)
        .frame(minWidth: 560, idealWidth: 640, minHeight: 380, idealHeight: 460)
        .task { await load(["checker", "todo", "--json"]) }
    }

    private func label(_ i: Int, _ yes: Bool) async { await load(SupportPairs.labelArgs(i: i, supports: yes)) }

    private func load(_ args: [String]) async {
        busy = true; defer { busy = false }
        do {
            let r = try await ToolRunner.json(SupportPairs.self, "lecture", args, timeout: 30)
            if r.ok { state = r; error = nil } else { error = r.error ?? "Could not load pairs" }
        } catch { self.error = "\(error)" }
    }
}
