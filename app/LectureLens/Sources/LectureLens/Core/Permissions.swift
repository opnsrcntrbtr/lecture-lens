import AppKit
import AVFoundation
import ApplicationServices
import CoreGraphics

/// macOS attributes screenpipe's privacy checks to this app (it launches
/// screenpipe), so the app's own trust state *is* screenpipe's. Reading it here
/// catches a stale grant — Settings shows the toggle ON, but the grant was pinned
/// to a previous build's signature — before capture starts (ADR-004).
enum Permissions {
    static var accessibility: Bool { AXIsProcessTrusted() }
    static var screenRecording: Bool { CGPreflightScreenCaptureAccess() }
    /// screenpipe refuses to start while audio is enabled and the microphone isn't
    /// authorized — even when it records only app/system audio.
    static var microphone: AVAuthorizationStatus { AVCaptureDevice.authorizationStatus(for: .audio) }

    /// Shows the macOS "allow microphone" dialog (needs the audio-input entitlement
    /// under the hardened runtime; build_app.sh adds it).
    static func requestMicrophone() async -> Bool {
        await AVCaptureDevice.requestAccess(for: .audio)
    }

    static func openMicrophoneSettings() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!)
    }

    static func openAccessibilitySettings() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
    }
}
