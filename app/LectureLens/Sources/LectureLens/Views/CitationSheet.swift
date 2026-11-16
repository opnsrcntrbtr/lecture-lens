import AppKit
import SwiftUI

/// `lecture cite --json`: the public sources an answer names and the lecture moments near its
/// time that share its words, with the slide on screen then (cite.py).
struct Citation: Decodable, Sendable {
    let ok: Bool
    let error: String?
    let t: String?
    let sources: [Source]?
    let moments: [Moment]?
    let slide: Slide?
    /// Citations v3 (NG-2): each answer sentence checked against the evidence store.
    let answerId: String?
    let checker: Checker?
    let sentences: [Sentence]?
    struct Checker: Decodable, Sendable { let name: String; let version: String; let threshold: Double; let calibrated: Bool? }
    struct Sentence: Decodable, Sendable, Identifiable {
        let i: Int; let text: String; let status: String; let citations: [Cited]
        var id: Int { i }
    }
    struct Cited: Decodable, Sendable, Identifiable {
        let lineIds: [String]; let t0: Double?; let tAbs: String?; let source: String?; let quote: String
        let supportP: Double?; let slideThumb: String?
        var id: String { lineIds.joined(separator: ",") }
        enum CodingKeys: String, CodingKey {
            case lineIds = "line_ids", t0, tAbs = "t_abs", source, quote, supportP = "support_p", slideThumb = "slide_thumb"
        }
    }
    enum CodingKeys: String, CodingKey { case ok, error, t, sources, moments, slide, answerId = "answer_id", checker, sentences }
    struct Source: Decodable, Sendable, Identifiable { let id: String; let title: String; let fact: String?; let url: String?; let date: String? }
    struct Moment: Decodable, Sendable, Identifiable { let t: String; let text: String; let shared: [String]; var id: String { t + text } }
    struct Slide: Decodable, Sendable { let t: String; let title: String }

    /// Arguments for `lecture cite`, as an argv array (never a shell string).
    static func args(t: String, text: String, sources: [String], day: String?, answer: String? = nil) -> [String] {
        var a = ["cite", "--json", "--t", t, "--text", text]
        if let ans = answer, !ans.isEmpty { a += ["--answer", ans] }
        if !sources.isEmpty { a += ["--sources", sources.joined(separator: ",")] }
        if let d = day, !d.isEmpty { a += ["--day", d] }
        return a
    }
}

/// The call to action on a question or answer: opens its citations.
struct CitationButton: View {
    let title: String
    let t: String
    let text: String
    var sources: [String] = []
    var day: String? = nil
    var answer: String? = nil
    @State private var open = false

    var body: some View {
        Button { open = true } label: {
            Label(sources.isEmpty ? "Citations" : "Citations (\(sources.count))", systemImage: "quote.bubble")
        }
        .buttonStyle(.borderless).controlSize(.small).font(.scaled(.caption))
        .help("Sources this answer names and the lecture moments that support it")
        .sheet(isPresented: $open) { CitationSheet(title: title, t: t, text: text, sources: sources, day: day, answer: answer) }
    }
}

struct CitationSheet: View {
    let title: String
    let t: String
    let text: String
    let sources: [String]
    let day: String?
    var answer: String? = nil
    @Environment(\.dismiss) private var dismiss
    @State private var result: Citation?
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                Text(title).font(.scaled(.headline)).textSelection(.enabled)
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let e = error {
                        Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                    } else if let r = result {
                        CitationBody(result: r)
                    } else {
                        HStack { ProgressView().controlSize(.small); Text("Finding sources and lecture moments…").foregroundStyle(.secondary) }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(18)
        .frame(minWidth: 560, idealWidth: 640, minHeight: 360, idealHeight: 480)
        .task {
            do {
                let r = try await ToolRunner.json(Citation.self, "lecture", Citation.args(t: t, text: text, sources: sources, day: day, answer: answer), timeout: 60)
                if r.ok { result = r } else { error = r.error ?? "No citations" }
            } catch { self.error = "\(error)" }
        }
    }
}

/// Sources first, then the lecture: slide on screen and the matching transcript lines.
struct CitationBody: View {
    let result: Citation

    var body: some View {
        let srcs = result.sources ?? [], ms = result.moments ?? []
        VStack(alignment: .leading, spacing: 14) {
            if let ss = result.sentences, !ss.isEmpty {
                SentenceCheck(sentences: ss, calibrated: result.checker?.calibrated == true)
            }
            GroupBox("Public sources") {
                VStack(alignment: .leading, spacing: 10) {
                    if srcs.isEmpty { Text("This answer names no source from the evidence bank: it rests on the lecture alone.").foregroundStyle(.secondary) }
                    ForEach(srcs) { s in
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(s.id).font(.scaled(.caption, design: .monospaced)).foregroundStyle(.secondary)
                                Text(s.title).font(.scaled(.callout, weight: .semibold)).textSelection(.enabled)
                                Spacer()
                                if let u = s.url, let url = URL(string: u), url.scheme == "https" {
                                    Button("Open", systemImage: "arrow.up.right.square") { NSWorkspace.shared.open(url) }
                                        .buttonStyle(.borderless).controlSize(.small)
                                }
                            }
                            if let f = s.fact { Text(f).font(.scaled(.caption)).foregroundStyle(.secondary).textSelection(.enabled) }
                            if let d = s.date { Text(d).font(.scaled(.caption2)).foregroundStyle(.tertiary) }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            GroupBox("In the lecture") {
                VStack(alignment: .leading, spacing: 8) {
                    if let sl = result.slide {
                        Label("\(LiveData.clock(sl.t))  \(sl.title)", systemImage: "rectangle.on.rectangle")
                            .font(.scaled(.callout)).textSelection(.enabled)
                    }
                    if ms.isEmpty { Text("No transcript line near this time shares enough words with the answer.").foregroundStyle(.secondary) }
                    ForEach(ms) { m in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(LiveData.clock(m.t) + "  " + m.text).font(.scaled(.callout)).textSelection(.enabled)
                            Text("shares: " + m.shared.joined(separator: ", ")).font(.scaled(.caption2)).foregroundStyle(.tertiary)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Text("Matched by shared words within 10 minutes of the question; check the lecture before relying on a draft.")
                .font(.scaled(.caption2)).foregroundStyle(.tertiary)
        }
    }
}

/// Each answer sentence with its status and the lecture lines found for it. "Supported" is
/// shown only when the checker passed its calibration; otherwise lines are "related".
struct SentenceCheck: View {
    let sentences: [Citation.Sentence]
    let calibrated: Bool

    var body: some View {
        GroupBox("Sentence by sentence") {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(sentences) { s in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            CitationStatus.badge(s.status)
                            Text(s.text).font(.scaled(.callout)).textSelection(.enabled)
                        }
                        ForEach(s.citations.prefix(3)) { c in
                            HStack(alignment: .top, spacing: 6) {
                                if let p = c.slideThumb, let img = NSImage(contentsOfFile: p) {
                                    Image(nsImage: img).resizable().scaledToFit().frame(width: 72)
                                }
                                Text(CitationStatus.when(c) + "  " + c.quote)
                                    .font(.scaled(.caption)).foregroundStyle(.secondary).lineLimit(3).textSelection(.enabled)
                            }
                            .padding(.leading, 22)
                        }
                    }
                }
                Text(calibrated ? "Checked by a support checker that passed calibration on your labels."
                                : "Not verified: lines are related by words; label pairs in Evals to calibrate a checker.")
                    .font(.scaled(.caption2)).foregroundStyle(.tertiary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// Status labels and times for the sentence check, outside any view.
enum CitationStatus {
    static func label(_ status: String) -> (String, String) {
        switch status {
        case "supported": return ("Supported", "checkmark.seal")
        case "related": return ("Related", "link")
        default: return ("No support found", "questionmark.circle")
        }
    }
    @MainActor static func badge(_ status: String) -> some View {
        let (t, icon) = label(status)
        return Label(t, systemImage: icon).labelStyle(.iconOnly).help(t)
            .foregroundStyle(status == "supported" ? Color.green : status == "related" ? Color.secondary : Color.orange)
    }
    static func when(_ c: Citation.Cited) -> String {
        if let a = c.tAbs { return LiveData.clock(a) }
        if let t = c.t0 { let s = Int(t); return String(format: "%02d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60) }
        return c.source == "evidence" ? "source" : ""
    }
}

/// Plain text helpers, outside any view (views are main-actor isolated).
enum CitationText {
    /// The answer's sentences that cite source n: "[n]", "[n, m]" or "[m, n]".
    static func sentences(_ answer: String, citing n: Int) -> [String] {
        let parts = answer.replacingOccurrences(of: "\n", with: " ")
            .split(whereSeparator: { ".!?".contains($0) }).map { $0.trimmingCharacters(in: .whitespaces) }
        return parts.filter { p in
            p.ranges(of: /\[[\d,\s]+\]/).contains { r in
                p[r].dropFirst().dropLast().split(separator: ",").contains { Int($0.trimmingCharacters(in: .whitespaces)) == n }
            }
        }
    }
}

/// Ask screen: each numbered source with the answer sentences that cite it, and the lecture
/// moments around that source's time.
struct AskCitationSheet: View {
    let question: String
    let answer: String
    let sources: [AskResult.Source]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                Text(question).font(.scaled(.headline)).textSelection(.enabled)
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(sources) { s in
                        GroupBox {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("[\(s.n)] \(s.src) · \(s.where)").font(.scaled(.caption, weight: .semibold)).foregroundStyle(.secondary)
                                let cited = CitationText.sentences(answer, citing: s.n)
                                if cited.isEmpty { Text("Not cited in the answer text.").font(.scaled(.caption)).foregroundStyle(.tertiary) }
                                ForEach(cited, id: \.self) { c in Text("“\(c)”").font(.scaled(.callout)).italic().textSelection(.enabled) }
                                Text(s.text).font(.scaled(.caption)).foregroundStyle(.secondary).lineLimit(6).textSelection(.enabled)
                                DisclosureGroup("Lecture moments near this source") {
                                    AskSourceMoments(source: s).padding(.top, 4)
                                }
                                .font(.scaled(.caption))
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
            }
        }
        .padding(18)
        .frame(minWidth: 560, idealWidth: 680, minHeight: 360, idealHeight: 520)
    }
}

private struct AskSourceMoments: View {
    let source: AskResult.Source
    @State private var result: Citation?
    @State private var failed = false

    var body: some View {
        Group {
            if let r = result { CitationBody(result: r) }
            else if failed { Text("No lecture moments found for this source.").foregroundStyle(.secondary) }
            else { ProgressView().controlSize(.small) }
        }
        .task {
            let r = try? await ToolRunner.json(Citation.self, "lecture", Citation.args(t: source.ts, text: source.text, sources: [], day: nil), timeout: 60)
            if let r, r.ok { result = r } else { failed = true }
        }
    }
}
