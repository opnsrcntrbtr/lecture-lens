import AppKit
import SwiftUI

/// Eval + tuning dashboard (brief J5): run the three suites, watch the job log,
/// compare runs, and see how much feedback has accumulated for the next round.
struct EvalsView: View {
    @Bindable var model: AppModel
    @State private var selectedRun: String? = nil
    @State private var selectedJob: UUID? = nil
    @State private var feedback: [FeedbackRecord] = []

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                suites
                if !model.jobs.jobs.isEmpty { jobs }
                runs
                feedbackBox
            }
            .padding(24)
        }
        .navigationTitle("Evals")
        .toolbar {
            Button("Refresh", systemImage: "arrow.clockwise") { model.loadEvalRuns(); feedback = FeedbackStore.load() }
        }
        .task { model.loadEvalRuns(); feedback = FeedbackStore.load() }
    }

    // MARK: suites
    private var suites: some View {
        GroupBox("Run a suite") {
            VStack(alignment: .leading, spacing: 12) {
                SuiteRow(title: "Vision — slide reading",
                         detail: "36 synthetic slides × Qwen3.6-35B, thinking off. Exact-match on titles, numbers, labels. ~6 min.",
                         symbol: "eye", busy: model.jobs.busy) { model.runVisionEval() }
                SuiteRow(title: "DeepEval — ask · notes · cards",
                         detail: "Generates with Qwen3.6-35B, then swaps in Qwen3.8-27B as judge (Faithfulness, Relevancy, G-Eval), then swaps back. 30–60 min.",
                         symbol: "checkmark.seal", busy: model.jobs.busy) { model.runDeepEval() }
                SuiteRow(title: "Judge benchmark — Laya vs oMLX",
                         detail: "Labelled claim–context pairs scored by the 27B judge and by Laya (System-1). Accuracy, F1, calibration, latency.",
                         symbol: "scalemass", busy: model.jobs.busy) { model.runJudgeBenchmark() }
                Text("One job runs at a time — they share the GPU and the oMLX model slot. Capture keeps running.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(6)
        }
    }

    // MARK: jobs
    private var jobs: some View {
        GroupBox("Jobs") {
            VStack(alignment: .leading, spacing: 8) {
                Picker("Job", selection: Binding(get: { selectedJob ?? model.jobs.jobs.first?.id },
                                                 set: { selectedJob = $0 })) {
                    ForEach(model.jobs.jobs) { j in
                        Text("\(j.running ? "⏳" : (j.succeeded ? "✓" : "✗")) \(j.title) · \(j.started.formatted(date: .omitted, time: .shortened))")
                            .tag(Optional(j.id))
                    }
                }
                .labelsHidden()
                if let job = model.jobs.jobs.first(where: { $0.id == (selectedJob ?? model.jobs.jobs.first?.id) }) {
                    JobLog(job: job)
                }
            }.padding(6)
        }
    }

    // MARK: runs
    private var runs: some View {
        GroupBox("Results") {
            if model.evalRuns.isEmpty {
                Text("No runs yet.").foregroundStyle(.secondary).padding(6)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(model.evalRuns) { run in
                        RunCard(run: run, expanded: selectedRun == run.id) {
                            selectedRun = selectedRun == run.id ? nil : run.id
                        }
                        if run.id != model.evalRuns.last?.id { Divider() }
                    }
                }.padding(6)
            }
        }
    }

    // MARK: feedback
    private var feedbackBox: some View {
        GroupBox("Your feedback") {
            let neg = feedback.filter { $0.rating < 0 }
            let corrected = neg.filter { $0.correction != nil }
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 24) {
                    Stat(value: "\(feedback.count)", label: "ratings")
                    Stat(value: "\(feedback.count - neg.count)", label: "👍")
                    Stat(value: "\(neg.count)", label: "👎")
                    Stat(value: "\(corrected.count)", label: "with corrections")
                }
                Text("Every 👎 with a correction becomes a golden in the next DeepEval round (feedback/feedback.jsonl), so the suite grows from real mistakes rather than synthetic ones.")
                    .font(.caption).foregroundStyle(.secondary)
                if !neg.isEmpty {
                    ForEach(neg.suffix(5).reversed(), id: \.id) { r in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(r.target) · \(r.input)").font(.callout.weight(.medium)).lineLimit(1)
                            if let c = r.correction { Text("→ \(c)").font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                        }
                    }
                }
                Button("Show feedback file", systemImage: "doc.text") {
                    NSWorkspace.shared.activateFileViewerSelecting([Paths.feedback])
                }.disabled(feedback.isEmpty)
            }.padding(6).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct SuiteRow: View {
    let title: String, detail: String, symbol: String, busy: Bool
    let action: () -> Void
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol).font(.title2).frame(width: 30).foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).fontWeight(.medium)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button(busy ? "Queue" : "Run", action: action)
        }
    }
}

private struct Stat: View {
    let value: String, label: String
    var body: some View {
        VStack(alignment: .leading) {
            Text(value).font(.title2.bold().monospacedDigit())
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct JobLog: View {
    let job: Job
    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 1) {
                    ForEach(Array(job.log.enumerated()), id: \.offset) { i, l in
                        Text(l).font(.caption.monospaced()).frame(maxWidth: .infinity, alignment: .leading).id(i)
                    }
                }
                .textSelection(.enabled).padding(8)
            }
            .frame(height: 220)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 6))
            .onChange(of: job.log.count) { _, n in proxy.scrollTo(n - 1, anchor: .bottom) }
        }
    }
}

private struct RunCard: View {
    let run: AppModel.EvalRun
    let expanded: Bool
    let toggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(action: toggle) {
                HStack {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right").frame(width: 12)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title).fontWeight(.medium)
                        Text(subtitle).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if let h = headline { Text(h).font(.callout.monospacedDigit()).foregroundStyle(.secondary) }
                }.contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if expanded {
                metricsTable.padding(.leading, 20)
                HStack {
                    if let rep = run.reportPath { Button("Open report") { NSWorkspace.shared.open(rep) } }
                    Button("Show folder") {
                        NSWorkspace.shared.activateFileViewerSelecting([Paths.results.appendingPathComponent(run.id)])
                    }
                }.padding(.leading, 20)
            }
        }
    }

    private var title: String {
        switch run.summary?.suite {
        case "deepeval-study": "DeepEval · ask / notes / cards"
        case "judge-bench": "Judge benchmark"
        default: run.summary?.by_config != nil ? "Vision · slide reading" : run.id
        }
    }
    private var subtitle: String {
        var parts = [run.date.formatted(date: .abbreviated, time: .shortened)]
        if let g = run.summary?.generator ?? run.summary?.model { parts.append(g) }
        if let j = run.summary?.judge { parts.append("judge: \(j)") }
        return parts.joined(separator: " · ")
    }
    /// One number to scan the list by: overall pass rate across metrics.
    private var headline: String? {
        if let m = run.summary?.metrics, !m.isEmpty {
            let p = m.values.compactMap(\.pass).reduce(0, +), n = m.values.compactMap(\.n).reduce(0, +)
            if n > 0 { return "\(p)/\(n) pass" }
            let means = m.values.compactMap(\.mean)
            return means.isEmpty ? nil : String(format: "mean %.2f", means.reduce(0, +) / Double(means.count))
        }
        return nil
    }

    @ViewBuilder private var metricsTable: some View {
        if let m = run.summary?.metrics, !m.isEmpty {
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 4) {
                GridRow {
                    Text("Metric").bold(); Text("Mean").bold(); Text("Pass").bold(); Text("Threshold").bold()
                }.font(.caption)
                ForEach(m.keys.sorted(), id: \.self) { k in
                    let v = m[k]!
                    GridRow {
                        Text(k)
                        Text(v.mean.map { String(format: "%.3f", $0) } ?? "–").monospacedDigit()
                        Text(v.n.map { "\(v.pass ?? 0)/\($0)" } ?? "–").monospacedDigit()
                            .foregroundStyle(passColor(v))
                        Text(v.threshold.map { String(format: "%.2f", $0) } ?? "–").monospacedDigit()
                    }.font(.callout)
                }
            }
        } else if let byc = run.summary?.by_config {
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 4) {
                ForEach(byc.keys.sorted(), id: \.self) { cfg in
                    GridRow { Text(cfg).bold().gridCellColumns(2) }
                    ForEach((byc[cfg] ?? [:]).keys.sorted(), id: \.self) { k in
                        GridRow {
                            Text(k).foregroundStyle(.secondary)
                            Text(Self.render(byc[cfg]![k]!)).monospacedDigit()
                        }.font(.callout)
                    }
                }
            }
        } else {
            Text("No readable summary.json").foregroundStyle(.secondary)
        }
    }

    private func passColor(_ v: EvalSummary.Metric) -> Color {
        guard let n = v.n, n > 0 else { return .primary }
        let r = Double(v.pass ?? 0) / Double(n)
        return r >= 0.8 ? .green : (r >= 0.5 ? .orange : .red)
    }

    static func render(_ v: FlexValue) -> String {
        switch v {
        case .number(let d): d == d.rounded() && abs(d) < 1e6 ? String(Int(d)) : String(format: "%.3f", d)
        case .agg(let p, let n, let mean): "\(p)/\(n)" + (mean.map { String(format: " (%.2f)", $0) } ?? "")
        case .other: "–"
        }
    }
}
