import SwiftUI

struct MenuBarContent: View {
    @Bindable var model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Lecture Lens").font(.headline)
                Spacer()
                Circle().fill(model.overall.color).frame(width: 10, height: 10)
            }
            ForEach(model.signals) { SignalRow(signal: $0) }
            Divider()
            HStack {
                if model.mode.running {
                    Button("Stop capture", systemImage: "stop.circle") { Task { await model.stopCapture() } }
                } else {
                    Button("Start capture", systemImage: "record.circle") { Task { await model.startCapture() } }
                        .keyboardShortcut("r")
                }
                Spacer()
                Picker("", selection: Binding(get: { model.mode.pinned?.isEmpty == false ? model.mode.pinned! : "auto" },
                                               set: { m in Task { await model.setMode(m) } })) {
                    Text("Auto").tag("auto"); Text("Portal").tag("portal"); Text("Live").tag("live")
                }
                .pickerStyle(.segmented).frame(width: 190)
                .disabled(!model.mode.running)
            }
            .disabled(model.busyCapture)
            if let err = model.lastError {
                Text(err).font(.caption).foregroundStyle(.red).lineLimit(3)
            }
            if model.jobs.busy, let j = model.jobs.jobs.first(where: { $0.running }) {
                HStack { ProgressView().controlSize(.small); Text(j.title).font(.caption) }
            }
            Divider()
            HStack {
                Button("Open Lecture Lens") {
                    openWindow(id: "main"); NSApp.activate()
                }
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }.keyboardShortcut("q")
            }
        }
        .padding(14)
        .frame(width: 380)
        .task { model.start() }
    }
}
