import AppKit
import SwiftUI

/// Lecture library (brief J2): every captured session, built or not, and the
/// notes / slides / transcript / cards the toolkit made from it.
struct LecturesView: View {
    @Bindable var model: AppModel
    @State private var selection: String? = nil
    @State private var filter = ""

    private var shown: [Session] {
        let s = model.sessions.sorted { ($0.start ?? "") > ($1.start ?? "") }
        guard !filter.isEmpty else { return s }
        return s.filter { $0.displayTitle.localizedCaseInsensitiveContains(filter) }
    }

    var body: some View {
        HSplitView {
            List(shown, selection: $selection) { s in
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(s.displayTitle).fontWeight(.medium).lineLimit(2)
                        Spacer()
                        if s.built { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).help("Built") }
                    }
                    Text([Self.day(s.start), s.minutes.map { "\(Int($0)) min" }, s.frames.map { "\($0) frames" }]
                        .compactMap { $0 }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary)
                }
                .padding(.vertical, 3)
                .tag(s.key)
            }
            .searchable(text: $filter, placement: .sidebar, prompt: "Filter lectures")
            .overlay {
                if model.sessions.isEmpty {
                    ContentUnavailableView(model.loadingSessions ? "Loading…" : "No lectures yet",
                                           systemImage: "books.vertical",
                                           description: Text("Capture a lecture on your course portal, Zoom or Meet, then it appears here."))
                }
            }
            .frame(minWidth: 260, idealWidth: 300, maxWidth: 380)

            Group {
                if let key = selection, let s = model.sessions.first(where: { $0.key == key }) {
                    LectureDetail(model: model, session: s).id(s.key)
                } else {
                    ContentUnavailableView("Select a lecture", systemImage: "book")
                }
            }
            .frame(minWidth: 480, maxWidth: .infinity, maxHeight: .infinity)
        }
        .navigationTitle("Lectures")
        .toolbar {
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.loadSessions() } }
                .disabled(model.loadingSessions)
        }
    }

    static func day(_ iso: String?) -> String? {
        guard let iso, let d = parseISO(iso) else { return nil }
        return d.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated).hour().minute())
    }
}

func parseISO(_ s: String) -> Date? {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = f.date(from: s) { return d }
    f.formatOptions = [.withInternetDateTime]
    return f.date(from: s)
}

private enum LectureTab: String, CaseIterable, Identifiable {
    case notes = "Notes", slides = "Slides", transcript = "Transcript", cards = "Cards"
    var id: String { rawValue }
}

struct LectureDetail: View {
    @Bindable var model: AppModel
    let session: Session
    @State private var tab: LectureTab = .notes
    @State private var rawManifest: [String: Any] = [:]

    private var folder: URL {
        session.folder.hasPrefix("/") ? URL(fileURLWithPath: session.folder)
                                      : Paths.lectures.appendingPathComponent(session.folder)
    }
    private var building: Bool {
        model.judgeLoaded || model.jobs.jobs.contains { $0.running && $0.title == "Build \(session.displayTitle)" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header.padding(16)
            Divider()
            if session.built {
                Picker("", selection: $tab) {
                    ForEach(LectureTab.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented).labelsHidden().padding(12)
                switch tab {
                case .notes: MarkdownFile(url: folder.appendingPathComponent("notes.md"),
                                          feedback: ("note", ["lecture": session.key]), model: manifestString("llm"),
                                          promptVersion: "notes-v1")
                case .slides: SlidesPane(folder: folder, lecture: session.key, model: manifestString("vl_model"))
                case .transcript: MarkdownFile(url: folder.appendingPathComponent("transcript.md"), feedback: nil,
                                               model: nil, promptVersion: nil)
                case .cards: CardsPane(url: folder.appendingPathComponent("cards.tsv"), lecture: session.key,
                                       model: manifestString("llm"))
                }
            } else {
                ContentUnavailableView {
                    Label("Not built yet", systemImage: "hammer")
                } description: {
                    Text("Build turns the capture into a transcript, described slides, study notes and flashcards. It uses the local models and takes a few minutes.")
                } actions: {
                    Button("Build now") { model.build(session) }.buttonStyle(.borderedProminent).disabled(building)
                }
            }
        }
        .task { loadManifest() }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(session.displayTitle).font(.title2.bold()).textSelection(.enabled)
                Text([LecturesView.day(session.start), session.minutes.map { "\(Int($0)) min" },
                      manifestInt("slides").map { "\($0) slides" }, manifestInt("transcript_segments").map { "\($0) transcript segments" },
                      manifestInt("cards").map { "\($0) cards" }].compactMap { $0 }.joined(separator: " · "))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if building { ProgressView().controlSize(.small) }
            if session.built {
                Button("Rebuild", systemImage: "arrow.triangle.2.circlepath") { model.build(session) }.disabled(building)
                Button("Show in Finder", systemImage: "folder") { NSWorkspace.shared.activateFileViewerSelecting([folder]) }
            }
        }
    }

    private func loadManifest() {
        let url = folder.appendingPathComponent("manifest.json")
        guard let d = try? Data(contentsOf: url) else { return }
        rawManifest = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] ?? [:]
    }
    private func manifestInt(_ k: String) -> Int? { (rawManifest[k] as? NSNumber)?.intValue }
    private func manifestString(_ k: String) -> String? { rawManifest[k] as? String }
}

/// A read-only markdown file with an optional feedback bar under it.
struct MarkdownFile: View {
    let url: URL
    let feedback: (target: String, ref: [String: String])?
    let model: String?
    let promptVersion: String?
    @State private var text: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ScrollView {
                Group {
                    if let text { MarkdownText(text) } else { Text("File not found: \(url.lastPathComponent)").foregroundStyle(.secondary) }
                }
                .frame(maxWidth: 760, alignment: .leading)
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            if let feedback, let text {
                Divider()
                FeedbackBar(target: feedback.target, ref: feedback.ref, input: url.lastPathComponent,
                            output: String(text.prefix(4000)), context: [], model: model, promptVersion: promptVersion)
                    .padding(12)
            }
        }
        .task(id: url) { text = try? String(contentsOf: url, encoding: .utf8) }
    }
}

/// Minimal block-level markdown: headings, bullets, quotes, paragraphs; inline
/// bold/italic/code/links via AttributedString. Enough for notes and transcripts
/// without a third-party renderer.
struct MarkdownText: View {
    let blocks: [String]
    init(_ text: String) { blocks = text.components(separatedBy: "\n") }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, raw in
                line(raw)
            }
        }
        .textSelection(.enabled)
    }

    @ViewBuilder private func line(_ raw: String) -> some View {
        let t = raw.trimmingCharacters(in: .whitespaces)
        if t.isEmpty {
            Spacer().frame(height: 2)
        } else if t.hasPrefix("### ") {
            Text(inline(String(t.dropFirst(4)))).font(.headline).padding(.top, 4)
        } else if t.hasPrefix("## ") {
            Text(inline(String(t.dropFirst(3)))).font(.title3.bold()).padding(.top, 8)
        } else if t.hasPrefix("# ") {
            Text(inline(String(t.dropFirst(2)))).font(.title2.bold())
        } else if t.hasPrefix("- ") || t.hasPrefix("* ") {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("•").foregroundStyle(.secondary)
                Text(inline(String(t.dropFirst(2))))
            }
            .padding(.leading, CGFloat(raw.prefix { $0 == " " }.count) * 6)
        } else if t.hasPrefix("> ") {
            Text(inline(String(t.dropFirst(2)))).foregroundStyle(.secondary)
                .padding(.leading, 10)
                .overlay(alignment: .leading) { Rectangle().fill(.quaternary).frame(width: 3) }
        } else if t.hasPrefix("![[") {
            EmptyView()   // Obsidian image embeds are shown on the Slides tab
        } else {
            Text(inline(t))
        }
    }

    private func inline(_ s: String) -> AttributedString {
        (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(s)
    }
}

/// Keyframes side by side with what the vision model said about each.
struct SlidesPane: View {
    let folder: URL
    let lecture: String
    let model: String?
    @State private var slides: [SlideItem] = []

    struct SlideItem: Identifiable {
        let id: Int
        let at: String
        let image: URL
        let text: String
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18) {
                if slides.isEmpty {
                    ContentUnavailableView("No slides captured", systemImage: "photo.on.rectangle",
                                           description: Text("Screen frames reach the library once Screen Recording is allowed for Lecture Lens."))
                }
                ForEach(slides) { s in
                    GroupBox {
                        HStack(alignment: .top, spacing: 14) {
                            AsyncSlide(url: s.image).frame(width: 320)
                            VStack(alignment: .leading, spacing: 8) {
                                Text("Slide \(s.id) · \(s.at)").font(.headline)
                                MarkdownText(s.text.isEmpty ? "_not described_" : s.text)
                                Spacer(minLength: 0)
                                FeedbackBar(target: "slide", ref: ["lecture": lecture, "slide": String(s.id)],
                                            input: s.image.lastPathComponent, output: s.text, context: [],
                                            model: model, promptVersion: "slide-v1")
                            }
                        }.padding(6)
                    }
                }
            }
            .padding(16)
        }
        .task(id: folder) { slides = Self.load(folder) }
    }

    /// slides.md is the source of truth for descriptions: "## N. at HH:MM:SS",
    /// "![[file.jpg]]", then description / on-screen text until the next heading.
    static func load(_ folder: URL) -> [SlideItem] {
        guard let md = try? String(contentsOf: folder.appendingPathComponent("slides.md"), encoding: .utf8) else { return [] }
        var out: [SlideItem] = []
        var n = 0, at = "", file = "", body: [String] = []
        func flush() {
            if n > 0, !file.isEmpty {
                out.append(SlideItem(id: n, at: at, image: folder.appendingPathComponent("slides/\(file)"),
                                     text: body.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)))
            }
            body = []; file = ""
        }
        for line in md.components(separatedBy: "\n") {
            if line.hasPrefix("## "), let dot = line.firstIndex(of: ".") {
                flush()
                n = Int(line.dropFirst(3)[..<dot].trimmingCharacters(in: .whitespaces)) ?? (n + 1)
                at = line.components(separatedBy: " at ").last ?? ""
            } else if line.hasPrefix("![["), line.hasSuffix("]]") {
                file = String(line.dropFirst(3).dropLast(2))
            } else if n > 0 {
                body.append(line)
            }
        }
        flush()
        return out
    }
}

struct AsyncSlide: View {
    let url: URL
    @State private var image: NSImage? = nil
    var body: some View {
        Group {
            if let image {
                Image(nsImage: image).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 6))
                    .onTapGesture(count: 2) { NSWorkspace.shared.open(url) }
                    .help("Double-click to open")
            } else {
                RoundedRectangle(cornerRadius: 6).fill(.quaternary).aspectRatio(16/9, contentMode: .fit)
            }
        }
        .task(id: url) {
            let u = url
            // Read bytes off the main actor; NSImage is not Sendable, so build it here.
            let data = await Task.detached { try? Data(contentsOf: u) }.value
            image = data.flatMap { NSImage(data: $0) }
        }
    }
}

/// Flashcards from cards.tsv (question \t answer \t tag), flip to reveal.
struct CardsPane: View {
    let url: URL
    let lecture: String
    let model: String?
    @State private var cards: [(q: String, a: String)] = []
    @State private var revealed: Set<Int> = []

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                if cards.isEmpty {
                    ContentUnavailableView("No cards", systemImage: "rectangle.on.rectangle")
                } else {
                    HStack {
                        Text("\(cards.count) cards").foregroundStyle(.secondary)
                        Spacer()
                        Button(revealed.count == cards.count ? "Hide all" : "Reveal all") {
                            revealed = revealed.count == cards.count ? [] : Set(cards.indices)
                        }
                        Button("Open for Anki import", systemImage: "square.and.arrow.up") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                    }
                }
                ForEach(Array(cards.enumerated()), id: \.offset) { i, c in
                    GroupBox {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(c.q).fontWeight(.medium).textSelection(.enabled)
                            if revealed.contains(i) {
                                Divider()
                                Text(c.a).textSelection(.enabled)
                                FeedbackBar(target: "card", ref: ["lecture": lecture, "card": String(i + 1)],
                                            input: c.q, output: c.a, context: [], model: model, promptVersion: "cards-v1")
                            } else {
                                Button("Show answer") { revealed.insert(i) }.buttonStyle(.link)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading).padding(4)
                    }
                }
            }
            .frame(maxWidth: 760).padding(16).frame(maxWidth: .infinity)
        }
        .task(id: url) {
            let text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
            cards = text.split(separator: "\n").compactMap { l in
                let p = l.components(separatedBy: "\t")
                return p.count >= 2 ? (p[0], p[1]) : nil
            }
        }
    }
}
