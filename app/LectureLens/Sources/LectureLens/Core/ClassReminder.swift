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

    /// Which reminder is due now, if any: "soon" once from 5 minutes before the start; "late"
    /// from 1 minute after it, every minute for 10 minutes (CT-1), while capture is not running.
    static func due(_ c: NextClass, now: Date, liveRunning: Bool, sent: Set<String>) -> Stage? {
        guard !liveRunning, let w = window(c) else { return nil }
        if now >= w.start.addingTimeInterval(-300) && now < w.start {
            return sent.contains(key(c, .soon)) ? nil : .soon
        }
        let since = now.timeIntervalSince(w.start)
        guard since >= lateFirst, since <= lateUntil, now < w.end else { return nil }
        return sent.contains(lateKey(c, now: now)) ? nil : .late
    }

    static let lateFirst: TimeInterval = 60, lateEvery: TimeInterval = 60, lateUntil: TimeInterval = 600

    /// One key per minute slot, so the late reminder repeats each minute and never twice in one.
    static func lateKey(_ c: NextClass, now: Date) -> String {
        guard let w = window(c) else { return key(c, .late) }
        let slot = Int((now.timeIntervalSince(w.start) - lateFirst) / lateEvery)
        return "\(key(c, .late))-\(slot)"
    }

    static func key(_ c: NextClass, _ s: Stage) -> String { "\(c.date)-\(c.start)-\(s.rawValue)" }

    static func text(_ c: NextClass, _ s: Stage) -> (title: String, body: String) {
        switch s {
        case .soon: return ("Class at \(c.start): start Live capture", c.title)
        case .late: return ("Capture is not running", "\(c.title) started at \(c.start). Start capture now so the class is not lost.")
        }
    }

    /// Posts a local notification; does nothing outside an app bundle (swift run, tests).
    static func post(_ c: NextClass, _ s: Stage, now: Date = Date()) {
        let t = text(c, s)
        // one notification id per class and stage: a repeat replaces the last one instead of piling up
        notify(id: key(c, s), title: t.title, body: t.body, category: s == .late ? startCategory : nil)
    }

    /// The notification category whose one action starts capture from the app.
    static let startCategory = "CAPTURE_LATE", startAction = "START_CAPTURE"

    static func registerCategories() {
        guard Bundle.main.bundleIdentifier != nil else { return }
        let start = UNNotificationAction(identifier: startAction, title: "Start capture", options: [.foreground])
        UNUserNotificationCenter.current().setNotificationCategories([
            UNNotificationCategory(identifier: startCategory, actions: [start], intentIdentifiers: [])])
    }

    /// A local notification; nothing outside an app bundle (swift run, tests).
    static func notify(id: String, title: String, body: String, category: String? = nil) {
        guard Bundle.main.bundleIdentifier != nil else { return }
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            if let category { content.categoryIdentifier = category }
            center.add(UNNotificationRequest(identifier: id, content: content, trigger: nil))
        }
    }
}
