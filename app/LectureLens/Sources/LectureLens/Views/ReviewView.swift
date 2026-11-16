import SwiftUI

// MARK: contracts (`lecture review ...` spaced.py, `lecture mastery` mastery.py, `lecture doctor --privacy` privacy.py)

struct ReviewSession: Decodable, Sendable {
    let ok: Bool; let error: String?; let cards: [Card]?; let due: Int?; let overdue_not_shown: Int?; let new_available: Int?
    let minutes_cap: Double?; let minutes_left: Double?; let planned_minutes: Double?; let retention: Double?; let new_per_week: Int?
    struct Card: Decodable, Sendable, Identifiable, Hashable {
        let card_id: String; let q: String; let a: String; let ref: String?; let lecture: String; let module: String
        let kind: String; let recall: Double?; let lapses: Int?
        var id: String { card_id }
    }
}

struct ReviewStats: Decodable, Sendable {
    let ok: Bool; let cards: Int?; let started: Int?; let due: Int?; let weekly_budget: Double?; let minutes_this_week: Double?
    let retention: Double?; let new_per_week: Int?; let projected_weekly_minutes: Double?; let leeches: [Leech]?; let settings: Settings?
    struct Leech: Decodable, Sendable, Hashable { let card_id: String; let q: String; let lapses: Int }
    struct Settings: Decodable, Sendable { let weekly_minutes: Int; let class_weekdays: [Int]; let new_per_week: Int }
}

struct ReviewGrade: Decodable, Sendable { let ok: Bool; let error: String?; let next_in_days: Int? }

struct Mastery: Decodable, Sendable {
    let ok: Bool; let modules: [Module]?; let min_observations: Int?; let projected_weekly_minutes: Double?; let weekly_budget: Double?
    let sure_and_wrong: [Wrong]?
    struct Module: Decodable, Sendable, Identifiable {
        let module: String; let cards_started: Int; let predicted_recall: Double?; let closed_book_items: Int
        let closed_book_accuracy: Double?; let apply_items: Int; let apply_accuracy: Double?
        let confidence_grid: [String: [String: Int]]; let enough_data: Bool
        var id: String { module }
    }
    struct Wrong: Decodable, Sendable, Hashable { let item_id: String; let stem: String?; let lecture: String? }
}

struct PrivacyReport: Decodable, Sendable {
    let ok: Bool; let checks: [Row]?
    struct Row: Decodable, Sendable, Identifiable { let check: String; let ok: Bool; let level: String; let detail: String; var id: String { check } }
}

enum Review {
    static let days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    static func pct(_ x: Double?) -> String { x.map { "\(Int(($0 * 100).rounded()))%" } ?? "not enough data" }
    static func run<T: Decodable & Sendable>(_ t: T.Type, _ args: [String], timeout: TimeInterval = 90) async -> T? {
        try? await ToolRunner.json(T.self, "lecture", args + ["--json"], timeout: timeout)
    }
    static func summary(_ s: ReviewStats) -> String {
        "\(s.due ?? 0) due · \(Int((s.minutes_this_week ?? 0).rounded())) of \(Int(s.weekly_budget ?? 0)) min this week · target recall \(pct(s.retention)) · \(s.new_per_week ?? 0) new cards a week"
    }
}

/// Spaced review to a weekly minutes budget (LS-3) and what you know without help (LS-7).
/// No streaks: a missed day only reorders the backlog, lowest predicted recall first.
struct ReviewView: View {
    @Bindable var model: AppModel
    @State private var session: ReviewSession?
    @State private var stats: ReviewStats?
    @State private var mastery: Mastery?
    @State private var index = 0
    @State private var shown = false
    @State private var shownAt = Date()
    @State private var attempt = ""
    @State private var minutes = 60
    @State private var classDays: Set<Int> = [5]
    @State private var editing: ReviewSession.Card?
    @State private var editQ = ""
    @State private var editA = ""
    @State private var note: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Cards from your lectures, scheduled to fit your weekly minutes. Missing a day is fine: the backlog is served lowest recall first.")
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
                if let s = stats { Text(Review.summary(s)).font(.scaled(.callout)) }
                if let n = note { Text(n).font(.scaled(.caption)).foregroundStyle(.secondary) }
                sessionBox
                budgetBox
                masteryBox
            }
            .padding(24).frame(maxWidth: 900, alignment: .leading).frame(maxWidth: .infinity)
        }
        .navigationTitle("Review")
        .task { await reload() }
        .sheet(item: $editing) { c in editSheet(c) }
    }

    private func reload() async {
        stats = await Review.run(ReviewStats.self, ["review", "stats"])
        if let s = stats?.settings { minutes = s.weekly_minutes; classDays = Set(s.class_weekdays) }
        session = await Review.run(ReviewSession.self, ["review", "today"])
        mastery = await Review.run(Mastery.self, ["mastery"])
        index = 0; shown = false; attempt = ""
    }

    private var current: ReviewSession.Card? {
        guard let cs = session?.cards, index < cs.count else { return nil }
        return cs[index]
    }

    private var sessionBox: some View {
        GroupBox("Today") {
            VStack(alignment: .leading, spacing: 10) {
                if let s = session {
                    Text("\(s.cards?.count ?? 0) cards in about \(Int((s.planned_minutes ?? 0).rounded())) min (today's share: \(Int((s.minutes_cap ?? 0).rounded())) min)\((s.overdue_not_shown ?? 0) > 0 ? " · \(s.overdue_not_shown!) more due later" : "")")
                        .font(.scaled(.caption)).foregroundStyle(.secondary)
                }
                if let c = current {
                    Text("\(index + 1) of \(session?.cards?.count ?? 0) · \(c.kind == "new" ? "new" : "recall \(Review.pct(c.recall))")").font(.scaled(.caption2)).foregroundStyle(.tertiary)
                    Text(c.q).font(.scaled(.title3, weight: .medium)).textSelection(.enabled)
                    if !shown {
                        TextField("Answer from memory first (optional)", text: $attempt)
                        Button("Show answer") { shown = true; shownAt = Date() }.keyboardShortcut(.space, modifiers: [])
                    } else {
                        if !attempt.isEmpty { Text("You wrote: \(attempt)").font(.scaled(.caption)).foregroundStyle(.secondary) }
                        Text(c.a).font(.scaled(.callout)).textSelection(.enabled)
                        if let r = c.ref, !r.isEmpty { Text(r).font(.scaled(.caption2)).foregroundStyle(.tertiary) }
                        HStack {
                            Button("Missed it") { grade(c, false) }.keyboardShortcut("1", modifiers: [])
                            Button("Got it") { grade(c, true) }.keyboardShortcut("2", modifiers: []).buttonStyle(.borderedProminent)
                            Spacer()
                            Button("Edit") { editQ = c.q; editA = c.a; editing = c }
                            Button("Suspend") { cardAction(c, ["--suspend"]) }
                        }
                    }
                } else {
                    Text(session == nil ? "Loading…" : "Done for today.").foregroundStyle(.secondary)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func grade(_ c: ReviewSession.Card, _ got: Bool) {
        let secs = max(1, Date().timeIntervalSince(shownAt) + 5)
        Task {
            let r = await Review.run(ReviewGrade.self, ["review", "grade", "--card", c.card_id, "--got", got ? "y" : "n", "--seconds", String(format: "%.0f", secs)])
            note = r?.ok == true ? "Next in \(r?.next_in_days ?? 1) day(s)." : (r?.error ?? "could not save")
            index += 1; shown = false; attempt = ""
            if current == nil { stats = await Review.run(ReviewStats.self, ["review", "stats"]) }
        }
    }

    private func cardAction(_ c: ReviewSession.Card, _ extra: [String]) {
        Task {
            struct R: Decodable, Sendable { let ok: Bool }
            _ = await Review.run(R.self, ["review", "card", "--card", c.card_id] + extra)
            index += 1; shown = false; attempt = ""
        }
    }

    private func editSheet(_ c: ReviewSession.Card) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Edit card").font(.scaled(.headline))
            TextField("Question", text: $editQ, axis: .vertical)
            TextField("Answer", text: $editA, axis: .vertical)
            HStack {
                Button("Delete card", role: .destructive) { cardAction(c, ["--delete"]); editing = nil }
                Spacer()
                Button("Cancel") { editing = nil }
                Button("Save") {
                    Task {
                        struct R: Decodable, Sendable { let ok: Bool }
                        _ = await Review.run(R.self, ["review", "card", "--card", c.card_id, "--q", editQ, "--a", editA])
                        editing = nil; await reload()
                    }
                }.buttonStyle(.borderedProminent)
            }
        }.padding(20).frame(width: 520)
    }

    private var budgetBox: some View {
        GroupBox("Weekly budget") {
            VStack(alignment: .leading, spacing: 8) {
                Stepper("\(minutes) minutes a week", value: $minutes, in: 10...600, step: 10)
                HStack {
                    Text("Class days (half share):").font(.scaled(.caption))
                    ForEach(0..<7, id: \.self) { d in
                        Toggle(Review.days[d], isOn: Binding(get: { classDays.contains(d) },
                                                             set: { if $0 { classDays.insert(d) } else { classDays.remove(d) } }))
                            .toggleStyle(.button).font(.scaled(.caption))
                    }
                }
                Button("Save and re-plan") {
                    Task {
                        struct R: Decodable, Sendable { let ok: Bool; let error: String? }
                        let r = await Review.run(R.self, ["review", "settings", "--minutes", String(minutes), "--class-days",
                                                          classDays.sorted().map { Review.days[$0] }.joined(separator: ",")], timeout: 180)
                        note = r?.ok == true ? "Plan updated." : (r?.error ?? "could not save")
                        await reload()
                    }
                }
                if let s = stats {
                    Text("Projected \(Int((s.projected_weekly_minutes ?? 0).rounded())) min a week at target recall \(Review.pct(s.retention)), FSRS-6 default parameters.")
                        .font(.scaled(.caption2)).foregroundStyle(.secondary)
                    if let l = s.leeches, !l.isEmpty {
                        Text("Keeps failing (rewrite or split): " + l.prefix(5).map { "\($0.q.prefix(60))" }.joined(separator: " · "))
                            .font(.scaled(.caption2)).foregroundStyle(.orange)
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var masteryBox: some View {
        GroupBox("What you know without help") {
            VStack(alignment: .leading, spacing: 8) {
                Text("From closed-book items and card reviews only. Estimates; a module needs \(mastery?.min_observations ?? 5) results before a number appears.")
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
                if let w = mastery?.sure_and_wrong, !w.isEmpty {
                    Text("Sure but wrong (review these first)").font(.scaled(.callout, weight: .semibold)).foregroundStyle(.orange)
                    ForEach(w.prefix(8), id: \.self) { x in Text("• \(x.stem ?? x.item_id)").font(.scaled(.caption)).textSelection(.enabled) }
                }
                ForEach(mastery?.modules ?? []) { m in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.module).font(.scaled(.callout, weight: .medium))
                        Text("Predicted recall \(Review.pct(m.predicted_recall)) of \(m.cards_started) cards · closed-book \(Review.pct(m.closed_book_accuracy)) of \(m.closed_book_items) · apply and above \(Review.pct(m.apply_accuracy))")
                            .font(.scaled(.caption)).foregroundStyle(.secondary)
                    }
                }
                if (mastery?.modules ?? []).isEmpty { Text("Nothing yet: review some cards or take a closed-book check.").font(.scaled(.caption)).foregroundStyle(.tertiary) }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// EP-5: local-only assertions, read-only (Settings).
struct PrivacyCheckBox: View {
    @State private var r: PrivacyReport?
    @State private var busy = false
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button(busy ? "Checking…" : "Run privacy check") {
                Task {
                    busy = true; defer { busy = false }
                    r = await Review.run(PrivacyReport.self, ["doctor", "--privacy"], timeout: 60)
                }
            }.disabled(busy)
            ForEach(r?.checks ?? []) { c in
                Label("\(c.check.replacingOccurrences(of: "_", with: " ")): \(c.detail)",
                      systemImage: c.ok ? "checkmark.circle" : (c.level == "warn" ? "exclamationmark.circle" : "xmark.octagon"))
                    .font(.scaled(.caption)).foregroundStyle(c.ok ? Color.secondary : (c.level == "warn" ? Color.orange : Color.red))
            }
        }
    }
}
