import SwiftUI

enum Screen: String, CaseIterable, Identifiable {
    case capture = "Capture", live = "Live", lectures = "Lectures", review = "Review", ask = "Ask", capstone = "Capstone", evals = "Evals", settings = "Settings"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .capture: "dot.radiowaves.left.and.right"
        case .live: "bubble.left.and.text.bubble.right"
        case .lectures: "books.vertical"
        case .review: "rectangle.stack"
        case .ask: "questionmark.bubble"
        case .capstone: "flag.checkered"
        case .evals: "checkmark.seal"
        case .settings: "gearshape"
        }
    }
}

struct MainView: View {
    @Bindable var model: AppModel

    var body: some View {
        NavigationSplitView {
            List(Screen.allCases, selection: $model.screen) { s in
                Label(s.rawValue, systemImage: s.symbol).font(.scaled(.body)).tag(s)
            }
            .safeAreaInset(edge: .top) {
                if let list = model.courseList {
                    CoursePicker(model: model, courses: list.choices)
                        .padding(.horizontal, 10).padding(.top, 6)
                }
            }
            .navigationSplitViewColumnWidth(min: 150, ideal: 150 + 40 * (TextScale.current - 1) * 2, max: 320)
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 6) {
                    Circle().fill(model.overall.color).frame(width: 8, height: 8)
                    Text(model.mode.running ? "Capturing · \(model.mode.mode)" : "Not capturing")
                        .font(.scaled(.caption)).foregroundStyle(.secondary)
                }.padding(10)
            }
        } detail: {
            Group {
                switch model.screen ?? .capture {
                case .capture: CaptureView(model: model)
                case .live: LiveView(model: model)
                case .lectures: LecturesView(model: model)
                case .review: ReviewView(model: model)
                case .ask: AskView(model: model)
                case .capstone: CapstoneView(model: model)
                case .evals: EvalsView(model: model)
                case .settings: SettingsView(model: model)
                }
            }
            .id(model.course?.id ?? "")     // a course switch rebuilds the screen with that course's data
        }
    }
}

/// Which course the app shows. Capture is shared; each course keeps its own lectures,
/// study aids, review, Ask, vault folder and NotebookLM folder.
struct CoursePicker: View {
    @Bindable var model: AppModel
    let courses: [CourseList.Course]
    @State private var adding = false
    @State private var renaming: CourseList.Course?

    var body: some View {
        Menu {
            ForEach(courses) { c in
                Button {
                    model.selectCourse(c)
                } label: {
                    if c.id == model.course?.id { Label(c.name, systemImage: "checkmark") } else { Text(c.name) }
                }
            }
            if !courses.isEmpty { Divider() }
            Button("Add course…", systemImage: "plus") { adding = true }
            Button("Rename course…", systemImage: "pencil") { renaming = model.course }
                .disabled(model.course == nil)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: model.course?.selfPaced == true ? "play.rectangle" : "person.3")
                    .accessibilityHidden(true)
                Text(model.course?.label ?? "Courses").font(.scaled(.callout)).lineLimit(1).truncationMode(.middle)
            }
        }
        .menuStyle(.borderlessButton)
        .help(model.course.map { "Showing \($0.name). Capture records every course's sites; switch to see another course's lectures." } ?? "Pick a course")
        .accessibilityLabel("Course: \(model.course?.name ?? "none")")
        .accessibilityHint("Shows another course's lectures, study aids and review")
        .accessibilityIdentifier("course.picker")
        .sheet(isPresented: $adding) { AddCourseSheet(model: model) }
        .sheet(item: $renaming) { c in RenameCourseSheet(model: model, course: c) }
    }
}
