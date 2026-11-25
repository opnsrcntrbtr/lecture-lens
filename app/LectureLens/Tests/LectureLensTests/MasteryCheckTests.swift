import AppKit
import Foundation
import Testing
@testable import LectureLens

// Mastery check: decode what `lecture mastery-check … --json` prints (mastery_check.py), and the copy.

@Test func masteryStartDecodesRetryFlags() throws {
    let json = #"{"ok": true, "check_id": "mastery-1", "attempt": 2, "bar": 90, "retry": true, "retry_count": 1, "lecture_count": 2, "item_count": 2, "items": [{"item_id": "m2-1", "lecture_id": "a", "lecture_title": "Soil", "kind": "mcq", "question": "Q1", "choices": ["x", "y"], "correct_index": 0, "expected_answer": "x", "concept": "mulch", "retry": true}, {"item_id": "m2-2", "lecture_id": "b", "lecture_title": "Water", "kind": "short", "question": "Q2", "choices": [], "correct_index": null, "expected_answer": "drip", "concept": "drip", "retry": false}], "scrubbed": true}"#
    let s = try JSONDecoder().decode(MasteryStart.self, from: Data(json.utf8))
    #expect(s.ok && s.attempt == 2 && s.items.count == 2)
    #expect(s.retried == ["m2-1"])
}

@Test func masteryResultAndStatusDecodeWithMissingKeys() throws {
    let r = try JSONDecoder().decode(MasteryResult.self, from: Data(#"{"ok": true, "score_pct": 75, "passed": false, "bar": 90, "next": "Try again", "calibration": {"line": "Sure 4 (3 right)", "verdict": "Your confidence matched your results.", "overconfident": 1}, "by_lecture": [{"lecture_id": "a", "title": "Soil", "asked": 2, "right": 1, "pct": 50}]}"#.utf8))
    #expect(r.score_pct == 75 && !r.passed && r.graded_items.isEmpty && r.by_lecture.first?.pct == 50)
    #expect(r.calibration?.overconfident == 1)
    let s = try JSONDecoder().decode(MasteryStatus.self, from: Data(#"{"ok": true, "state": "due_new", "message": "m", "bar": 90, "new_lectures": ["Pests"]}"#.utf8))
    #expect(MasteryText.todayBadge(s) == "1 new lecture(s)")
    #expect(MasteryText.startTitle(s.state) == "Take it again")
}

@Test func masteryCopy() {
    #expect(MasteryText.startTitle(nil) == "Start mastery check")
    #expect(MasteryText.startTitle("retry") == "Try again")
    #expect(MasteryText.resultTitle(score: 92, bar: 90, passed: true) == "Passed: 92% (pass mark 90%)")
    #expect(MasteryText.resultTitle(score: 75, bar: 90, passed: false) == "Not yet: 75%. Pass mark 90%")
    #expect(MasteryText.progress(answered: 3, total: 6) == "3 of 6 answered")
    #expect(MasteryText.submitArgs(checkId: "mastery-1", answers: "[]", elapsed: -3).suffix(3) == ["--elapsed-seconds", "0", "--json"])
}

@Test func phosphorAccentAdaptsToAppearance() {
    #expect(Phosphor.isDark(NSAppearance(named: .darkAqua)!))
    #expect(!Phosphor.isDark(NSAppearance(named: .aqua)!))
}

@Test func relearnQueueRequeuesMissesUntilEachIsRightOnce() {
    let a = MockExamItem(item_id: "a", kind: "mcq", question: "A", choices: ["x", "y"], correct_index: 0)
    let b = MockExamItem(item_id: "b", kind: "mcq", question: "B", choices: ["x", "y"], correct_index: 1)
    var q = RelearnQueue(items: [a, b])
    #expect(q.answer(1) == false && q.current?.item_id == "b")   // a goes to the back
    #expect(q.answer(1) == true && q.current?.item_id == "a" && q.learned == 1)
    #expect(q.answer(0) == true && q.done && q.rounds == 2 && q.answers == 3)
    var once = RelearnQueue(items: [a])
    once.answer(0)
    #expect(once.done && once.rounds == 1)
}

@Test func masteryRetryWaitsForItsSpacing() throws {
    let s = try JSONDecoder().decode(MasteryStatus.self, from: Data(#"{"ok": true, "state": "retry", "message": "m", "due_at": "2030-01-03T10:00+05:30"}"#.utf8))
    #expect(s.waiting(now: ISO8601DateFormatter().date(from: "2030-01-02T00:00:00Z")!))
    #expect(!s.waiting(now: ISO8601DateFormatter().date(from: "2030-01-04T00:00:00Z")!))
}
