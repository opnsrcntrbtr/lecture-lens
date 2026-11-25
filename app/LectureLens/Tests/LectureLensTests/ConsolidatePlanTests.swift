import Foundation
import Testing
@testable import LectureLens

// Consolidate says what it would do before it happens: the newest lecture used to be the default
// destination, so folding in an older class quietly dated the result to the older day.

private func lec(_ id: String, _ start: String, _ minutes: Double, consolidated: Bool? = nil) -> Session {
    let f = ISO8601DateFormatter()
    let end = f.date(from: start).map { f.string(from: $0.addingTimeInterval(minutes * 60)) }
    return Session(id: id, title: "T \(id)", start: start, end: end, minutes: minutes, frames: 1, built: true,
                   folder: "/l/\(id)_x", consolidated: consolidated)
}

@Test func consolidateOffersOnlyASameDayDestination() {
    let octA = lec("a", "2030-10-31T04:33:00Z", 234), octB = lec("b", "2030-10-31T04:33:44Z", 142)
    let nov = lec("c", "2030-11-21T04:30:06Z", 193)
    #expect(ConsolidatePlan.defaultDestination(for: octB, among: [octA, octB, nov])?.id == "a")
    #expect(ConsolidatePlan.defaultDestination(for: nov, among: [octA, octB, nov]) == nil)
}

@Test func consolidateWarnsAboutOtherDaysAndOverlappingCopies() {
    let octA = lec("a", "2030-10-31T04:33:00Z", 234), octB = lec("b", "2030-10-31T04:33:44Z", 142)
    let nov = lec("c", "2030-11-21T04:30:06Z", 193)
    let far = ConsolidatePlan.warnings(destination: nov, sources: [octA, octB])
    #expect(far.contains { $0.contains("2 different days") })
    #expect(ConsolidatePlan.warnings(destination: octA, sources: [octB]).first?.contains("second copy of the same class") == true)
    let morning = lec("d", "2030-11-21T04:30:00Z", 60), later = lec("e", "2030-11-21T05:40:00Z", 60)
    #expect(ConsolidatePlan.warnings(destination: morning, sources: [later]).isEmpty)   // one class in two pieces
}

@Test func sessionDecodesTheConsolidatedFlag() throws {
    let s = try JSONDecoder().decode(SessionList.self, from: Data(#"{"sessions": [{"id": "a", "built": true, "folder": "/x", "consolidated": true}, {"id": "b", "built": false, "folder": "/y"}]}"#.utf8))
    #expect(s.sessions[0].consolidated == true && s.sessions[1].consolidated == nil)
}

@Test func libraryCheckDecodesAndOrdersCopiesLast() throws {
    let j = #"{"ok": true, "lectures": 3, "issues": [{"kind": "copies", "folders": ["a", "b"], "message": "two copies"}, {"kind": "dated_away", "folders": ["c"], "message": "dated away"}], "counts": {"copies": 1}}"#
    let l = try JSONDecoder().decode(LibraryCheck.self, from: Data(j.utf8))
    #expect(l.ordered.first?.kind == "dated_away")
    #expect(l.summary == "1 to fix · 1 class(es) built twice")
}
