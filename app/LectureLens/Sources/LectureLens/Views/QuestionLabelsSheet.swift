import SwiftUI

/// `lecture qlabels todo|label --json` (M-7): label a past class's questions as would-ask,
/// duplicate and answered-in-class. These labels are the baseline questions v2 must beat.
struct QuestionLabels: Decodable, Sendable {
    let ok: Bool
    let error: String?
    let total: Int?
    let labelled: Int?
    let todo: [Item]?
    let would_ask_share: Double?
    struct Item: Decodable, Sendable, Identifiable { let i: Int; let q: String; let answer: String?; let round: Int?; var id: Int { i } }

    static func labelArgs(day: String, i: Int, wouldAsk: Bool, duplicate: Bool, answered: Bool) -> [String] {
        ["qlabels", "label", "--json", "--day", day, "--i", String(i), "--would-ask", wouldAsk ? "y" : "n",
         "--duplicate", duplicate ? "y" : "n", "--answered", answered ? "y" : "n"]
    }
}

struct QuestionLabelsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var day = LiveData.pastDays().first ?? ""
    @State private var state: QuestionLabels?
    @State private var wouldAsk = true
    @State private var duplicate = false
    @State private var answered = false
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Question labels").font(.scaled(.headline))
                Picker("Class", selection: $day) {
                    ForEach(LiveData.pastDays(), id: \.self) { Text(LiveData.dayLabel($0)).tag($0) }
                }.frame(width: 200)
                if let s = state, let l = s.labelled, let t = s.total { Text("\(l) of \(t) labelled").font(.scaled(.caption)).foregroundStyle(.secondary) }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            Text("Label blind, from what you remember of the class. These set the baseline that questions v2 must beat.")
                .font(.scaled(.caption)).foregroundStyle(.secondary)
            Divider()
            if let e = state?.error { Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.orange) }
            if let item = state?.todo?.first {
                GroupBox("Question\(item.round.map { " · round \($0)" } ?? "")") {
                    Text(item.q).font(.scaled(.callout)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                }
                Toggle("I would ask this in class", isOn: $wouldAsk)
                Toggle("It repeats an earlier question", isOn: $duplicate)
                Toggle("The class already answered it", isOn: $answered)
                HStack {
                    Button("Save and next") {
                        Task {
                            busy = true; defer { busy = false }
                            state = try? await ToolRunner.json(QuestionLabels.self, "lecture",
                                QuestionLabels.labelArgs(day: day, i: item.i, wouldAsk: wouldAsk, duplicate: duplicate, answered: answered), timeout: 20)
                            wouldAsk = true; duplicate = false; answered = false
                        }
                    }.keyboardShortcut(.return, modifiers: []).disabled(busy)
                    if busy { ProgressView().controlSize(.small) }
                }
            } else if state != nil {
                Text("All questions for this class are labelled.").foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(18)
        .frame(minWidth: 560, idealWidth: 640, minHeight: 360, idealHeight: 420)
        .task(id: day) {
            guard !day.isEmpty else { return }
            state = try? await ToolRunner.json(QuestionLabels.self, "lecture", ["qlabels", "todo", "--json", "--day", day], timeout: 20)
        }
    }
}
