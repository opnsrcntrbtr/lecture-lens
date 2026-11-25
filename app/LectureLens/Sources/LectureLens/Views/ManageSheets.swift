import SwiftUI

// Course and lecture housekeeping: register a course from a Chrome tab or a pasted URL, rename a
// course or a lecture, and consolidate lectures into one (lecture courses / rename-lecture /
// merge-lectures).

/// `lecture courses tabs --json`: Chrome's open tabs (read only).
struct ChromeTabs: Decodable, Sendable {
    let ok: Bool
    let tabs: [Tab]?
    let error: String?
    struct Tab: Decodable, Sendable, Identifiable, Hashable {
        let title: String
        let url: String
        let active: Bool?
        let course: String?
        var id: String { url + "\u{1}" + title }
    }
}

/// `lecture courses add|rename --json`, `lecture rename-lecture --json`.
struct ManageResult: Decodable, Sendable {
    let ok: Bool
    let error: String?
    let course: String?
    let title: String?
}

enum CourseForm {
    /// The page's own name from a tab title ("Lesson 3 - Example Learn" -> "Lesson 3").
    static func name(fromTitle t: String) -> String {
        let seps = [" - ", " – ", " — ", " | ", " · "]
        var parts = [t]
        for s in seps where parts.count == 1 { parts = t.components(separatedBy: s) }
        let bits = parts.map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        return bits.count >= 2 ? bits.dropLast().joined(separator: " - ") : (bits.first ?? "")
    }

    static func validURL(_ s: String) -> Bool {
        guard let u = URL(string: s.trimmingCharacters(in: .whitespaces)), let scheme = u.scheme?.lowercased(),
              ["http", "https"].contains(scheme), let host = u.host, host.contains(".") else { return false }
        return true
    }

    static func args(name: String, url: String, title: String?, focus: String) -> [String] {
        var a = ["courses", "add", url.trimmingCharacters(in: .whitespaces), "--name", name.trimmingCharacters(in: .whitespaces), "--json"]
        if let t = title, !t.isEmpty { a += ["--title", t] }
        let f = focus.trimmingCharacters(in: .whitespaces)
        if !f.isEmpty { a += ["--focus", f] }
        return a
    }

    /// Folder name of a lecture: what the toolkit's find_folder accepts.
    static func lectureArg(_ s: Session) -> String { URL(fileURLWithPath: s.folder).lastPathComponent }
}

struct AddCourseSheet: View {
    @Bindable var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var tabs: [ChromeTabs.Tab] = []
    @State private var tabsNote: String?
    @State private var picked: ChromeTabs.Tab?
    @State private var name = ""
    @State private var url = ""
    @State private var focus = ""
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Add a course").font(.scaled(.title2)).bold()
            Text("Pick the course's page from your open Chrome tabs, or paste its web address. Capture then records that site, and its lectures, notes and review stay in their own course.")
                .font(.scaled(.callout)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            GroupBox("Open Chrome tabs") {
                if tabs.isEmpty {
                    Text(tabsNote ?? "Reading Chrome's tabs…").font(.scaled(.caption)).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(6)
                } else {
                    List(tabs, selection: Binding(get: { picked?.id }, set: { id in choose(tabs.first { $0.id == id }) })) { t in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(t.title.isEmpty ? t.url : t.title).lineLimit(1)
                                if t.active == true { Text("front tab").font(.scaled(.caption2)).foregroundStyle(.secondary) }
                                if let c = t.course { Text("already: \(c)").font(.scaled(.caption2)).foregroundStyle(.orange) }
                            }
                            Text(t.url).font(.scaled(.caption)).foregroundStyle(.secondary).lineLimit(1)
                        }
                        .tag(t.id)
                    }
                    .frame(minHeight: 160, maxHeight: 220)
                }
            }
            Form {
                TextField("Course name", text: $name)
                TextField("Course page URL", text: $url, prompt: Text("https://…"))
                TextField("What it's for (optional)", text: $focus, prompt: Text("e.g. building agentic AI solutions"))
                    .help("Notes and cards relate ideas to this")
            }
            if let e = error { Text(e).foregroundStyle(.red).font(.scaled(.callout)) }
            HStack {
                Button("Reload tabs") { Task { await loadTabs() } }
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Add course") { Task { await add() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(busy || name.trimmingCharacters(in: .whitespaces).isEmpty || !CourseForm.validURL(url))
            }
        }
        .padding(20)
        .frame(width: 560)
        .task { await loadTabs() }
    }

    private func choose(_ t: ChromeTabs.Tab?) {
        picked = t
        guard let t else { return }
        url = t.url
        name = CourseForm.name(fromTitle: t.title)
        error = t.course.map { "This site already belongs to the course “\($0)”." }
    }

    private func loadTabs() async {
        tabsNote = nil
        guard let r = try? await ToolRunner.json(ChromeTabs.self, "lecture", ["courses", "tabs", "--json"], timeout: 30) else {
            tabsNote = "Couldn't read Chrome's tabs. Paste the course URL below."; return
        }
        tabs = r.tabs ?? []
        if !r.ok { tabsNote = r.error ?? "Chrome didn't answer. Paste the course URL below." }
        else if tabs.isEmpty { tabsNote = "Chrome has no web pages open. Paste the course URL below." }
        if picked == nil, let front = tabs.first(where: { $0.active == true && $0.course == nil }) { choose(front) }
    }

    private func add() async {
        busy = true; defer { busy = false }
        let title = picked?.url == url.trimmingCharacters(in: .whitespaces) ? picked?.title : nil
        guard let r = try? await ToolRunner.json(ManageResult.self, "lecture", CourseForm.args(name: name, url: url, title: title, focus: focus), timeout: 30) else {
            error = "The course could not be added."; return
        }
        guard r.ok else { error = r.error ?? "The course could not be added."; return }
        await model.loadCourses()
        if let id = r.course, let c = model.courseList?.choices.first(where: { $0.id == id }) { model.selectCourse(c) }
        dismiss()
    }
}

struct RenameCourseSheet: View {
    @Bindable var model: AppModel
    let course: CourseList.Course
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var short = ""
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Rename course").font(.scaled(.title2)).bold()
            Text("Only the names change. The course keeps its folders, lectures and review history.")
                .font(.scaled(.callout)).foregroundStyle(.secondary)
            Form {
                TextField("Name", text: $name)
                TextField("Short name (sidebar, decks)", text: $short)
            }
            if let e = error { Text(e).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Rename") { Task { await save() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20).frame(width: 440)
        .onAppear { name = course.name; short = course.short ?? "" }
    }

    private func save() async {
        let r = try? await ToolRunner.json(ManageResult.self, "lecture",
                                           ["courses", "rename", course.id, "--name", name, "--short", short, "--json"], timeout: 30)
        guard let r, r.ok else { error = r?.error ?? "The course could not be renamed."; return }
        await model.loadCourses()
        dismiss()
    }
}

struct RenameLectureSheet: View {
    @Bindable var model: AppModel
    let session: Session
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Rename lecture").font(.scaled(.title2)).bold()
            Text("The new title goes everywhere: notes, transcript, slides, cards, the vault note and search. The lecture keeps its folder, so links, sync and review history still work.")
                .font(.scaled(.callout)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextField("Title", text: $title).textFieldStyle(.roundedBorder)
            if let e = error { Text(e).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Rename") { Task { await save() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(busy || title.trimmingCharacters(in: .whitespaces).isEmpty || title == session.displayTitle)
            }
        }
        .padding(20).frame(width: 480)
        .onAppear { title = session.displayTitle }
    }

    private func save() async {
        busy = true; defer { busy = false }
        let r = try? await ToolRunner.json(ManageResult.self, "lecture",
                                           ["rename-lecture", CourseForm.lectureArg(session), "--title", title, "--json"], timeout: 120)
        guard let r, r.ok else { error = r?.error ?? "The lecture could not be renamed."; return }
        await model.loadSessions()
        dismiss()
    }
}

struct ConsolidateSheet: View {
    @Bindable var model: AppModel
    let start: Session
    @Environment(\.dismiss) private var dismiss
    @State private var destination: String = ""
    @State private var sources: Set<String> = []

    private var built: [Session] { model.sessions.filter(\.built).sorted { ($0.start ?? "") < ($1.start ?? "") } }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Consolidate lectures").font(.scaled(.title2)).bold()
            Text("The chosen lectures join the destination on one timeline, in the order they were recorded: transcript, slides, Q&A, decks, resources and readings. Then notes and cards are rebuilt with the local model. The originals move to _superseded, so nothing is lost.")
                .font(.scaled(.callout)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Picker("Destination", selection: $destination) {
                ForEach(built) { s in Text(s.displayTitle).tag(s.key) }
            }
            GroupBox("Lectures to fold in") {
                List(built.filter { $0.key != destination }) { s in
                    Toggle(isOn: Binding(get: { sources.contains(s.key) },
                                         set: { on in if on { sources.insert(s.key) } else { sources.remove(s.key) } })) {
                        VStack(alignment: .leading) {
                            Text(s.displayTitle).lineLimit(1)
                            Text([s.start.map { String($0.prefix(16)).replacingOccurrences(of: "T", with: " ") }, s.minutes.map { "\(Int($0)) min" }]
                                .compactMap { $0 }.joined(separator: " · ")).font(.scaled(.caption)).foregroundStyle(.secondary)
                        }
                    }
                }
                .frame(minHeight: 180, maxHeight: 260)
            }
            HStack {
                Text(sources.isEmpty ? "Choose at least one lecture" : "\(sources.count) lecture(s) into “\(built.first { $0.key == destination }?.displayTitle ?? "")”")
                    .font(.scaled(.caption)).foregroundStyle(.secondary)
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Consolidate") { run() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(sources.isEmpty || destination.isEmpty || model.jobs.busy)
                    .help(model.jobs.busy ? "Another job is running" : "Runs as a background job; rebuilding notes uses the local model")
            }
        }
        .padding(20).frame(width: 560)
        .onAppear {
            // the lecture you started from goes into the latest other one by default
            destination = built.last { $0.key != start.key }?.key ?? start.key
            if start.key != destination { sources = [start.key] }
        }
        .onChange(of: destination) { _, d in sources.remove(d) }
    }

    private func run() {
        guard let dest = built.first(where: { $0.key == destination }) else { return }
        let from = built.filter { sources.contains($0.key) }
        model.consolidate(into: dest, from: from)
        dismiss()
    }
}
