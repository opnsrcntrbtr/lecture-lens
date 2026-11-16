import SwiftUI

/// `lecture names scan|apply --json`: counts only (names never appear in this output).
struct NamesAudit: Decodable, Sendable {
    let ok: Bool
    let error: String?
    let files_with_names: Int?
    let files_with_review: Int?
    let removed_total: Int?
    let review_total: Int?
    let held_for_review: Int?
    let roster_names: Int?
    let files_rewritten: Int?
    let backup: String?
}

/// One token from the private candidate file (lectures/.private/names_candidates.json).
struct NameCandidate: Decodable, Sendable, Identifiable {
    let token: String
    let total: Int
    let groups: [String]
    let layer: String
    let score: Double
    let decision: String          // "removed" (by the guard) or "review"
    let held: Bool?
    let learner: String?          // "remove" | "keep" | nil
    var id: String { token }

    /// Review rows that most need a person: roster spellings, frequent matches held back, gated terms.
    var needsDecision: Bool { decision == "review" && (layer.hasPrefix("roster") || held == true || layer == "frequency-gate") }
}

enum NamesReview {
    static var candidatesURL: URL { Paths.lectures.appendingPathComponent(".private/names_candidates.json") }

    static func load() -> [NameCandidate] {
        guard let d = try? Data(contentsOf: candidatesURL) else { return [] }
        return (try? JSONDecoder().decode([NameCandidate].self, from: d)) ?? []
    }

    /// argv for `lecture names decide`, from the sheet's choices (only rows that were changed).
    static func decideArgs(_ choice: [String: String]) -> [String] {
        let rem = choice.filter { $0.value == "remove" }.keys.sorted()
        let keep = choice.filter { $0.value == "keep" }.keys.sorted()
        var a = ["names", "decide", "--json"]
        if !rem.isEmpty { a += ["--remove"] + rem }
        if !keep.isEmpty { a += ["--keep"] + keep }
        return a
    }
}

/// Lectures > Names review: scan stored lectures for attendee names, decide what the guard
/// was unsure about, then apply (files are backed up first; raw captures are never rewritten).
struct NamesReviewSheet: View {
    @Bindable var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var audit: NamesAudit?
    @State private var rows: [NameCandidate] = []
    @State private var choice: [String: String] = [:]
    @State private var busy: String?
    @State private var note: String?

    private var decide: [NameCandidate] { rows.filter(\.needsDecision) }
    private var other: [NameCandidate] { rows.filter { $0.decision == "review" && !$0.needsDecision } }
    private var removed: [NameCandidate] { rows.filter { $0.decision == "removed" } }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Names review").font(.scaled(.title3, weight: .semibold))
                Spacer()
                if let b = busy { ProgressView().controlSize(.small); Text(b).foregroundStyle(.secondary) }
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            summary
            HStack(spacing: 8) {
                Button("Scan again", systemImage: "magnifyingglass") { run(["names", "scan", "--json"], "Scanning…") }
                Button("Save decisions", systemImage: "checkmark") { saveDecisions() }.disabled(choice.isEmpty)
                Spacer()
                Button("Apply to all lectures", systemImage: "wand.and.stars") { saveDecisions(then: apply) }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy != nil || model.mode.running)
                    .help(model.mode.running ? "Stop capture first" : "Rewrite lecture files without the removed names (backups are kept), then rebuild the Anki export")
            }
            if let n = note { Text(n).font(.scaled(.caption)).foregroundStyle(.secondary) }
            List {
                Section("Needs your decision (\(decide.count))") { ForEach(decide) { row($0) } }
                Section("Will be removed (\(removed.count))") { ForEach(removed) { row($0) } }
                Section("Other capitalised words (\(other.count))") { ForEach(other) { row($0) } }
            }
            .listStyle(.inset)
            Text("Remove takes a word out of every lecture file and keeps it out of future builds; Keep marks it as not a name. Raw captures and imports are never rewritten.")
                .font(.scaled(.caption2)).foregroundStyle(.tertiary)
        }
        .padding(18)
        .frame(minWidth: 640, idealWidth: 760, minHeight: 520, idealHeight: 640)
        .task { rows = NamesReview.load(); if rows.isEmpty { run(["names", "scan", "--json"], "Scanning…") } }
    }

    private var summary: some View {
        HStack(spacing: 14) {
            if let a = audit {
                if let e = a.error { Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange) }
                Text("\(a.files_with_names ?? 0) files with names · \(a.removed_total ?? 0) to remove · \(a.review_total ?? 0) to review")
                if let w = a.files_rewritten { Text("· \(w) files rewritten").foregroundStyle(.green) }
            } else {
                Text("\(removed.count) words to remove · \(decide.count) need a decision")
            }
        }
        .font(.scaled(.callout)).foregroundStyle(.secondary)
    }

    private func row(_ c: NameCandidate) -> some View {
        HStack {
            Text(c.token).font(.scaled(.body, design: .monospaced)).textSelection(.enabled)
            Text("×\(c.total) · \(c.groups.count) place\(c.groups.count == 1 ? "" : "s") · \(c.layer)\(c.score > 0 && c.score < 1 ? String(format: " %.2f", c.score) : "")\(c.held == true ? " · held" : "")")
                .font(.scaled(.caption)).foregroundStyle(.secondary)
            Spacer()
            Picker("", selection: Binding(get: { choice[c.token] ?? c.learner ?? "" },
                                          set: { choice[c.token] = $0.isEmpty ? nil : $0 })) {
                Text("—").tag("")
                Text("Remove").tag("remove")
                Text("Keep").tag("keep")
            }
            .pickerStyle(.segmented).labelsHidden().frame(width: 200)
        }
    }

    private func run(_ args: [String], _ label: String, then: (() -> Void)? = nil) {
        busy = label
        Task {
            defer { busy = nil }
            do {
                audit = try await ToolRunner.json(NamesAudit.self, "lecture", args, timeout: 900)
                rows = NamesReview.load()
                then?()
            } catch { note = "\(error)".prefix(200).description }
        }
    }

    private func saveDecisions(then: (() -> Void)? = nil) {
        guard !choice.isEmpty else { then?(); return }
        busy = "Saving…"
        Task {
            defer { busy = nil }
            _ = try? await ToolRunner.run("lecture", NamesReview.decideArgs(choice), timeout: 60)
            choice = [:]
            note = "Decisions saved; they apply to every future build too."
            if let then { then() } else { run(["names", "scan", "--json"], "Scanning…") }
        }
    }

    private func apply() {
        run(["names", "apply", "--json"], "Applying…") {
            if let b = audit?.backup { note = "Backups: \(b)" }
            Task { await model.loadSessions() }
        }
    }
}
