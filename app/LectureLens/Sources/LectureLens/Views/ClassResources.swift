import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// What `lecture resources add <id> FILE... --json` prints: each file's kind (deck, agenda,
/// qa_sheet, reading) and a one-line summary, files skipped and why, and whether a rebuild
/// would use them.
struct ResourcesAddResult: Decodable, Sendable, Equatable {
    struct Added: Decodable, Sendable, Equatable {
        let file: String
        let kind: String
        let summary: String?
    }
    struct Skipped: Decodable, Sendable, Equatable {
        let file: String
        let reason: String
    }
    let lecture: String?
    let added: [Added]
    let skipped: [Skipped]
    let rebuild: Bool
}

/// The class's files posted on the portal after it (the deck, the agenda and pre-read, the
/// answered Q&A sheet, readings), added to a built lecture from the Lectures screen.
enum ClassResources {
    /// Files `lecture resources add` reads. Zoom's transcript and Q&A export (.txt, .vtt)
    /// still go through Import Zoom files.
    static let extensions = ["pptx", "pdf", "docx", "xlsx"]

    static func accepts(_ url: URL) -> Bool { extensions.contains(url.pathExtension.lowercased()) }

    static func args(lecture: String, files: [String]) -> [String] {
        ["resources", "add", lecture] + files + ["--json"]
    }

    static func kindLabel(_ kind: String) -> String {
        switch kind {
        case "deck": return "Slides"
        case "agenda": return "Agenda"
        case "qa_sheet": return "Q&A sheet"
        default: return "Reading"
        }
    }

    static func kindIcon(_ kind: String) -> String {
        switch kind {
        case "deck": return "rectangle.on.rectangle"
        case "agenda": return "list.bullet.rectangle"
        case "qa_sheet": return "questionmark.bubble"
        default: return "doc.text"
        }
    }

    @MainActor static func pick() -> [URL] {
        let p = NSOpenPanel()
        p.allowedContentTypes = extensions.compactMap { UTType(filenameExtension: $0) }
        p.allowsMultipleSelection = true
        p.canChooseDirectories = false
        p.prompt = "Add"
        p.message = "Choose the class's files from the portal: the deck (.pptx or .pdf), the agenda (.docx), the answered Q&A sheet (.xlsx) or readings. Names in a Q&A sheet are not copied."
        return p.runModal() == .OK ? p.urls : []
    }
}

/// Shown after adding: what each file became, and Rebuild to use them.
struct ClassResourcesBanner: View {
    let result: ResourcesAddResult
    let building: Bool
    let onRebuild: () -> Void
    let onDismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(result.added.isEmpty ? "No new class resources" : "Class resources added",
                      systemImage: "tray.full")
                    .font(.headline)
                Spacer()
                Button(action: onDismiss) { Image(systemName: "xmark") }
                    .buttonStyle(.borderless)
                    .help("Dismiss")
                    .accessibilityLabel("Dismiss class resources")
            }
            ForEach(result.added, id: \.file) { a in
                Label {
                    Text("\(ClassResources.kindLabel(a.kind)) · \(a.file)").fontWeight(.medium)
                        + Text(a.summary.map { " — \($0)" } ?? "").foregroundColor(.secondary)
                } icon: { Image(systemName: ClassResources.kindIcon(a.kind)) }
                .font(.callout)
            }
            ForEach(result.skipped, id: \.file) { s in
                Label("\(s.file) — \(s.reason)", systemImage: "minus.circle")
                    .font(.callout).foregroundStyle(.secondary)
            }
            if result.rebuild {
                HStack {
                    Text("Rebuild to use them in the notes, cards and Q&A.").font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    Button("Rebuild now", systemImage: "arrow.triangle.2.circlepath", action: onRebuild)
                        .buttonStyle(.borderedProminent)
                        .disabled(building)
                        .help("Rebuild notes, cards and Q&A with the added deck, agenda and Q&A sheet")
                }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.accentColor.opacity(0.07)))
        .accessibilityIdentifier("classResourcesBanner")
    }
}
