import SwiftUI

// MARK: contracts (`lecture notes-v2`, notes_v2.py notes_audit.json)

struct NotesAudit: Decodable, Sendable {
    let extract_coverage: Double?; let extracts: Int?; let dropped_extracts: Int?; let timecodes_repaired: Int?
    let numbers_not_in_sources: [String]?; let unreferenced_sentences: Int?; let sentences: Int?
    let title_source: String?; let title_review: Bool?; let seconds: [String: Double]?
}

enum NotesV2 {
    static func summary(_ a: NotesAudit) -> String {
        var s = "\(a.extracts ?? 0) verbatim extracts (\(a.dropped_extracts ?? 0) dropped as not verbatim)"
        if let c = a.extract_coverage { s += " · \(Int((c * 100).rounded()))% used in the notes" }
        if let n = a.numbers_not_in_sources, !n.isEmpty { s += " · numbers not found in the class: \(n.joined(separator: ", "))" }
        if (a.unreferenced_sentences ?? 0) > 0 { s += " · \(a.unreferenced_sentences!) sentence(s) far from their section" }
        if a.title_review == true { s += " · title needs review" }
        return s
    }
}

/// NG-3: notes written section by section from verbatim extracts, beside the current notes.
struct NotesV2Pane: View {
    let folder: URL
    @State private var audit: NotesAudit?
    @State private var busy = false
    @State private var note: String?
    @State private var reload = 0

    private var notesURL: URL { folder.appendingPathComponent("notes_v2/notes.md") }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                if let a = audit { Text(NotesV2.summary(a)).font(.scaled(.caption)).foregroundStyle(.secondary) }
                else { Text("Notes written from the class as delivered: outline first, then verbatim extracts, then each section from its own lines. Your current notes are not changed.")
                        .font(.scaled(.caption)).foregroundStyle(.secondary) }
                Spacer()
                Button(busy ? "Writing (about 6 minutes)…" : (audit == nil ? "Write notes v2" : "Rewrite")) { build() }.disabled(busy)
            }.padding(12)
            if let n = note { Text(n).font(.scaled(.caption)).foregroundStyle(.orange).padding(.horizontal, 12) }
            if FileManager.default.fileExists(atPath: notesURL.path) {
                MarkdownFile(url: notesURL, feedback: nil, model: nil, promptVersion: "notes-v2").id(reload)
            }
        }
        .task(id: folder) { load() }
    }

    private func load() {
        let u = folder.appendingPathComponent("notes_v2/notes_audit.json")
        audit = (try? Data(contentsOf: u)).flatMap { try? JSONDecoder().decode(NotesAudit.self, from: $0) }
    }

    private func build() {
        Task {
            busy = true; defer { busy = false }
            struct R: Decodable, Sendable { let ok: Bool; let error: String? }
            let r = try? await ToolRunner.json(R.self, "lecture", ["notes-v2", folder.lastPathComponent], timeout: 1800)
            note = r?.ok == true ? nil : (r?.error ?? "could not write notes v2 (is the local model running?)")
            load(); reload += 1
        }
    }
}
