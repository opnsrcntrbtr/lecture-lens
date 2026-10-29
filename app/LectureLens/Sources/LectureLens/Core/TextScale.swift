import SwiftUI

/// App-wide text size. macOS ignores SwiftUI's dynamicTypeSize, so every text style is
/// drawn at the system's base size for that style times a user scale (View > Bigger /
/// Smaller / Actual Size, ⌘+ ⌘− ⌘0, or Settings > Appearance). Changing the scale
/// rebuilds the window content (`.id(scale)` at the root) so every view picks it up.
enum TextScale {
    static let key = "textScale"
    static let steps: [Double] = [0.85, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0]
    static let `default` = 1.0

    static var current: Double {
        let v = UserDefaults.standard.double(forKey: key)
        return v == 0 ? `default` : min(max(v, steps.first!), steps.last!)
    }

    static func next(_ v: Double) -> Double { steps.first { $0 > v + 0.001 } ?? steps.last! }
    static func previous(_ v: Double) -> Double { steps.last { $0 < v - 0.001 } ?? steps.first! }
    static func label(_ v: Double) -> String { "\(Int((v * 100).rounded()))%" }

    /// macOS 15+ point sizes for the text styles (Apple HIG, macOS type sizes).
    static func base(_ style: Font.TextStyle) -> CGFloat {
        switch style {
        case .largeTitle: 26
        case .title: 22
        case .title2: 17
        case .title3: 15
        case .headline: 13
        case .subheadline: 11
        case .body: 13
        case .callout: 12
        case .footnote: 10
        case .caption: 10
        case .caption2: 10
        @unknown default: 13
        }
    }
}

extension Font {
    /// A text style at the user's text size. Headline keeps its semibold default.
    static func scaled(_ style: Font.TextStyle, weight: Font.Weight? = nil, design: Font.Design = .default) -> Font {
        let w = weight ?? (style == .headline ? .semibold : .regular)
        return .system(size: (TextScale.base(style) * TextScale.current).rounded(), weight: w, design: design)
    }
}

/// GroupBox at the user's text size: macOS draws a GroupBox title at a fixed small size.
struct ScaledGroupBoxStyle: GroupBoxStyle {
    func makeBody(configuration: Configuration) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            configuration.label
                .font(.scaled(.subheadline, weight: .semibold))
                .foregroundStyle(.secondary)
                .padding(.leading, 4)
            configuration.content
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 8))
        }
    }
}
