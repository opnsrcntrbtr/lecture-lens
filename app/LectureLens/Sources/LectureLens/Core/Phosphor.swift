import AppKit
import SwiftUI

/// The app's green accent, in one place. The bright phosphor green reads well on dark backgrounds
/// (10:1) but is 1.6:1 on white, so light mode uses a deep green (5.3:1). Buttons that put white
/// text on the green use `fill` in both modes (5.3:1). WCAG 2.2 asks for 4.5:1 for text.
enum Phosphor {
    static let accent = Color(nsColor: NSColor(name: NSColor.Name("phosphorAccent")) { appearance in
        isDark(appearance)
            ? NSColor(srgbRed: 0, green: 229.0 / 255.0, blue: 163.0 / 255.0, alpha: 1)
            : NSColor(srgbRed: 0, green: 122.0 / 255.0, blue: 90.0 / 255.0, alpha: 1)
    })
    static let fill = Color(.sRGB, red: 0, green: 122.0 / 255.0, blue: 90.0 / 255.0, opacity: 1)

    nonisolated static func isDark(_ appearance: NSAppearance) -> Bool {
        appearance.bestMatch(from: [.aqua, .darkAqua, .accessibilityHighContrastAqua, .accessibilityHighContrastDarkAqua])
            .map { $0 == .darkAqua || $0 == .accessibilityHighContrastDarkAqua } ?? true
    }
}
