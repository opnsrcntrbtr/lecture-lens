import Foundation
import Testing
@testable import LectureLens

// Guided tutor v2: decode `lecture tutor-session … --json` and the copy that explains the gating.

@Test func guidedSessionDecodesWithoutTheAnswer() throws {
    let j = #"{"ok": true, "id": "tutor-1", "question": "Why mulch?", "lecture_id": "a", "source": "mastery", "rung": 1, "max_rung": 3, "next_hint": "where in the lecture", "attempts": 1, "solved": false, "can_reveal": false, "revealed": null, "hints": [{"rung": 1, "kind": "a narrower question", "text": "What happens to water?", "how": "template", "t": "x"}], "last": {"t": "x", "text": "no idea", "pct": 0, "verdict": "Needs Review", "correct": false}, "note": null, "exit": null}"#
    let s = try JSONDecoder().decode(GuidedSession.self, from: Data(j.utf8))
    #expect(s.answer == nil && s.hints?.count == 1 && s.can_reveal == false)
    #expect(GuidedText.status(s).contains("1 more try"))
    #expect(GuidedText.hintButton(s) == "Hint: where in the lecture")
}

@Test func guidedArgsKeepQuestionsAfterTheSeparator() {
    #expect(GuidedText.startArgs(GuidedSeed(question: "-why?", lecture: "a")).last == "--question=-why?")
    #expect(GuidedText.startArgs(GuidedSeed(itemRef: "mastery:c:i")) == ["tutor-session", "start", "--item", "mastery:c:i", "--json"])
    #expect(GuidedText.args("reveal", id: "t", extra: ["--just-tell-me"]) == ["tutor-session", "reveal", "--id", "t", "--just-tell-me", "--json"])
}

@Test func guidedStatusAfterAskingSaysSo() throws {
    let s = try JSONDecoder().decode(GuidedSession.self, from: Data(#"{"ok": true, "revealed": "asked", "answer": "x"}"#.utf8))
    #expect(GuidedText.status(s).hasPrefix("You asked for the answer"))
}

@Test func guidedSessionCarriesTheLearnerBrief() throws {
    let s = try JSONDecoder().decode(GuidedSession.self, from: Data(#"{"ok": true, "id": "t", "brief": "Mastery checks: 1 of 2 right on this lecture."}"#.utf8))
    #expect(s.brief?.hasPrefix("Mastery checks") == true)
}
