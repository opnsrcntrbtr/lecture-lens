import SwiftUI

/// Judge calibration (tests/evals/calibration.py): label each judged case Pass or Fail
/// yourself, blind to the judge's verdict, then compare. The judge's score and reason are
/// shown only after you label a row, so they cannot anchor you. Labels are written to
/// labels.csv after every click; the report gives agreement, Cohen's kappa and the
/// false-pass rate (judge passed, you failed), the error that hides real problems.
struct CalibrationView: View {
    /// labels.csv (the calibration set) or holdout.csv (fresh rows, the honest test).
    var sheetName = "labels.csv"
    var title = "Calibrate the judge"
    @Environment(\.dismiss) private var dismiss
    @State private var sheet = CalibrationSheet()
    @State private var i = 0
    @State private var report: CalibrationReport?
    @State private var error: String?
    @State private var showContext = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            if let error { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange) }
            if sheet.rows.isEmpty {
                ContentUnavailableView("No calibration sheet",
                    systemImage: "checklist",
                    description: Text("Create one in Terminal: .venv-eval/bin/python tests/evals/calibration.py export eval/results/<run> …"))
            } else {
                card(sheet.rows[i])
            }
            if let report { reportView(report) }
        }
        .padding(24)
        .frame(minWidth: 760, idealWidth: 860, minHeight: 620)
        .onAppear { load() }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.scaled(.title2, weight: .bold))
                Text("Would you pass this output on this criterion? Decide first; the judge's verdict appears after you do.")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if !sheet.rows.isEmpty {
                VStack(alignment: .trailing, spacing: 4) {
                    Text("\(sheet.yours) yours · \(sheet.panel) panel · \(sheet.rows.count - sheet.labelled) open").monospacedDigit()
                    ProgressView(value: Double(sheet.yours), total: Double(sheet.rows.count)).frame(width: 180)
                }
            }
            Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
        }
    }

    private func card(_ r: CalibrationRow) -> some View {
        let labelled = r.yours
        return VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(r.metric).font(.scaled(.headline))
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Color.accentColor.opacity(0.12), in: Capsule())
                Text("Row \(i + 1)").foregroundStyle(.secondary).monospacedDigit()
                Spacer()
                Text(r.run).font(.scaled(.caption)).foregroundStyle(.secondary)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    field("Input", r.input)
                    field("Output being judged", r.output)
                    if !r.expected.isEmpty { field("Expected (reference)", r.expected) }
                    if !r.context.isEmpty {
                        DisclosureGroup("Context the output should rest on", isExpanded: $showContext) {
                            Text(r.context).textSelection(.enabled).foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 340)
            if !r.yours && ["0", "1"].contains(r.human) {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: r.source == "panel" ? "person.2" : "person.2.slash").foregroundStyle(.purple)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Panel label: \(r.human == "1" ? "pass" : "fail")"
                             + (r.source == "panel" ? " · both jurors agreed" : " · jurors split, Claude decided")
                             + " (Claude \(r.panelClaude == "1" ? "pass" : "fail"), local \(r.panelLocal == "1" ? "pass" : r.panelLocal == "0" ? "fail" : "–"))")
                            .font(.scaled(.callout, weight: .semibold))
                        if !r.note.isEmpty { Text(r.note).foregroundStyle(.secondary).textSelection(.enabled) }
                        Text("Click Pass or Fail to make it your own label; the judge's verdict then appears.")
                            .font(.scaled(.caption)).foregroundStyle(.secondary)
                    }
                }
                .padding(10).background(Color.purple.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            }
            HStack(spacing: 10) {
                Button { label("1") } label: { Label("Pass", systemImage: "checkmark.circle") }
                    .keyboardShortcut(.upArrow, modifiers: .command).help("Pass (⌘↑)")
                    .tint(.green).buttonStyle(.borderedProminent).opacity(r.human == "0" ? 0.5 : 1)
                Button { label("0") } label: { Label("Fail", systemImage: "xmark.circle") }
                    .keyboardShortcut(.downArrow, modifiers: .command).help("Fail (⌘↓)")
                    .tint(.red).buttonStyle(.borderedProminent).opacity(r.human == "1" ? 0.5 : 1)
                TextField("Note (optional): why", text: Binding(
                    get: { sheet.rows[i].note }, set: { sheet.rows[i].note = $0 }))
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { save() }
                Spacer()
                Button { go(-1) } label: { Image(systemName: "chevron.left") }
                    .keyboardShortcut(.leftArrow, modifiers: .command).disabled(i == 0).help("Previous (⌘←)")
                Button { go(1) } label: { Image(systemName: "chevron.right") }
                    .keyboardShortcut(.rightArrow, modifiers: .command).disabled(i >= sheet.rows.count - 1).help("Next (⌘→)")
                Button("Next not yours") { nextUnlabelled() }.disabled(sheet.yours == sheet.rows.count)
            }
            if labelled {
                let agree = r.human == r.judgePass
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: agree ? "equal.circle" : "exclamationmark.triangle")
                        .foregroundStyle(agree ? .green : .orange)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Judge, old rules: \(r.judgePass == "1" ? "pass" : r.judgePass == "0" ? "fail" : "not scored") (score \(r.judgeScore))\(agree ? " · agrees with you" : " · disagrees with you")")
                            .font(.scaled(.callout, weight: .semibold))
                        Text(r.judgeReason).foregroundStyle(.secondary).textSelection(.enabled).lineLimit(4)
                        ForEach(r.versions, id: \.tag) { v in
                            Text("Judge, rules \(v.tag): \(v.pass == "1" ? "pass" : "fail") (score \(v.score))\(r.human == v.pass ? " · agrees with you" : " · disagrees with you")")
                                .font(.scaled(.callout, weight: .semibold)).padding(.top, 4)
                            Text(v.reason).foregroundStyle(.secondary).textSelection(.enabled).lineLimit(4)
                            if !v.warning.isEmpty {
                                Label(v.warning, systemImage: "exclamationmark.triangle").font(.scaled(.caption)).foregroundStyle(.orange).lineLimit(3)
                            }
                        }
                    }
                }
                .padding(10).background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            }
            HStack {
                Button("Compute report") { Task { await runReport() } }.disabled(sheet.labelled == 0)
                Text("Saved to \(sheet.url.lastPathComponent) after every label.")
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
            }
        }
    }

    private func field(_ title: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.scaled(.caption, weight: .semibold)).foregroundStyle(.secondary)
            Text(text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func reportView(_ r: CalibrationReport) -> some View {
        GroupBox("Judge vs you (\(r.labelled) labelled)") {
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 6) {
                GridRow { Text("Metric").bold(); Text("n").bold(); Text("Agreement").bold(); Text("Kappa").bold(); Text("False pass").bold() }
                row("Overall", r.overall)
                if let h = r.by_source?["human"], h.n > 0 { row("Your labels", h) }
                if let p = r.by_source?["panel"], p.n > 0 { row("Panel labels", p) }
                ForEach(r.by_metric.keys.sorted(), id: \.self) { k in row(k, r.by_metric[k]!) }
            }
            .monospacedDigit().padding(6)
            if let (oldTag, v1) = r.pick(["v1x3", "v1"]), let (newTag, v2) = r.pick(["v2.1x3", "v2.1", "v2x3", "v2"]) {
                Divider()
                Text("Old rules (\(oldTag)) vs new rules (\(newTag)), same labels").font(.scaled(.callout, weight: .semibold))
                Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 6) {
                    GridRow { Text("Against").bold(); Text("n").bold(); Text("Agreement v1 → v2").bold(); Text("Kappa v1 → v2").bold(); Text("False pass v1 → v2").bold() }
                    compare("All labels", v1.overall, v2.overall)
                    if let a = v1.by_source["human"], let b = v2.by_source["human"], b.n > 0 { compare("Your labels", a, b) }
                    if let a = v1.by_source["panel"], let b = v2.by_source["panel"], b.n > 0 { compare("Panel labels", a, b) }
                    ForEach(v2.by_metric.keys.sorted(), id: \.self) { k in
                        if let a = v1.by_metric[k] { compare(k, a, v2.by_metric[k]!) }
                    }
                }
                .monospacedDigit().padding(6)
            }
            Text("False pass = the judge passed what you failed. Under 10% means its passes can be trusted for that metric.")
                .font(.scaled(.caption)).foregroundStyle(.secondary)
        }
    }

    private func row(_ name: String, _ s: CalibrationReport.Stats) -> some View {
        GridRow {
            Text(name)
            Text("\(s.n)")
            Text(pct(s.agreement)); Text(s.kappa.map { String(format: "%.2f", $0) } ?? "–")
            Text(pct(s.false_pass_rate)).foregroundStyle((s.false_pass_rate ?? 0) >= 0.1 ? .orange : .primary)
        }
    }

    private func compare(_ name: String, _ a: CalibrationReport.Stats, _ b: CalibrationReport.Stats) -> some View {
        GridRow {
            Text(name)
            Text("\(b.n)")
            Text("\(pct(a.agreement)) → \(pct(b.agreement))")
                .foregroundStyle((b.agreement ?? 0) > (a.agreement ?? 0) ? .green : (b.agreement ?? 0) < (a.agreement ?? 0) ? .orange : .primary)
            Text("\(a.kappa.map { String(format: "%.2f", $0) } ?? "–") → \(b.kappa.map { String(format: "%.2f", $0) } ?? "–")")
            Text("\(pct(a.false_pass_rate)) → \(pct(b.false_pass_rate))")
                .foregroundStyle((b.false_pass_rate ?? 0) >= 0.1 ? .orange : .primary)
        }
    }

    private func pct(_ v: Double?) -> String { v.map { "\(Int(($0 * 100).rounded()))%" } ?? "–" }

    private func load() {
        do { sheet = try CalibrationSheet.load(sheetName); nextUnlabelled() } catch { self.error = "Could not read the sheet: \(error.localizedDescription)" }
    }

    private func label(_ v: String) {
        sheet.rows[i].human = v
        sheet.rows[i].source = "human"
        save()
    }

    private func save() {
        do { try sheet.save(); error = nil } catch { self.error = "Not saved: \(error.localizedDescription)" }
    }

    private func go(_ d: Int) { showContext = false; i = min(max(0, i + d), sheet.rows.count - 1) }

    private func nextUnlabelled() {
        guard !sheet.rows.isEmpty else { return }
        let order = Array(i..<sheet.rows.count) + Array(0..<i)
        // open rows first, then panel rows to review
        if let j = order.first(where: { !["0", "1"].contains(sheet.rows[$0].human) }) ?? order.first(where: { !sheet.rows[$0].yours }) {
            i = j; showContext = false
        }
    }

    private func runReport() async {
        do {
            let r = try await ToolRunner.run(".venv-eval/bin/python", ["tests/evals/calibration.py", "report", sheet.url.path], timeout: 60)
            report = try JSONDecoder().decode(CalibrationReport.self, from: r.stdout)
        } catch { self.error = "Report failed: \(error.localizedDescription)" }
    }
}

struct CalibrationReport: Decodable {
    struct Stats: Decodable { var n: Int; var agreement: Double?; var kappa: Double?; var false_pass_rate: Double? }
    var labelled: Int
    var overall: Stats
    var by_metric: [String: Stats]
    var by_source: [String: Stats]?
    struct Rubric: Decodable { var overall: Stats; var by_source: [String: Stats]; var by_metric: [String: Stats] }
    var by_rubric: [String: Rubric]?
    /// The first rubric tag present, in order of preference (majority-of-3 runs first).
    func pick(_ tags: [String]) -> (String, Rubric)? {
        for t in tags { if let r = by_rubric?[t] { return (t, r) } }
        return nil
    }
}

struct CalibrationRow {
    var cells: [String: String]
    var metric: String { cells["metric"] ?? "" }
    var run: String { cells["run"] ?? "" }
    var input: String { cells["input"] ?? "" }
    var output: String { cells["output"] ?? "" }
    var expected: String { cells["expected"] ?? "" }
    var context: String { cells["context"] ?? "" }
    var judgeScore: String { cells["judge_score"] ?? "" }
    var judgePass: String { cells["judge_pass"] ?? "" }
    var judgeReason: String { cells["judge_reason"] ?? "" }
    var judgeV2Pass: String { cells["judge_v2_pass"] ?? "" }
    /// Re-judged verdicts (calibration.py rejudge): judge_<tag>_pass for tags like v2, v2.1x3.
    var versions: [(tag: String, pass: String, score: String, reason: String, warning: String)] {
        cells.keys.compactMap { k -> String? in
            guard k.hasPrefix("judge_v"), k.hasSuffix("_pass") else { return nil }
            return String(k.dropFirst(6).dropLast(5))
        }
        .filter { ["0", "1"].contains(cells["judge_\($0)_pass"] ?? "") }
        .sorted()
        .map { t in (t, cells["judge_\(t)_pass"] ?? "", cells["judge_\(t)_score"] ?? "", cells["judge_\(t)_reason"] ?? "",
                     cells["judge_\(t)_claims_warning"] ?? "") }
    }
    var judgeV2Score: String { cells["judge_v2_score"] ?? "" }
    var judgeV2Reason: String { cells["judge_v2_reason"] ?? "" }
    var human: String { get { cells["human_pass"] ?? "" } set { cells["human_pass"] = newValue } }
    /// human = your own label; panel / panel-split = the adversarial panel's (tests/evals/panel_label.py).
    var source: String { get { (cells["label_source"] ?? "").isEmpty ? "human" : cells["label_source"]! }
                         set { cells["label_source"] = newValue } }
    var yours: Bool { (human == "0" || human == "1") && source == "human" }
    var panelClaude: String { cells["panel_claude"] ?? "" }
    var panelLocal: String { cells["panel_local"] ?? "" }
    var note: String { get { cells["note"] ?? "" } set { cells["note"] = newValue } }
}

/// labels.csv, read and written as RFC 4180 CSV (quoted fields may hold commas, quotes, newlines).
struct CalibrationSheet {
    static func url(_ name: String) -> URL { Paths.study.appendingPathComponent("tests/evals/calibration/\(name)") }
    var url: URL = CalibrationSheet.url("labels.csv")
    var header: [String] = []
    var rows: [CalibrationRow] = []
    var labelled: Int { rows.filter { ["0", "1"].contains($0.human) }.count }
    var yours: Int { rows.filter(\.yours).count }
    var panel: Int { rows.filter { ["0", "1"].contains($0.human) && $0.source != "human" }.count }

    static func load(_ name: String = "labels.csv") throws -> CalibrationSheet {
        let url = Self.url(name)
        guard FileManager.default.fileExists(atPath: url.path) else { return CalibrationSheet(url: url) }
        let table = CSV.parse(try String(contentsOf: url, encoding: .utf8))
        guard let head = table.first else { return CalibrationSheet(url: url) }
        let rows = table.dropFirst().filter { $0.count > 1 }.map { r in
            CalibrationRow(cells: Dictionary(uniqueKeysWithValues: head.enumerated().map { ($1, $0 < r.count ? r[$0] : "") }))
        }
        return CalibrationSheet(url: url, header: head, rows: rows)
    }

    /// Writes your labels into the file as it is now: only human_pass, note and label_source come
    /// from this copy, so judge scores written meanwhile by calibration.py rejudge are kept.
    func save() throws {
        var current = (try? Self.load(url.lastPathComponent)) ?? self
        if current.rows.isEmpty { current = self }
        let mine = Dictionary(rows.map { ($0.cells["id"] ?? "", $0) }, uniquingKeysWith: { a, _ in a })
        for i in current.rows.indices {
            guard let m = mine[current.rows[i].cells["id"] ?? ""] else { continue }
            for k in ["human_pass", "note", "label_source"] { current.rows[i].cells[k] = m.cells[k] ?? "" }
        }
        var head = current.header
        for k in ["human_pass", "note", "label_source"] where !head.contains(k) { head.append(k) }
        let lines = [head] + current.rows.map { r in head.map { r.cells[$0] ?? "" } }
        try CSV.write(lines).write(to: url, atomically: true, encoding: .utf8)
    }
}

enum CSV {
    static func parse(_ text: String) -> [[String]] {
        var rows: [[String]] = [], row: [String] = [], field = "", quoted = false
        var it = Array(text).makeIterator()
        var pending: Character? = nil
        func next() -> Character? { if let p = pending { pending = nil; return p }; return it.next() }
        while let c = next() {
            if quoted {
                if c == "\"" {
                    if let n = next() { if n == "\"" { field.append("\"") } else { quoted = false; pending = n } }
                    else { quoted = false }
                } else { field.append(c) }
            } else {
                switch c {
                case "\"": quoted = true
                case ",": row.append(field); field = ""
                case "\r\n", "\n", "\r": row.append(field); field = ""; rows.append(row); row = []
                default: field.append(c)
                }
            }
        }
        if !field.isEmpty || !row.isEmpty { row.append(field); rows.append(row) }
        return rows
    }

    static func write(_ rows: [[String]]) -> String {
        rows.map { $0.map { f in
            f.contains(where: { ",\"\n\r".contains($0) }) ? "\"" + f.replacingOccurrences(of: "\"", with: "\"\"") + "\"" : f
        }.joined(separator: ",") }.joined(separator: "\r\n") + "\r\n"
    }
}
