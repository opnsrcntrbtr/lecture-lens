import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Lecture library (brief J2): every captured session, built or not, and the
/// notes / slides / transcript / cards the toolkit made from it.
struct LecturesView: View {
    @Bindable var model: AppModel
    @State private var selection: String? = nil
    @State private var filter = ""
    @State private var showNames = false
    @State private var renamingLecture: Session?
    @State private var consolidating: Session?

    private var shown: [Session] {
        let s = model.sessions.sorted { ($0.start ?? "") > ($1.start ?? "") }
        guard !filter.isEmpty else { return s }
        return s.filter { $0.displayTitle.localizedCaseInsensitiveContains(filter) }
    }

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
            TextField("Filter lectures", text: $filter)
                .textFieldStyle(.roundedBorder).padding(8)
                .accessibilityLabel("Filter lectures by title")
            List(shown, selection: $selection) { s in
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(s.displayTitle).fontWeight(.medium).lineLimit(2)
                        Spacer()
                        if s.built { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).help("Built") }
                    }
                    Text([Self.day(s.start), s.minutes.map { "\(Int($0)) min" }, s.frames.map { "\($0) frames" }]
                        .compactMap { $0 }.joined(separator: " · "))
                        .font(.scaled(.caption)).foregroundStyle(.secondary)
                }
                .padding(.vertical, 3)
                .tag(s.key)
                .contextMenu {
                    Button("Rename…", systemImage: "pencil") { renamingLecture = s }
                        .disabled(!s.built)
                    Button("Consolidate into another lecture…", systemImage: "arrow.triangle.merge") { consolidating = s }
                        .disabled(!s.built || model.sessions.filter(\.built).count < 2)
                }
            }
            .overlay {
                if model.sessions.isEmpty, let err = model.sessionsError, !model.loadingSessions {
                    ContentUnavailableView {
                        Label("Couldn't load lectures", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(err)
                    } actions: {
                        Button("Try again") { Task { await model.loadSessions() } }
                    }
                } else if model.sessions.isEmpty {
                    ContentUnavailableView(model.loadingSessions ? "Loading…" : "No lectures yet",
                                           systemImage: "books.vertical",
                                           description: Text(LectureText.emptyLibrary(model.course)))
                }
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
            Button("Names review", systemImage: "person.crop.circle.badge.xmark") { showNames = true }
                .help("Find attendee names in stored lectures and remove them")
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.loadSessions() } }
                .disabled(model.loadingSessions)
        }
        .sheet(isPresented: $showNames) { NamesReviewSheet(model: model) }
        .sheet(item: $renamingLecture) { s in RenameLectureSheet(model: model, session: s) }
        .sheet(item: $consolidating) { s in ConsolidateSheet(model: model, start: s) }
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

enum LectureTab: String, CaseIterable, Identifiable {
    case notes = "Notes", slides = "Slides", transcript = "Transcript", qa = "Q&A", cards = "Cards", cornell = "Cornell", notesV2 = "Notes v2", practice = "Practice"
    var id: String { rawValue }

    /// Class Q&A only exists for live classes; a self-paced lesson has no tab for it.
    static func shown(selfPaced: Bool) -> [LectureTab] {
        selfPaced ? allCases.filter { $0 != .qa } : allCases
    }
}

struct LectureDetail: View {
    @Bindable var model: AppModel
    let session: Session
    @State private var tab: LectureTab = .notes
    @State private var confirmRebuild = false
    @State private var recalling = false   // Recall first: the notes stay hidden until you are done
    @State private var rawManifest: [String: Any] = [:]
    @State private var dropTargeted = false
    @State private var resourcesResult: ResourcesAddResult?
    @State private var resourcesError: String?
    @State private var addingResources = false

    private var folder: URL {
        session.folder.hasPrefix("/") ? URL(fileURLWithPath: session.folder)
                                      : Paths.lectures.appendingPathComponent(session.folder)
    }
    private var building: Bool {
        model.judgeLoaded || model.jobs.jobs.contains { $0.running && ($0.title == "Build \(session.displayTitle)" || $0.title == "After class \(session.displayTitle)") }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header.padding(16)
            if let r = resourcesResult {
                ClassResourcesBanner(result: r, building: building,
                                     onRebuild: { model.build(session); resourcesResult = nil },
                                     onDismiss: { resourcesResult = nil })
                    .padding(.horizontal, 16).padding(.bottom, 12)
            } else if let e = resourcesError {
                Label(e, systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
                    .padding(.horizontal, 16).padding(.bottom, 12)
            }
            Divider()
            if session.built {
                Picker("", selection: $tab) {
                    ForEach(LectureTab.shown(selfPaced: model.course?.selfPaced == true)) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented).labelsHidden().padding(12)
                switch tab {
                case .notes:
                    VStack(spacing: 0) {
                        RecallFirstBanner(model: model, lecture: folder.lastPathComponent, practising: $recalling)
                        if recalling { Spacer() } else {
                            MarkdownFile(url: folder.appendingPathComponent("notes.md"),
                                         feedback: ("note", ["lecture": session.key]), model: manifestString("llm"),
                                         promptVersion: "notes-v1")
                        }
                    }
                case .slides: SlidesPane(folder: folder, lecture: session.key, model: manifestString("vl_model"))
                case .transcript:
                    // Zoom's own transcript merged in, once imported; else this Mac's capture.
                    let merged = folder.appendingPathComponent("transcript_merged.md")
                    MarkdownFile(url: FileManager.default.fileExists(atPath: merged.path) ? merged
                                      : folder.appendingPathComponent("transcript.md"),
                                 feedback: nil, model: nil, promptVersion: nil)
                case .qa: MarkdownFile(url: folder.appendingPathComponent("qa.md"), feedback: nil,
                                       model: nil, promptVersion: nil)
                case .cards: CardsPane(url: folder.appendingPathComponent("cards.tsv"), lecture: session.key,
                                       model: manifestString("llm"))
                case .cornell: CornellPane(lecture: folder.lastPathComponent)
                case .notesV2: NotesV2Pane(folder: folder)
                case .practice: ScrollView { PracticeBox(model: model, fixedLecture: folder.lastPathComponent).padding(12) }
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
        .overlay {
            if dropTargeted {
                RoundedRectangle(cornerRadius: 12).strokeBorder(Color.accentColor, style: StrokeStyle(lineWidth: 2, dash: [6]))
                    .background(Color.accentColor.opacity(0.06))
                    .overlay(Label("Drop the deck, agenda, Q&A sheet or Zoom's transcript", systemImage: "tray.and.arrow.down")
                        .font(.scaled(.title3, weight: .semibold)))
                    .padding(8).allowsHitTesting(false)
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard session.built else { return false }
            let resources = urls.filter(ClassResources.accepts)
            if !resources.isEmpty { addResources(resources) }
            let fm = FileManager.default
            var copied = 0
            for u in urls where ["md", "txt", "vtt"].contains(u.pathExtension.lowercased()) {
                let dest = folder.appendingPathComponent(u.lastPathComponent)
                if !fm.fileExists(atPath: dest.path), (try? fm.copyItem(at: u, to: dest)) != nil { copied += 1 }
            }
            if copied > 0 { model.ingest(folder.lastPathComponent) }
            return copied > 0 || !resources.isEmpty
        } isTargeted: { dropTargeted = $0 }
        .task { loadManifest() }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(session.displayTitle).font(.scaled(.title2, weight: .bold)).textSelection(.enabled)
                Text([LecturesView.day(session.start), session.minutes.map { "\(Int($0)) min" },
                      manifestInt("slides").map { "\($0) slides" }, manifestInt("transcript_segments").map { "\($0) transcript segments" },
                      manifestInt("cards").map { "\($0) cards" }].compactMap { $0 }.joined(separator: " · "))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if building { ProgressView().controlSize(.small) }
            if model.jobs.jobs.contains(where: { $0.running && $0.title == "Import Zoom files" }) {
                Label("Importing Zoom files…", systemImage: "tray.and.arrow.down")
                    .font(.scaled(.callout)).foregroundStyle(.secondary)
                    .help(model.autoImportNote ?? "Transcript merge and word error rate, Q&A import, answer scores")
            }
            studioControls
            GapChips(gaps: rawManifest["gaps"] as? [[String: Any]] ?? [],
                     filled: rawManifest["gap_filled"] as? [[String: Any]] ?? []) { gap in
                fillGap(gap)
            }
            AfterClassChips(url: folder.appendingPathComponent("after_class.json"))
            if model.course?.selfPaced != true {   // live classes only: Zoom files, names check, class Q&A
            Button("After class", systemImage: "checkmark.seal") { model.afterClass(session) }
                .disabled(building || model.mode.running)
                .help(model.mode.running ? "Stop capture first" : "Import Zoom files, build, check attendee names are gone, rewrite qa.md")
            }
            if session.built {
                Button("Rebuild", systemImage: "arrow.triangle.2.circlepath") {
                    if session.built { confirmRebuild = true } else { model.build(session) }
                }
                    .disabled(building)
                    .confirmationDialog("Rebuild this lecture?", isPresented: $confirmRebuild, titleVisibility: .visible) {
                        Button("Rebuild") { model.build(session) }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text(LectureText.rebuildWarning(rawManifest))
                    }
                Button("Add resources…", systemImage: "doc.badge.plus") { addResources(ClassResources.pick()) }
                    .buttonStyle(.bordered)
                    .controlSize(.regular)
                    .disabled(building || addingResources || !session.built)
                    .help("Add the class's files from the portal: deck (.pptx/.pdf), agenda (.docx), answered Q&A sheet (.xlsx), readings. You can also drop them here.")
                    .accessibilityIdentifier("addClassResources")

                if model.course?.selfPaced != true {
                Button("Import Zoom files", systemImage: "tray.and.arrow.down") { model.ingest(folder.lastPathComponent) }
                    .disabled(building)
                    .help("Use Zoom's transcript, Q&A export or the deck saved in this class's folder or today's live folder")
                }
                Button("Show in Finder", systemImage: "folder") { NSWorkspace.shared.activateFileViewerSelecting([folder]) }
            }
        }
    }

    /// Browser mode (Lecture Lens Studio): where this lecture is, and the next step.
    @ViewBuilder private var studioControls: some View {
        switch Studio.state(folder: folder) {
        case .none:
            EmptyView()
        case .pending:
            Label("Waiting for Lecture Lens Studio", systemImage: "globe").font(.scaled(.callout)).foregroundStyle(.secondary)
            Button("Open Studio", systemImage: "globe") { Studio.open() }
                .help("Run this lecture's notes, cards and slide descriptions in Chrome")
        case .done(let engine):
            Label("Done in the Studio (\(engine))", systemImage: "checkmark.circle").font(.scaled(.callout)).foregroundStyle(.secondary)
            Button("Finish", systemImage: "square.and.arrow.down") { model.finishStudio(folder.lastPathComponent) }
                .buttonStyle(.borderedProminent).disabled(building)
                .help("Write the vault note and update the lecture from the Studio's notes and cards (lecture finish)")
        case .finished(let engine):
            Label("Notes by \(engine) in Chrome", systemImage: "globe").font(.scaled(.callout)).foregroundStyle(.secondary)
        }
    }

    /// CT-6: pick the class recording (its audio is used) and fill this gap from it.
    private func fillGap(_ gap: [String: Any]) {
        let p = NSOpenPanel()
        p.allowedContentTypes = [.audio, .movie, .mpeg4Movie, .mpeg4Audio]
        p.allowsMultipleSelection = false
        p.message = "Choose the class recording. Only its audio is used; existing transcript lines are never changed."
        guard p.runModal() == .OK, let url = p.url,
              let from = GapChips.hhmm(gap["from"] as? String), let to = GapChips.hhmm(gap["to"] as? String) else { return }
        model.gapfill(folder.lastPathComponent, audio: url.path, from: from, to: to) { loadManifest() }
    }

    /// Class resources from the portal (`lecture resources add`): the result banner offers Rebuild.
    private func addResources(_ urls: [URL]) {
        let files = urls.filter(ClassResources.accepts).map(\.path)
        guard !files.isEmpty, !addingResources else { return }
        let targetId = folder.lastPathComponent
        addingResources = true
        resourcesError = nil
        Task {
            defer { addingResources = false }
            do {
                let r = try await ToolRunner.json(ResourcesAddResult.self, "lecture",
                                                  ClassResources.args(lecture: targetId, files: files), timeout: 300)
                guard targetId == folder.lastPathComponent else { return }
                resourcesResult = r
            } catch {
                guard targetId == folder.lastPathComponent else { return }
                resourcesError = "Couldn't add the files: \(error.localizedDescription)"
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
            Text(inline(String(t.dropFirst(4)))).font(.scaled(.headline)).padding(.top, 4)
        } else if t.hasPrefix("## ") {
            Text(inline(String(t.dropFirst(3)))).font(.scaled(.title3, weight: .bold)).padding(.top, 8)
        } else if t.hasPrefix("# ") {
            Text(inline(String(t.dropFirst(2)))).font(.scaled(.title2, weight: .bold))
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
                                Text("Slide \(s.id) · \(s.at)").font(.scaled(.headline))
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
                    .accessibilityLabel("Slide image")
                    .accessibilityAddTraits(.isButton)
                    .accessibilityAction { NSWorkspace.shared.open(url) }   // VoiceOver cannot double-click
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


/// One chip per After-class step from after_class.json: green when it worked, orange with the reason when not.
struct AfterClassChips: View {
    let url: URL
    var body: some View {
        if let data = try? Data(contentsOf: url), let r = try? JSONDecoder().decode(AfterClassReport.self, from: data) {
            HStack(spacing: 4) {
                ForEach(r.steps) { s in
                    Label(s.name, systemImage: s.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .font(.scaled(.caption2)).foregroundStyle(s.ok ? Color.green : Color.orange)
                        .help(s.note)
                }
                if r.needs_review == true, let g = r.gates {
                    Label("needs review", systemImage: "flag.fill")
                        .font(.scaled(.caption2)).foregroundStyle(.orange)
                        .help(g.filter { !$0.ok }.map { "\($0.name): \($0.value?.description ?? "–") (limit \($0.threshold?.description ?? "–"))" }
                            .joined(separator: "\n"))
                }
            }
        }
    }
}


/// CT-6: a "Missing 10:00–10:54" chip per unfilled gap in the ledger, with "Fill from
/// recording…"; a filled gap shows how many of its lines have an approximate time.
struct GapChips: View {
    let gaps: [[String: Any]]
    let filled: [[String: Any]]
    let fill: ([String: Any]) -> Void

    static func hhmm(_ iso: String?) -> String? {
        guard let iso, let d = ISO8601DateFormatter().date(from: iso) else { return nil }
        let f = DateFormatter(); f.dateFormat = "HH:mm"; f.timeZone = .current
        return f.string(from: d)
    }

    var body: some View {
        HStack(spacing: 4) {
            ForEach(Array(gaps.enumerated()), id: \.offset) { _, g in
                let span = "\(Self.hhmm(g["from"] as? String) ?? "?")–\(Self.hhmm(g["to"] as? String) ?? "?")"
                if (g["filled"] as? Bool) == true {
                    let f = filled.first { ($0["from"] as? String) == (g["from"] as? String) } ?? [:]
                    let approx = (f["approx_lines"] as? NSNumber)?.intValue ?? 0
                    Label("Filled \(span)", systemImage: "checkmark.circle")
                        .font(.scaled(.caption2)).foregroundStyle(.green)
                        .help("From the recording (\(f["file"] as? String ?? "audio")): \((f["lines"] as? NSNumber)?.intValue ?? 0) lines, \(approx) with an approximate time. Rebuild to use them in the notes.")
                } else {
                    Menu {
                        Button("Fill from recording…") { fill(g) }
                    } label: {
                        Label("Missing \(span)", systemImage: "exclamationmark.circle")
                    }
                    .menuStyle(.borderlessButton).fixedSize()
                    .font(.scaled(.caption2)).foregroundStyle(.orange)
                    .help("No transcript for this span (\(g["cause"] as? String ?? "unknown")). Fill it from the class recording's audio.")
                }
            }
        }
    }
}


/// Wording for the lecture library that depends on the course or the lecture.
enum LectureText {
    static func emptyLibrary(_ c: CourseList.Course?) -> String {
        if let c, let sites = c.sites, !sites.isEmpty {
            return "Watch a lesson on \(sites.joined(separator: ", ")) while capture runs; it appears here, then build it."
        }
        return "Capture a lecture (your course portal, Zoom or Meet) and it appears here."
    }

    /// What a rebuild replaces, from the lecture's manifest.
    static func rebuildWarning(_ man: [String: Any]) -> String {
        var s = "Notes, cards and the concept map are made again with the local model (a few minutes); the current ones are replaced."
        if man["consolidated"] != nil {
            s += " This lecture was consolidated, so it is rebuilt from its merged files."
        } else if (man["transcript_source"] as? String) == "merged" {
            s += " Its transcript was merged from another recording; the rebuild uses that merged transcript again."
        }
        return s
    }
}

/// Retrieval before rereading: once a day per lecture, opening its notes offers two minutes of
/// recall from memory first (the attempt-first recap), since retrieval practice beats rereading.
enum RecallFirst {
    static let enabledKey = "recallFirstEnabled"
    static func key(_ lecture: String) -> String { "recallFirst." + lecture }
    static var enabled: Bool { UserDefaults.standard.object(forKey: enabledKey) as? Bool ?? true }

    static func due(_ lecture: String, now: Date = .now, defaults: UserDefaults = .standard) -> Bool {
        guard defaults.object(forKey: enabledKey) as? Bool ?? true else { return false }
        guard let last = defaults.object(forKey: key(lecture)) as? Date else { return true }
        return !Calendar.current.isDate(last, inSameDayAs: now)
    }

    static func markDone(_ lecture: String, now: Date = .now, defaults: UserDefaults = .standard) {
        defaults.set(now, forKey: key(lecture))
    }
}

struct RecallFirstBanner: View {
    @Bindable var model: AppModel
    let lecture: String
    // decided when the view is made: an empty Group never gets onAppear
    @State private var due: Bool
    @Binding var practising: Bool

    init(model: AppModel, lecture: String, practising: Binding<Bool>) {
        self.model = model
        self.lecture = lecture
        _practising = practising
        _due = State(initialValue: RecallFirst.due(lecture))
    }

    var body: some View {
        Group {
            if practising {
                VStack(alignment: .leading, spacing: 6) {
                    PracticeBox(model: model, fixedLecture: lecture, autoStart: "recap")
                    Button("Done, show the notes") { practising = false; due = false }
                        .buttonStyle(.bordered).controlSize(.small)
                        .keyboardShortcut(.escape, modifiers: [])
                }
                .padding(10)
            } else if due {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Image(systemName: "brain.head.profile").foregroundStyle(.tint).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Recall first").font(.scaled(.callout)).bold()
                        Text("Two minutes from memory before you reread: retrieving what you know holds it far better than reading it again.")
                            .font(.scaled(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    Button("Start recall") { RecallFirst.markDone(lecture); practising = true }
                        .buttonStyle(.borderedProminent).controlSize(.small)
                    Button("Not now") { RecallFirst.markDone(lecture); due = false }
                        .buttonStyle(.bordered).controlSize(.small)
                        .help("Hide until tomorrow. Turn it off for good with Don't offer again.")
                    Button("Don't offer again") { UserDefaults.standard.set(false, forKey: RecallFirst.enabledKey); due = false }
                        .buttonStyle(.borderless).controlSize(.small)
                }
                .padding(10)
                .background(.tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                .padding(.horizontal, 12).padding(.top, 8)
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Recall first: practise from memory before reading the notes")
            }
        }
    }
}
