import Foundation
@preconcurrency import UserNotifications

/// Reminds the learner to start Live capture when a calendar class is about to start or has
/// started without it (14 Nov: capture began 54 minutes into the class). Capture itself is
/// never started from here: only the Capture screen's Start does that.
enum ClassReminder {
    enum Stage: String, Sendable { case soon, late }

    /// The class's start and end as dates, from the calendar's IST wall times.
    static func window(_ c: NextClass) -> (start: Date, end: Date)? {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm"
        f.timeZone = TimeZone(identifier: c.tz ?? "Asia/Kolkata") ?? TimeZone(identifier: "Asia/Kolkata")
        guard let s = f.date(from: "\(c.date) \(c.start)"), let e = f.date(from: "\(c.date) \(c.end)") else { return nil }
        return (s, e)
    }

    /// Which reminder is due now, if any: "soon" from 5 minutes before the start, "late"
    /// from 2 minutes after it until the class ends, each once, and none while Live capture runs.
    static func due(_ c: NextClass, now: Date, liveRunning: Bool, sent: Set<String>) -> Stage? {
        guard !liveRunning, let w = window(c) else { return nil }
        let late = now >= w.start.addingTimeInterval(120) && now < w.end
        let soon = now >= w.start.addingTimeInterval(-300) && now < w.start
        let stage: Stage? = late ? .late : (soon ? .soon : nil)
        guard let st = stage, !sent.contains(key(c, st)) else { return nil }
        return st
    }

    static func key(_ c: NextClass, _ s: Stage) -> String { "\(c.date)-\(c.start)-\(s.rawValue)" }

    static func text(_ c: NextClass, _ s: Stage) -> (title: String, body: String) {
        switch s {
        case .soon: return ("Class at \(c.start): start Live capture", c.title)
        case .late: return ("Live capture is not running", "\(c.title) started at \(c.start). Open Capture and press Start (Live).")
        }
    }

    /// Posts a local notification; does nothing outside an app bundle (swift run, tests).
    static func post(_ c: NextClass, _ s: Stage) {
        let t = text(c, s)
        notify(id: key(c, s), title: t.title, body: t.body)
    }

    /// A local notification; nothing outside an app bundle (swift run, tests).
    static func notify(id: String, title: String, body: String) {
        guard Bundle.main.bundleIdentifier != nil else { return }
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            center.add(UNNotificationRequest(identifier: id, content: content, trigger: nil))
        }
    }
}
