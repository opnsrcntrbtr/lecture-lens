import SwiftUI

enum Screen: String, CaseIterable, Identifiable {
    case capture = "Capture", live = "Live", lectures = "Lectures", ask = "Ask", evals = "Evals", settings = "Settings"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .capture: "dot.radiowaves.left.and.right"
        case .live: "bubble.left.and.text.bubble.right"
        case .lectures: "books.vertical"
        case .ask: "questionmark.bubble"
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
            .navigationSplitViewColumnWidth(min: 150, ideal: 150 + 40 * (TextScale.current - 1) * 2, max: 320)
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 6) {
                    Circle().fill(model.overall.color).frame(width: 8, height: 8)
                    Text(model.mode.running ? "Capturing · \(model.mode.mode)" : "Not capturing")
                        .font(.scaled(.caption)).foregroundStyle(.secondary)
                }.padding(10)
            }
        } detail: {
            switch model.screen ?? .capture {
            case .capture: CaptureView(model: model)
            case .live: LiveView()
            case .lectures: LecturesView(model: model)
            case .ask: AskView(model: model)
            case .evals: EvalsView(model: model)
            case .settings: SettingsView(model: model)
            }
        }
    }
}
