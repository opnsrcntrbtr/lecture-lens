import SwiftUI

// MARK: contracts (`lecture capstone peer ...` capstone_peer.py, `lecture lanes` lecture_kit.lane_summary)

struct PeerList: Decodable, Sendable {
    let ok: Bool; let error: String?; let peers: [Peer]?; let peer: Peer?
    struct Peer: Decodable, Sendable, Identifiable, Hashable { let peer_label: String; let consent: Bool; var id: String { peer_label } }
}

struct PeerCheck: Decodable, Sendable {
    let ok: Bool; let error: String?; let comments: Int?; let findings: [Finding]?; let gated_out: Int?; let note: String?
    struct Finding: Decodable, Sendable, Identifiable {
        let finding_id: String; let check_id: String; let quote: String; let issue: String; let question_to_learner: String
        let evidence_refs: [[String: String]]?
        var id: String { finding_id }
    }
}

struct PeerClusters: Decodable, Sendable {
    let ok: Bool; let error: String?; let clusters: [Cluster]?
    struct Cluster: Decodable, Sendable, Identifiable {
        let cluster_id: String; let name: String; let items: [String]; let artefact_quotes: [Quote]; let `class`: String?; let plan: String?
        var id: String { cluster_id }
    }
    struct Quote: Decodable, Sendable, Hashable { let artefact_id: String; let quote: String }
}

struct PeerOverlap: Decodable, Sendable {
    let ok: Bool; let share_raised: Double?; let themes: [Theme]?
    struct Theme: Decodable, Sendable, Hashable { let cluster_id: String; let name: String; let copilot_raised: Bool }
}

struct Lanes: Decodable, Sendable {
    let ok: Bool; let rows: Int?; let by_kind: [String: Kind]?; let bench: Bench?
    struct Kind: Decodable, Sendable { let calls: Int; let median_wait_s: Double?; let p95_wait_s: Double?; let skipped: Int; let errors: Int; let median_prefill_s: Double?; let median_decode_tps: Double? }
    struct Bench: Decodable, Sendable { let median_idle: Double?; let median_loaded: Double?; let prefix_ratio: Double?; let lanes_ok: Bool?; let prefix_ok: Bool? }
}

enum PeerText {
    static let checkNames = ["vague": "Vague", "unjustified": "Unjustified", "already_answered": "Already answered?", "crp_order": "Questions first"]
    static func lane(_ k: String, _ v: Lanes.Kind) -> String {
        let f = { (x: Double?) in x.map { String(format: "%.1f s", $0) } ?? "–" }
        return "\(k.replacingOccurrences(of: "_", with: " ")): \(v.calls) calls · wait median \(f(v.median_wait_s)), p95 \(f(v.p95_wait_s))\(v.skipped > 0 ? " · \(v.skipped) skipped" : "")\(v.median_decode_tps.map { String(format: " · %.0f tok/s", $0) } ?? "")"
    }
}

/// CC-4: coaching on reviews you write and feedback you receive. It never writes a comment.
struct PeerCoachBox: View {
    @State private var peers: [PeerList.Peer] = []
    @State private var sel = ""
    @State private var reviewPath = ""
    @State private var docPath = ""
    @State private var check: PeerCheck?
    @State private var feedback = ""
    @State private var clusters: [PeerClusters.Cluster] = []
    @State private var plans: [String: String] = [:]
    @State private var overlap: PeerOverlap?
    @State private var note: String?
    @State private var busy = false

    private var current: PeerList.Peer? { peers.first { $0.peer_label == sel } }

    var body: some View {
        GroupBox("Peer review coach") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Checks the reviews you write (vague, unjustified, already answered, questions before opinions) and sorts feedback you receive into themes for you to classify. It never writes a review comment.")
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
                if let n = note { Text(n).font(.scaled(.caption)).foregroundStyle(.orange) }
                HStack {
                    Picker("Peer", selection: $sel) {
                        Text("Choose…").tag("")
                        ForEach(peers) { p in Text("\(p.peer_label)\(p.consent ? " (agreed)" : "")").tag(p.peer_label) }
                    }.frame(width: 220)
                    Button("Add peer") { act(["peer", "add"]) }
                    if let p = current {
                        Toggle("They agreed to share their document", isOn: Binding(get: { p.consent },
                            set: { act(["peer", "consent", "--peer", p.peer_label, $0 ? "--yes" : "--no"]) }))
                            .font(.scaled(.caption))
                    }
                }
                if current != nil {
                    HStack {
                        TextField("Path to your review (markdown or text)", text: $reviewPath)
                        Button("Use review") { act(["peer", "review", "--peer", sel, "--path", reviewPath]) }.disabled(reviewPath.isEmpty)
                    }
                    HStack {
                        TextField("Path to their document", text: $docPath).disabled(current?.consent != true)
                        Button("Load document") { act(["peer", "doc", "--peer", sel, "--path", docPath]) }
                            .disabled(docPath.isEmpty || current?.consent != true)
                    }
                    Button(busy ? "Checking…" : "Check my review") {
                        Task { busy = true; defer { busy = false }
                            check = await Capstone.run(PeerCheck.self, ["peer", "check", "--peer", sel], timeout: 600)
                            note = check?.error }
                    }.disabled(busy)
                    ForEach(check?.findings ?? []) { f in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(PeerText.checkNames[f.check_id] ?? f.check_id).font(.scaled(.caption, weight: .semibold)).foregroundStyle(.orange)
                            Text("“\(f.quote)”").italic().font(.scaled(.callout)).textSelection(.enabled)
                            Text(f.issue).font(.scaled(.caption))
                            Text(f.question_to_learner).font(.scaled(.callout, weight: .medium))
                            if let q = f.evidence_refs?.first?["peer_quote"] { Text("Their document: “\(q)”").font(.scaled(.caption)).foregroundStyle(.secondary) }
                        }
                    }
                }
                Divider()
                Text("Feedback you received (remove names first; the name guard runs again)").font(.scaled(.callout, weight: .semibold))
                TextEditor(text: $feedback).frame(minHeight: 70).overlay(RoundedRectangle(cornerRadius: 4).stroke(.quaternary))
                HStack {
                    Button(busy ? "Sorting…" : "Sort into themes") {
                        Task { busy = true; defer { busy = false }
                            let r = await Capstone.run(PeerClusters.self, ["peer", "feedback", "--text", feedback], timeout: 600)
                            clusters = r?.clusters ?? clusters; note = r?.error }
                    }.disabled(busy || feedback.trimmingCharacters(in: .whitespaces).isEmpty)
                    Button("Overlap with the copilot") { Task { overlap = await Capstone.run(PeerOverlap.self, ["peer", "overlap"]) } }
                        .disabled(clusters.isEmpty)
                }
                ForEach(clusters) { c in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(c.name).font(.scaled(.callout, weight: .medium))
                        ForEach(c.items, id: \.self) { i in Text("• \(i)").font(.scaled(.caption)) }
                        ForEach(c.artefact_quotes, id: \.self) { q in Text("Your \(q.artefact_id): “\(q.quote)”").font(.scaled(.caption2)).foregroundStyle(.secondary) }
                        HStack {
                            Picker("", selection: Binding(get: { c.class ?? "" }, set: { act(["peer", "classify", "--cluster", c.cluster_id, "--class", $0], reload: true) })) {
                                Text("Classify…").tag(""); Text("Tiger").tag("tiger"); Text("Paper Tiger").tag("paper_tiger"); Text("Elephant").tag("elephant")
                            }.labelsHidden().frame(width: 150)
                            TextField("Your revision plan", text: Binding(get: { plans[c.cluster_id] ?? c.plan ?? "" }, set: { plans[c.cluster_id] = $0 }))
                            Button("Save") { act(["peer", "classify", "--cluster", c.cluster_id, "--plan", plans[c.cluster_id] ?? ""], reload: true) }
                                .disabled((plans[c.cluster_id] ?? "").isEmpty)
                        }
                    }
                }
                if let o = overlap, let s = o.share_raised {
                    Text("The copilot had raised \(Int((s * 100).rounded()))% of these themes (a word match, not a judgement of who is right).").font(.scaled(.caption)).foregroundStyle(.secondary)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        .task {
            peers = (await Capstone.run(PeerList.self, ["peer", "list"]))?.peers ?? []
            clusters = (await Capstone.run(PeerClusters.self, ["peer", "clusters"]))?.clusters ?? []
        }
    }

    private func act(_ args: [String], reload: Bool = false) {
        Task {
            struct R: Decodable, Sendable { let ok: Bool; let error: String? }
            let r = await Capstone.run(R.self, args)
            note = r?.ok == true ? nil : (r?.error ?? "could not save")
            peers = (await Capstone.run(PeerList.self, ["peer", "list"]))?.peers ?? peers
            if sel.isEmpty, let last = peers.last { sel = last.peer_label }
            if reload { clusters = (await Capstone.run(PeerClusters.self, ["peer", "clusters"]))?.clusters ?? clusters }
        }
    }
}

/// NG-6: how long each kind of caller waited for the local model.
struct LanesBox: View {
    @State private var l: Lanes?
    var body: some View {
        GroupBox("Model lanes") {
            VStack(alignment: .leading, spacing: 4) {
                Text("Ask goes ahead of waiting builds; live rounds skip rather than queue. The server cannot interrupt a call already running.")
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
                ForEach((l?.by_kind ?? [:]).keys.sorted(), id: \.self) { k in
                    if let v = l?.by_kind?[k] { Text(PeerText.lane(k, v)).font(.scaled(.caption)) }
                }
                if let b = l?.bench, let i = b.median_idle, let ld = b.median_loaded {
                    Text(String(format: "Last bench: Ask first token %.1f s idle, %.1f s during a build; shared prefix cuts prefill to %.0f%%.", i, ld, (b.prefix_ratio ?? 0) * 100))
                        .font(.scaled(.caption2)).foregroundStyle(.tertiary)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { l = try? await ToolRunner.json(Lanes.self, "lecture", ["lanes", "--json"], timeout: 20) }
    }
}
