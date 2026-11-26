import SwiftUI

// Review: one recommended next step (`lecture next-step --json`, next_step.py), worked out in code from the
// study record: relearning due, then due cards, then the mastery check, then the weakest lecture's miss
// through the guided tutor.

struct NextSteps: Decodable, Sendable {
    struct Step: Decodable, Sendable, Hashable {
        let kind: String
        let title: String
        let detail: String
        let minutes: Double?
        let target: String
        let ref: String?
    }
    let ok: Bool
    let top: Step?
    let steps: [Step]
}

enum NextStepText {
    static func button(_ s: NextSteps.Step) -> String? {
        switch s.target {
        case "tutor": return "Work it out"
        default: return nil
        }
    }

    static func minutes(_ s: NextSteps.Step) -> String? {
        guard let m = s.minutes, m > 0 else { return nil }
        return m < 1 ? "under a minute" : "about \(Int(m.rounded())) min"
    }
}

struct NextStepCard: View {
    @Bindable var model: AppModel
    @State private var next: NextSteps?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Color.clear.frame(height: 0).accessibilityHidden(true)   // .task never runs on an empty view
            if let n = next, n.ok, let top = n.top {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Image(systemName: "arrow.forward.circle.fill").foregroundStyle(Phosphor.accent).accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Next step").font(.scaled(.caption, weight: .semibold)).foregroundStyle(.secondary)
                            Text(top.title).font(.scaled(.headline))
                            Text([top.detail, NextStepText.minutes(top)].compactMap { $0 }.joined(separator: " · "))
                                .font(.scaled(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer()
                        if let b = NextStepText.button(top) {
                            Button(b) { go(top) }.buttonStyle(.borderedProminent).tint(Phosphor.fill)
                                .accessibilityLabel("\(b): \(top.title)")
                        }
                    }
                    if n.steps.count > 1 {
                        HStack(spacing: 8) {
                            Text("Then:").font(.scaled(.caption)).foregroundStyle(.secondary)
                            ForEach(Array(n.steps.dropFirst()), id: \.self) { s in
                                Button(s.title) { go(s) }.buttonStyle(.link).font(.scaled(.caption))
                                    .disabled(NextStepText.button(s) == nil)
                            }
                        }
                    }
                }
                .padding(12)
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Phosphor.accent.opacity(0.45)))
                .accessibilityElement(children: .contain)
            }
        }
        .task(id: model.course?.id) {
            next = try? await ToolRunner.json(NextSteps.self, "lecture", ["next-step", "--json"], timeout: 60)
        }
    }

    private func go(_ s: NextSteps.Step) {
        switch s.target {
        case "tutor": if let r = s.ref { model.tutorOnItem(r) }   // review and mastery are on this screen
        default: break
        }
    }
}
