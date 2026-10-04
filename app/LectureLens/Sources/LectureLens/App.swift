import SwiftUI

@main
struct LectureLensApp: App {
    @State private var model: AppModel

    init() {
        let m = AppModel()
        _model = State(initialValue: m)
        // Poll from launch so the menu-bar light is live before any view appears.
        Task { @MainActor in m.start() }
    }

    var body: some Scene {
        Window("Lecture Lens", id: "main") {
            MainView(model: model)
                .frame(minWidth: 980, minHeight: 640)
        }
        .defaultSize(width: 1180, height: 760)
        .defaultLaunchBehavior(.presented)

        MenuBarExtra {
            MenuBarContent(model: model)
        } label: {
            Image(systemName: model.overall.menuSymbol)
        }
        .menuBarExtraStyle(.window)
    }
}

extension Signal.State {
    var color: Color {
        switch self {
        case .ok: .green
        case .warn: .yellow
        case .down: .red
        case .unknown: .gray
        }
    }
    var menuSymbol: String {
        switch self {
        case .ok: "graduationcap.fill"
        case .warn: "graduationcap"
        case .down: "exclamationmark.triangle"
        case .unknown: "graduationcap"
        }
    }
}

/// Status light + label + detail, used in the menu bar and on the Capture screen.
struct SignalRow: View {
    let signal: Signal
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle().fill(signal.state.color).frame(width: 9, height: 9)
                .accessibilityLabel("\(signal.label) \(String(describing: signal.state))")
            Text(signal.label).fontWeight(.medium).frame(width: 78, alignment: .leading)
            Text(signal.detail).foregroundStyle(.secondary).lineLimit(2).font(.callout)
            Spacer(minLength: 0)
        }
    }
}

/// ↑ / ↓ plus an optional correction — the whole feedback interaction (brief J4).
struct FeedbackBar: View {
    let target: String
    let ref: [String: String]
    let input: String
    let output: String
    let context: [String]
    let model: String?
    let promptVersion: String?
    @State private var rating: Int? = nil
    @State private var correction = ""
    @State private var saved = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Text("Was this right?").font(.caption).foregroundStyle(.secondary)
                Button { rate(1) } label: { Image(systemName: rating == 1 ? "hand.thumbsup.fill" : "hand.thumbsup") }
                    .keyboardShortcut(.upArrow, modifiers: [.command])
                    .help("Correct (⌘↑)")
                Button { rating = -1; saved = false } label: {
                    Image(systemName: rating == -1 ? "hand.thumbsdown.fill" : "hand.thumbsdown") }
                    .keyboardShortcut(.downArrow, modifiers: [.command])
                    .help("Wrong (⌘↓) — add what's right")
                if saved { Label("saved", systemImage: "checkmark").font(.caption).foregroundStyle(.green) }
                if let error { Text(error).font(.caption).foregroundStyle(.red) }
            }
            .buttonStyle(.borderless)
            if rating == -1 && !saved {
                HStack {
                    TextField("What's the correct answer? (optional)", text: $correction, axis: .vertical)
                        .textFieldStyle(.roundedBorder).lineLimit(1...4)
                    Button("Save") { rate(-1) }.keyboardShortcut(.return, modifiers: [.command])
                }
            }
        }
    }

    private func rate(_ r: Int) {
        rating = r
        do {
            try FeedbackStore.append(FeedbackRecord(
                target: target, ref: ref, input: input, output: output, context: context, rating: r,
                correction: correction.isEmpty ? nil : correction, model: model, prompt_version: promptVersion))
            saved = true; error = nil
        } catch { self.error = "not saved: \(error.localizedDescription)" }
    }
}
