import AppKit
import SwiftUI

// MARK: contracts (`lecture capstone ... `, capstone_cli.py)

struct CapArtefact: Decodable, Sendable, Identifiable {
    let artefact_id: String; let kind: String; let path: String; let version: Int; let updated: String
    var id: String { artefact_id }
}

struct CapFinding: Decodable, Sendable, Identifiable {
    let finding_id: String; let artefact_id: String; let quote: String; let criterion_id: String; let check_id: String
    let verdict: String; let issue: String; let question_to_learner: String; let reviewer_view: String?
    let confidence: Double?; let stale: Bool?; let exploratory: Bool?
    var id: String { finding_id }
}

struct CapStatus: Decodable, Sendable {
    let ok: Bool; let error: String?; let locked: Bool?; let artefacts: [CapArtefact]?; let findings: [CapFinding]?
}

struct CapReview: Decodable, Sendable {
    let ok: Bool; let error: String?; let gate_pass_rate: Double?; let results: [Result]?
    struct Result: Decodable, Sendable, Identifiable {
        let check_id: String; let verdict: String; let runs: [String]; let finding: CapFinding?; let note: String?
        var id: String { check_id }
    }
}

struct CapHint: Decodable, Sendable { let ok: Bool; let error: String?; let level: Int?; let text: String?; let needs_attempt: Bool? }

struct CapAsk: Decodable, Sendable { let ok: Bool; let refused: Bool?; let text: String?; let question_to_learner: String? }

struct CapEcon: Decodable, Sendable {
    let ok: Bool; let error: String?; let cost_per_successful_outcome: Double?; let cost_per_user_month_median: Double?
    let cost_per_user_month_p90: Double?; let gross_margin_user_median: Double?; let gross_margin_user_p90: Double?
    let warning: String?
}

struct CapDrill: Decodable, Sendable {
    let ok: Bool; let error: String?; let seed: Int?; let brief: String?; let injects: [Inject]?
    let decisions: [Decision]?; let rated: Bool?; let hotwash: Bool?
    struct Inject: Decodable, Sendable, Identifiable { let inject_id: String; let t_offset_min: Int; let role: String; let text: String; var id: String { inject_id } }
    struct Decision: Decodable, Sendable { let inject_id: String; let severity: String }
}

struct CapHotwash: Decodable, Sendable {
    let ok: Bool; let error: String?; let aar: [Row]?
    struct Row: Decodable, Sendable, Identifiable {
        let objective_id: String; let critic_rating: String; let observation_quote: String; let self_rating: String?
        var id: String { objective_id }
    }
}

enum Capstone {
    static let objectives = ["severity_call", "time_to_declare", "mitigation_before_root_cause", "fallback_in_artefacts",
                             "comms_cadence_honesty", "regulatory_clock", "golden_set_update", "action_item_quality"]
    static let kinds = ["prd", "trust_brief", "evidence_ladder", "cost_model", "system_card", "press_faq", "postmortem",
                        "review", "strategy", "other"]

    static func label(_ id: String) -> String { id.replacingOccurrences(of: "_", with: " ").capitalized }

    static func decideArgs(inject: String, severity: String, containment: String, customer: String, team: String,
                           regulator: String, golden: String, rationale: String) -> [String] {
        ["capstone", "sim", "decide", "--inject", inject, "--severity", severity, "--containment", containment,
         "--customer", customer, "--internal", team, "--regulator", regulator, "--golden", golden, "--rationale", rationale]
    }

    static func ratingsArg(_ r: [String: String]) -> String {
        objectives.compactMap { o in r[o].map { "\(o)=\($0)" } }.joined(separator: ",")
    }

    static func run<T: Decodable & Sendable>(_ t: T.Type, _ args: [String], timeout: TimeInterval = 120) async -> T? {
        try? await ToolRunner.json(T.self, "lecture", ["capstone"] + args, timeout: timeout)
    }
}

// MARK: view

/// Capstone copilot: feedback on the learner's own artefacts and incident drills. It never
/// writes or rewrites a submission, and it does not open while capture runs.
struct CapstoneView: View {
    @Bindable var model: AppModel
    @State private var status: CapStatus?
    @State private var review: CapReview?
    @State private var reviewing = false
    @State private var note: String?
    @State private var attempts: [String: String] = [:]
    @State private var hints: [String: CapHint] = [:]
    @State private var kind = "prd"
    @State private var askText = ""
    @State private var askReply: CapAsk?
    @State private var econ: [String: String] = ["cost_per_call": "", "calls_per_task": "", "retry_rate": "0.1",
                                                 "failure_rate": "0.05", "tasks_per_user_month_median": "",
                                                 "tasks_per_user_month_p90": "", "price_per_user_month": ""]
    @State private var econResult: CapEcon?
    @State private var drill: CapDrill?
    @State private var dec = ["severity": "SEV2", "containment": "", "customer": "", "internal": "", "regulator": "", "golden": "no", "rationale": ""]
    @State private var roleReply: String?
    @State private var selfRatings: [String: String] = [:]
    @State private var hotwash: CapHotwash?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Feedback and drills on your own capstone work. The copilot quotes your words and asks one question; it never writes or rewrites any part of a submission.")
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
                if status?.locked == true {
                    Label("Capture is running: the copilot does not open during a class.", systemImage: "lock.fill")
                        .foregroundStyle(.orange)
                }
                if let n = note { Label(n, systemImage: "info.circle").font(.scaled(.caption)).foregroundStyle(.secondary) }
                artefactsBox
                reviewBox
                econBox
                drillBox
                askBox
            }
            .padding(24)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Capstone")
        .task { await refresh() }
        .disabled(status?.locked == true)
    }

    private func refresh() async { status = await Capstone.run(CapStatus.self, ["status"]) }

    // artefacts
    private var artefactsBox: some View {
        GroupBox("Your artefacts (read only)") {
            VStack(alignment: .leading, spacing: 8) {
                let arts = status?.artefacts ?? []
                if arts.isEmpty { Text("Nothing registered yet. Register your drafts, or load the invented practice product.").foregroundStyle(.secondary) }
                ForEach(arts) { a in
                    HStack {
                        Text(a.artefact_id).font(.scaled(.callout, design: .monospaced))
                        Text(a.kind).foregroundStyle(.secondary)
                        Text("v\(a.version)").foregroundStyle(.tertiary)
                        Spacer()
                        Text(URL(fileURLWithPath: a.path).lastPathComponent).font(.scaled(.caption)).foregroundStyle(.secondary)
                    }
                }
                HStack {
                    Picker("Kind", selection: $kind) { ForEach(Capstone.kinds, id: \.self) { Text($0).tag($0) } }.frame(width: 200)
                    Button("Register file…", systemImage: "doc.badge.plus") { register() }
                    Button("Load practice product") { Task { _ = await Capstone.run(CapStatus.self, ["sample"]); await refresh() } }
                    Spacer()
                    Button("Re-read files", systemImage: "arrow.clockwise") { Task { _ = await Capstone.run(CapStatus.self, ["refresh"]); await refresh() } }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func register() {
        let p = NSOpenPanel(); p.allowedContentTypes = [.plainText, .text]; p.allowsMultipleSelection = false
        p.message = "Choose one of your capstone drafts (Markdown or text). The copilot only reads it."
        guard p.runModal() == .OK, let url = p.url else { return }
        Task {
            _ = await Capstone.run(CapStatus.self, ["register", "--kind", kind, "--path", url.path])
            await refresh()
        }
    }

    // review
    private var reviewBox: some View {
        GroupBox("Strategy review") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Button(reviewing ? "Reviewing…" : "Run review", systemImage: "checklist") {
                        reviewing = true
                        Task {
                            review = await Capstone.run(CapReview.self, ["review"], timeout: 900)
                            reviewing = false
                            if let e = review?.error { note = e }
                            await refresh()
                        }
                    }.disabled(reviewing || (status?.artefacts ?? []).isEmpty)
                    if let r = review?.gate_pass_rate { Text("gate pass rate \(Int(r * 100))%").font(.scaled(.caption)).foregroundStyle(.secondary) }
                    Spacer()
                    Text("Each check runs three times; anything not unanimous is unclear.").font(.scaled(.caption2)).foregroundStyle(.tertiary)
                }
                let fs = (status?.findings ?? []).filter { $0.verdict != "pass" }
                if fs.isEmpty { Text("No open findings.").foregroundStyle(.secondary) }
                ForEach(fs) { f in findingRow(f) }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func findingRow(_ f: CapFinding) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Label(Capstone.label(f.check_id), systemImage: f.verdict == "fail" ? "exclamationmark.circle" : "questionmark.circle")
                    .foregroundStyle(f.verdict == "fail" ? Color.orange : Color.secondary).font(.scaled(.callout, weight: .semibold))
                if let v = f.reviewer_view { Text(v).font(.scaled(.caption2)).foregroundStyle(.tertiary) }
                if f.stale == true { Text("artefact changed: re-run").font(.scaled(.caption2)).foregroundStyle(.orange) }
                if f.exploratory != false {
                    Text("exploratory").font(.scaled(.caption2)).foregroundStyle(.secondary)
                        .help("This checker has not yet met its accuracy bar on the seeded test set (TPR 0.80, TNR 0.90)")
                }
            }
            Text("“\(f.quote)”").italic().font(.scaled(.callout)).textSelection(.enabled)
            Text(f.issue).font(.scaled(.callout))
            Text(f.question_to_learner).font(.scaled(.callout, weight: .medium))
            HStack {
                TextField("Your attempt (15+ characters unlocks the next hint)", text: Binding(
                    get: { attempts[f.finding_id] ?? "" }, set: { attempts[f.finding_id] = $0 }))
                Button("Hint") {
                    Task { hints[f.finding_id] = await Capstone.run(CapHint.self, ["hint", "--finding", f.finding_id, "--attempt", attempts[f.finding_id] ?? ""]) }
                }
            }
            if let h = hints[f.finding_id], let t = h.text {
                Text("Hint \(h.level ?? 0): \(t)").font(.scaled(.caption)).foregroundStyle(.secondary).textSelection(.enabled)
                if h.needs_attempt == true { Text("Write an attempt first to unlock the next level.").font(.scaled(.caption2)).foregroundStyle(.tertiary) }
            }
        }
        .padding(.vertical, 4)
    }

    // unit economics
    private var econBox: some View {
        GroupBox("Unit economics (your numbers, checked)") {
            VStack(alignment: .leading, spacing: 6) {
                let keys = ["cost_per_call", "calls_per_task", "retry_rate", "failure_rate", "tasks_per_user_month_median",
                            "tasks_per_user_month_p90", "price_per_user_month"]
                ForEach(keys, id: \.self) { k in
                    HStack {
                        Text(Capstone.label(k)).frame(width: 240, alignment: .leading)
                        TextField("", text: Binding(get: { econ[k] ?? "" }, set: { econ[k] = $0 })).frame(width: 120)
                    }
                }
                Button("Compute") {
                    let x = econ.compactMapValues { Double($0) }
                    let json = (try? JSONSerialization.data(withJSONObject: x)).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
                    Task { econResult = await Capstone.run(CapEcon.self, ["econ", "--inputs", json]) }
                }
                if let r = econResult {
                    if let e = r.error { Text(e).foregroundStyle(.orange) }
                    if let c = r.cost_per_successful_outcome { Text(String(format: "Cost per successful outcome: %.4f", c)) }
                    if let m = r.cost_per_user_month_median, let p = r.cost_per_user_month_p90 {
                        Text(String(format: "Per user per month: median %.2f · heavy (p90) %.2f", m, p))
                    }
                    if let g = r.gross_margin_user_p90 { Text(String(format: "Gross margin at heavy usage: %.0f%%", g * 100)) }
                    if let w = r.warning { Label(w, systemImage: "exclamationmark.triangle").foregroundStyle(.orange) }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // drill
    private var drillBox: some View {
        GroupBox("Incident drill (20 minutes, 3 injects)") {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Button("Start drill", systemImage: "flame") {
                        Task { drill = await Capstone.run(CapDrill.self, ["sim", "start"]); hotwash = nil; selfRatings = [:]; roleReply = nil }
                    }
                    Button("Next inject") { Task { drill = await Capstone.run(CapDrill.self, ["sim", "next"]) } }.disabled(drill == nil)
                    if let s = drill?.seed { Text("seed \(s)").font(.scaled(.caption)).foregroundStyle(.tertiary) }
                }
                if let b = drill?.brief { Text(b).font(.scaled(.callout)) }
                ForEach(drill?.injects ?? []) { i in
                    Text("+\(i.t_offset_min) min · \(i.role): \(i.text)").font(.scaled(.callout)).textSelection(.enabled)
                }
                if let last = drill?.injects?.last { decisionForm(last) }
                if let r = roleReply, !r.isEmpty { Text(r).italic().font(.scaled(.callout)).foregroundStyle(.secondary) }
                if drill != nil { ratingForm }
                if let h = hotwash?.aar {
                    ForEach(h) { row in
                        HStack(alignment: .top) {
                            Text(Capstone.label(row.objective_id)).frame(width: 230, alignment: .leading)
                            Text("you \(row.self_rating ?? "–") · critic \(row.critic_rating)").frame(width: 130, alignment: .leading)
                            Text(row.observation_quote).font(.scaled(.caption)).foregroundStyle(.secondary)
                        }
                    }
                }
                if let e = hotwash?.error ?? drill?.error { Text(e).foregroundStyle(.orange) }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func decisionForm(_ i: CapDrill.Inject) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Your call on \(i.inject_id)").font(.scaled(.caption, weight: .semibold))
            Picker("Severity", selection: Binding(get: { dec["severity"] ?? "SEV2" }, set: { dec["severity"] = $0 })) {
                ForEach(["SEV1", "SEV2", "SEV3", "SEV4"], id: \.self) { Text($0).tag($0) }
            }.pickerStyle(.segmented).frame(width: 320)
            ForEach(["containment", "customer", "internal", "regulator", "rationale"], id: \.self) { k in
                TextField(Capstone.label(k == "customer" ? "message to customers" : k), text: Binding(get: { dec[k] ?? "" }, set: { dec[k] = $0 }))
            }
            Picker("Would the golden set have caught it?", selection: Binding(get: { dec["golden"] ?? "no" }, set: { dec["golden"] = $0 })) {
                Text("yes").tag("yes"); Text("no").tag("no")
            }.pickerStyle(.segmented).frame(width: 420)
            HStack {
                Button("Record decision") {
                    let args = Capstone.decideArgs(inject: i.inject_id, severity: dec["severity"] ?? "", containment: dec["containment"] ?? "",
                                                   customer: dec["customer"] ?? "", team: dec["internal"] ?? "", regulator: dec["regulator"] ?? "",
                                                   golden: dec["golden"] ?? "", rationale: dec["rationale"] ?? "")
                    Task { _ = try? await ToolRunner.json(CapDrill.self, "lecture", args, timeout: 30); drill = await Capstone.run(CapDrill.self, ["sim", "show"]) }
                }
                Button("Hear from the \(i.role)") {
                    Task {
                        struct R: Decodable, Sendable { let role_reply: String? }
                        roleReply = (await Capstone.run(R.self, ["sim", "reply", "--inject", i.inject_id]))?.role_reply
                    }
                }
            }
        }
    }

    private var ratingForm: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Rate yourself first (P without challenges · S some · M major · U unable)").font(.scaled(.caption, weight: .semibold))
            ForEach(Capstone.objectives, id: \.self) { o in
                Picker(Capstone.label(o), selection: Binding(get: { selfRatings[o] ?? "" }, set: { selfRatings[o] = $0 })) {
                    Text("–").tag(""); ForEach(["P", "S", "M", "U"], id: \.self) { Text($0).tag($0) }
                }.pickerStyle(.segmented).frame(width: 520)
            }
            Button("Save my ratings, then show the critic's") {
                Task {
                    _ = await Capstone.run(CapDrill.self, ["sim", "rate", "--ratings", Capstone.ratingsArg(selfRatings.filter { !$0.value.isEmpty })])
                    hotwash = await Capstone.run(CapHotwash.self, ["sim", "hotwash"])
                }
            }.disabled(selfRatings.values.filter { !$0.isEmpty }.count < Capstone.objectives.count)
        }
    }

    // ask
    private var askBox: some View {
        GroupBox("Ask the coach") {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    TextField("A question about your work (requests to write anything are refused)", text: $askText)
                    Button("Ask") { Task { askReply = await Capstone.run(CapAsk.self, ["ask", askText]) } }.disabled(askText.isEmpty)
                }
                if let r = askReply {
                    if let t = r.text { Text(t).font(.scaled(.callout)) }
                    if let q = r.question_to_learner { Text(q).font(.scaled(.callout, weight: .medium)) }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
