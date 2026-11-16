import SwiftUI

// MARK: contracts (`lecture capstone readiness|panel|progress`, capstone_readiness.py, capstone_panel.py, capstone_eval.py)

struct CapReadiness: Decodable, Sendable {
    let ok: Bool; let error: String?; let items: [Item]?; let gates: [String: Gate]?; let ready: Bool?
    struct Item: Decodable, Sendable, Identifiable {
        let item_id: String; let gate: String; let question: String; let rating: String; let evidence: Evidence?
        var id: String { item_id }
    }
    struct Evidence: Decodable, Sendable { let artefact_id: String?; let quote: String?; let eval_run_id: String? }
    struct Gate: Decodable, Sendable { let items: Int; let demonstrated: Int; let partial: Int; let unrated: Int }
}

struct CapCandidates: Decodable, Sendable {
    let ok: Bool; let candidates: [C]?
    struct C: Decodable, Sendable, Hashable { let artefact_id: String; let quote: String }
}

struct CapTriage: Decodable, Sendable {
    let ok: Bool; let as_of: String?; let to_verify: [Row]?; let note: String?; let questions: [String: String]?
    struct Row: Decodable, Sendable, Hashable { let regime: String; let obligation: String; let date: String; let status: String; let source_url: String? }
}

struct CapCard: Decodable, Sendable { let ok: Bool; let error: String?; let present: [String]?; let mentioned_only: [String]?; let missing: [String]?; let complete: Bool? }

struct CapPanel: Decodable, Sendable {
    let ok: Bool; let error: String?; let note: String?; let seed: Int?; let session_type: String?; let pressure: String?
    let cold: Bool?; let time_box_min: Int?; let time_left_min: Double?; let awaiting_answer: Bool?; let done: Bool?
    let turns: [Turn]?; let self_scored: Bool?; let criteria: [String]?; let score: [Score]?; let metrics: Metrics?
    struct Turn: Decodable, Sendable, Identifiable { let turn: Int; let speaker: String; let role: String; let text: String; let is_followup: Bool; let criterion: String?; var id: Int { turn } }
    struct Score: Decodable, Sendable, Identifiable { let criterion_id: String; let checks: [Check]; let self_score: Int?; var id: String { criterion_id } }
    struct Check: Decodable, Sendable, Hashable { let check_id: String; let verdict: String; let answer_quote: String?; let detail: String? }
    struct Metrics: Decodable, Sendable { let questions: Int; let followups: Int; let stacked_question_rate: Double?; let followup_grounded_share: Double?; let rubric_coverage: Double?; let concessions_blocked: Int; let template_fallbacks: Int }
}

struct CapProgress: Decodable, Sendable {
    let ok: Bool; let checker_card: [Card]?; let outcomes: Outcomes?; let telemetry: Telemetry?
    struct Card: Decodable, Sendable, Identifiable { let criterion: String; let n_labels: Int; let valid_share: Double?; let tpr: Double?; let tnr: Double?; let status: String; let why: [String]; var id: String { criterion } }
    struct Outcomes: Decodable, Sendable {
        let cold_mocks: [Cold]; let drills: [Drill]; let self_minus_critic: Gap; let unresolved_tigers: Int
        struct Cold: Decodable, Sendable, Hashable { let session: String; let checks_passed: Double? }
        struct Drill: Decodable, Sendable, Hashable { let session: String; let mean_rating: Double? }
        struct Gap: Decodable, Sendable { let panel: Double?; let drill: Double? }
    }
    struct Telemetry: Decodable, Sendable { let hints: Int; let attempted_first_share: Double?; let findings: Int; let artefact_changed_after_share: Double?; let days_since_cold_mock: Int? }
}

enum CapText {
    static func pct(_ x: Double?) -> String { x.map { "\(Int(($0 * 100).rounded()))%" } ?? "–" }
    static func scoresArg(_ s: [String: Int]) -> String { s.keys.sorted().map { "\($0)=\(s[$0]!)" }.joined(separator: ",") }
    static func progressLine(_ p: CapProgress) -> String {
        var parts: [String] = []
        if let t = p.telemetry { parts.append("attempt before hint \(pct(t.attempted_first_share)) of \(t.hints) hints") }
        if let o = p.outcomes {
            if let c = o.cold_mocks.last { parts.append("last cold mock \(pct(c.checks_passed)) of checks") }
            if let g = o.self_minus_critic.panel { parts.append(String(format: "self minus critic %+.1f", g)) }
            if o.unresolved_tigers > 0 { parts.append("\(o.unresolved_tigers) Tigers without a mitigation") }
        }
        let ok = (p.checker_card ?? []).filter { $0.status == "ok" }.count
        parts.append("\(ok) of \((p.checker_card ?? []).count) checkers past their accuracy bar")
        return parts.joined(separator: " · ")
    }
}

// MARK: readiness (CC-3)

struct ReadinessBox: View {
    let artefacts: [CapArtefact]
    @State private var r: CapReadiness?
    @State private var cands: [String: [CapCandidates.C]] = [:]
    @State private var pick: [String: CapCandidates.C] = [:]
    @State private var err: String?
    @State private var triage: CapTriage?
    @State private var answers: [String: Bool] = ["users_in_eu": false, "high_risk_like": false, "india_personal_data": false, "chatbot": false]
    @State private var card: CapCard?
    @State private var cardId = ""

    var body: some View {
        GroupBox("Readiness review (evidence or it isn't ticked)") {
            VStack(alignment: .leading, spacing: 8) {
                if let r, let ready = r.ready {
                    Text(ready ? "All five launch gates demonstrated." : "Rate each item; 'Demonstrated' needs a quote from your artefacts or an eval run.")
                        .font(.scaled(.caption)).foregroundStyle(.secondary)
                }
                if let e = err { Text(e).font(.scaled(.caption)).foregroundStyle(.orange) }
                ForEach(r?.items ?? []) { i in item(i) }
                Divider()
                Text("Governance triage (a checklist to verify, not advice)").font(.scaled(.callout, weight: .semibold))
                ForEach(["users_in_eu", "high_risk_like", "india_personal_data", "chatbot"], id: \.self) { k in
                    Toggle(triage?.questions?[k] ?? Capstone.label(k), isOn: Binding(get: { answers[k] ?? false }, set: { answers[k] = $0 }))
                        .font(.scaled(.caption))
                }
                Button("List obligations to check") {
                    Task { triage = await Capstone.run(CapTriage.self, ["readiness", "triage", "--answers",
                        answers.map { "\($0.key)=\($0.value ? "y" : "n")" }.joined(separator: ",")]) }
                }
                if let t = triage {
                    ForEach(t.to_verify ?? [], id: \.self) { o in
                        Text("• \(o.regime): \(o.obligation), \(o.date) (\(o.status); snapshot as of \(t.as_of ?? "?"), verify)")
                            .font(.scaled(.caption)).textSelection(.enabled)
                    }
                    if (t.to_verify ?? []).isEmpty { Text("Nothing triggered by these answers.").font(.scaled(.caption)).foregroundStyle(.secondary) }
                }
                Divider()
                HStack {
                    Picker("System card", selection: $cardId) {
                        Text("Choose…").tag("")
                        ForEach(artefacts) { a in Text("\(a.artefact_id) (\(a.kind))").tag(a.artefact_id) }
                    }.frame(maxWidth: 360)
                    Button("Audit sections") { Task { card = await Capstone.run(CapCard.self, ["readiness", "card", "--artefact", cardId]) } }
                        .disabled(cardId.isEmpty)
                }
                if let c = card, c.ok {
                    Text(c.complete == true ? "All eight sections present." :
                         "Missing: \((c.missing ?? []).map(Capstone.label).joined(separator: ", ")). Mentioned but no heading: \((c.mentioned_only ?? []).map(Capstone.label).joined(separator: ", ")).")
                        .font(.scaled(.caption))
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { r = await Capstone.run(CapReadiness.self, ["readiness", "list"]) }
    }

    @ViewBuilder private func item(_ i: CapReadiness.Item) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(i.question).font(.scaled(.callout))
                Spacer()
                Text(i.rating.replacingOccurrences(of: "_", with: " ")).font(.scaled(.caption2))
                    .foregroundStyle(i.rating == "demonstrated" ? Color.green : i.rating == "stale" ? Color.orange : Color.secondary)
            }
            if let q = i.evidence?.quote { Text("“\(q)”").italic().font(.scaled(.caption)).foregroundStyle(.secondary) }
            HStack {
                Button("Find passages") {
                    Task { cands[i.item_id] = (await Capstone.run(CapCandidates.self, ["readiness", "candidates", "--item", i.item_id]))?.candidates ?? [] }
                }.font(.scaled(.caption))
                if let cs = cands[i.item_id], !cs.isEmpty {
                    Picker("", selection: Binding(get: { pick[i.item_id] }, set: { pick[i.item_id] = $0 })) {
                        Text("Pick evidence…").tag(CapCandidates.C?.none)
                        ForEach(cs, id: \.self) { c in Text("\(c.artefact_id): \(String(c.quote.prefix(70)))").tag(Optional(c)) }
                    }.labelsHidden().frame(maxWidth: 360)
                }
                Button("Demonstrated") { rate(i, "demonstrated") }.disabled(pick[i.item_id] == nil).font(.scaled(.caption))
                Button("Partial") { rate(i, "partial") }.font(.scaled(.caption))
                Button("Not observed") { rate(i, "not_observed") }.font(.scaled(.caption))
            }
        }
    }

    private func rate(_ i: CapReadiness.Item, _ rating: String) {
        Task {
            var args = ["readiness", "rate", "--item", i.item_id, "--rating", rating]
            if let c = pick[i.item_id], rating != "not_observed" { args += ["--artefact", c.artefact_id, "--quote", c.quote] }
            let res = await Capstone.run(CapReadiness.self, args)
            if res?.ok == true { r = res; err = nil } else { err = res?.error ?? "could not rate" }
        }
    }
}

// MARK: panel (CC-5)

struct PanelBox: View {
    @State private var p: CapPanel?
    @State private var type = "panel"
    @State private var pressure = "neutral"
    @State private var answer = ""
    @State private var scores: [String: Int] = [:]
    @State private var busy = false

    var body: some View {
        GroupBox("Panel defence (text)") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Up to three reviewers, each with different documents. One question at a time; one follow-up that quotes your answer; they do not concede without new evidence and they do not score. You score yourself first, then a separate critic rates the transcript.")
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
                HStack {
                    Picker("Type", selection: $type) { Text("Panel").tag("panel"); Text("Leadership review").tag("leadership") }.frame(width: 260)
                    Picker("Pressure", selection: $pressure) { Text("Neutral").tag("neutral"); Text("Sceptical").tag("sceptical"); Text("Interrupting").tag("interrupting") }.frame(width: 240)
                    Button(busy ? "Starting…" : "Start") { act(["panel", "start", "--type", type, "--pressure", pressure]) }.disabled(busy)
                }
                if let p {
                    if let e = p.error ?? p.note { Text(e).font(.scaled(.caption)).foregroundStyle(.orange) }
                    if p.cold == true { Label("Cold mock: no criteria shown", systemImage: "snowflake").font(.scaled(.caption)) }
                    if let t = p.time_left_min, p.done != true { Text("\(Int(max(t, 0))) of \(p.time_box_min ?? 0) minutes left").font(.scaled(.caption2)).foregroundStyle(.secondary) }
                    ForEach(p.turns ?? []) { t in
                        VStack(alignment: .leading, spacing: 1) {
                            Text(t.speaker == "learner" ? "You" : "\(t.role.capitalized)\(t.is_followup ? " (follow-up)" : "")")
                                .font(.scaled(.caption, weight: .semibold)).foregroundStyle(t.speaker == "learner" ? Color.accentColor : Color.primary)
                            Text(t.text).font(.scaled(.callout)).textSelection(.enabled)
                            if let c = t.criterion { Text(c).font(.scaled(.caption2)).foregroundStyle(.tertiary) }
                        }
                    }
                    if p.awaiting_answer == true {
                        TextEditor(text: $answer).frame(minHeight: 70).overlay(RoundedRectangle(cornerRadius: 4).stroke(.quaternary))
                        HStack {
                            Button(busy ? "Waiting for the panel…" : "Answer") { let a = answer; answer = ""; act(["panel", "answer", "--text", a]) }
                                .disabled(busy || answer.trimmingCharacters(in: .whitespaces).count < 2)
                            Button("End session") { act(["panel", "finish"]) }.disabled(busy)
                        }
                    } else if p.done == true && p.self_scored != true {
                        Text("Score yourself first (1 weak, 4 strong):").font(.scaled(.callout, weight: .medium))
                        ForEach(p.criteria ?? [], id: \.self) { c in
                            Picker(Capstone.label(c), selection: Binding(get: { scores[c] ?? 0 }, set: { scores[c] = $0 })) {
                                Text("–").tag(0); ForEach(1...4, id: \.self) { Text("\($0)").tag($0) }
                            }.pickerStyle(.segmented).frame(maxWidth: 480)
                        }
                        Button("Save my scores") { act(["panel", "self", "--scores", CapText.scoresArg(scores.filter { $0.value > 0 })]) }
                            .disabled((p.criteria ?? []).contains { (scores[$0] ?? 0) == 0 })
                    } else if p.done == true && p.score == nil {
                        Button(busy ? "Critic is reading…" : "Show the critic's rating") { act(["panel", "critique"], timeout: 600) }.disabled(busy)
                    }
                    ForEach(p.score ?? []) { s in
                        VStack(alignment: .leading, spacing: 1) {
                            Text("\(Capstone.label(s.criterion_id)): you \(s.self_score.map(String.init) ?? "–")").font(.scaled(.caption, weight: .semibold))
                            ForEach(s.checks, id: \.self) { c in
                                Text("\(c.verdict.replacingOccurrences(of: "_", with: " ")) · \(Capstone.label(c.check_id.replacingOccurrences(of: "panel_", with: "")))\(c.answer_quote.map { ": “\($0)”" } ?? "")\(c.detail.map { " (\($0))" } ?? "")")
                                    .font(.scaled(.caption)).foregroundStyle(c.verdict == "pass" ? Color.green : c.verdict == "fail" ? Color.orange : Color.secondary)
                            }
                        }
                    }
                    if let m = p.metrics {
                        Text("\(m.questions) questions, \(m.followups) follow-ups, coverage \(CapText.pct(m.rubric_coverage)), \(m.concessions_blocked) concessions blocked")
                            .font(.scaled(.caption2)).foregroundStyle(.tertiary)
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { p = await Capstone.run(CapPanel.self, ["panel", "show"]) }
    }

    private func act(_ args: [String], timeout: TimeInterval = 180) {
        Task {
            busy = true; defer { busy = false }
            p = await Capstone.run(CapPanel.self, args, timeout: timeout)
        }
    }
}

// MARK: progress (CC-6)

struct CapProgressBox: View {
    @State private var p: CapProgress?

    var body: some View {
        GroupBox("Progress without the copilot") {
            VStack(alignment: .leading, spacing: 6) {
                if let p {
                    Text(CapText.progressLine(p)).font(.scaled(.caption))
                    DisclosureGroup("Checker card") {
                        ForEach(p.checker_card ?? []) { c in
                            Text("\(Capstone.label(c.criterion)): \(c.status) · \(c.n_labels) labels, valid \(CapText.pct(c.valid_share)) · bench TPR \(CapText.pct(c.tpr)) TNR \(CapText.pct(c.tnr))\(c.why.isEmpty ? "" : " (\(c.why.joined(separator: "; ")))")")
                                .font(.scaled(.caption2)).foregroundStyle(c.status == "ok" ? Color.green : Color.secondary)
                        }
                    }.font(.scaled(.caption))
                } else {
                    Text("Label findings as valid or specific to build each checker's card.").font(.scaled(.caption)).foregroundStyle(.secondary)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { p = await Capstone.run(CapProgress.self, ["progress"]) }
    }
}

/// Valid / specific labels for one finding (CC-6), shown under each finding.
struct FindingLabelRow: View {
    let findingId: String
    @State private var valid: Bool?
    @State private var specific: Bool?
    @State private var saved = false

    var body: some View {
        HStack(spacing: 8) {
            Text("Your label:").font(.scaled(.caption2)).foregroundStyle(.tertiary)
            Picker("", selection: $valid) { Text("valid?").tag(Bool?.none); Text("valid").tag(Bool?.some(true)); Text("not valid").tag(Bool?.some(false)) }
                .labelsHidden().frame(width: 110)
            Picker("", selection: $specific) { Text("specific?").tag(Bool?.none); Text("specific").tag(Bool?.some(true)); Text("vague").tag(Bool?.some(false)) }
                .labelsHidden().frame(width: 110)
            Button(saved ? "Saved" : "Save") {
                guard let v = valid, let s = specific else { return }
                Task {
                    struct R: Decodable, Sendable { let ok: Bool }
                    saved = (await Capstone.run(R.self, ["label", "--finding", findingId, "--valid", v ? "y" : "n", "--specific", s ? "y" : "n"]))?.ok == true
                }
            }.disabled(valid == nil || specific == nil).font(.scaled(.caption2))
        }
    }
}
