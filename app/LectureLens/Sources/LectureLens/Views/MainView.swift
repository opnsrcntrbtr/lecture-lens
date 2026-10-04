import SwiftUI

enum Screen: String, CaseIterable, Identifiable {
    case capture = "Capture", lectures = "Lectures", ask = "Ask", evals = "Evals", settings = "Settings"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .capture: "dot.radiowaves.left.and.right"
        case .lectures: "books.vertical"
        case .ask: "questionmark.bubble"
        case .evals: "checkmark.seal"
        case .settings: "gearshape"
        }
    }
}

struct MainView: View {
    @Bindable var model: AppModel
    @State private var screen: Screen? = .capture

    var body: some View {
        NavigationSplitView {
            List(Screen.allCases, selection: $screen) { s in
                Label(s.rawValue, systemImage: s.symbol).tag(s)
            }
            .navigationSplitViewColumnWidth(170)
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 6) {
                    Circle().fill(model.overall.color).frame(width: 8, height: 8)
                    Text(model.mode.running ? "Capturing · \(model.mode.mode)" : "Not capturing")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(10)
            }
        } detail: {
            switch screen ?? .capture {
            case .capture: CaptureView(model: model)
            case .lectures: LecturesView(model: model)
            case .ask: AskView(model: model)
            case .evals: EvalsView(model: model)
            case .settings: SettingsView(model: model)
            }
        }
    }
}
