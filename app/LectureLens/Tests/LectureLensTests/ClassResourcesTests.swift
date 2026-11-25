import Foundation
import Testing
@testable import LectureLens

@Test func decodesResourcesAddAndBuildsArgs() throws {
    let json = #"{"lecture": "20300115-1000_Gardening", "added": [{"file": "Soil.pptx", "kind": "deck", "summary": "12 slides", "outputs": ["resources/decks/Soil.deck.json"], "added_at": "2030-01-20T10:00:00+00:00"}, {"file": "qa.xlsx", "kind": "qa_sheet", "summary": "9 answered questions", "people": 4}], "skipped": [{"file": "agenda.docx", "reason": "already added", "kind": "agenda"}], "rebuild": true}"#
    let r = try JSONDecoder().decode(ResourcesAddResult.self, from: Data(json.utf8))
    #expect(r.added.map(\.kind) == ["deck", "qa_sheet"] && r.skipped.first?.reason == "already added" && r.rebuild)
    #expect(ClassResources.args(lecture: "20300115-1000", files: ["/tmp/a.pdf"]) == ["resources", "add", "20300115-1000", "/tmp/a.pdf", "--json"])
    #expect(ClassResources.accepts(URL(fileURLWithPath: "/x/Deck.PPTX")) && !ClassResources.accepts(URL(fileURLWithPath: "/x/zoom.vtt")))
    #expect(ClassResources.kindLabel("qa_sheet") == "Q&A sheet" && ClassResources.kindLabel("other") == "Reading")
}
