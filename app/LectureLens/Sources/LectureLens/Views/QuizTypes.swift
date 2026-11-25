import Foundation

// Question, answer and graded-item types shared by the mastery check (lecture mastery-check --json).

enum MockExamConfidence: String, CaseIterable, Identifiable, Sendable {
    case guess
    case unsure
    case sure

    var id: String { rawValue }

    var label: String {
        switch self {
        case .guess: return "Guess"
        case .unsure: return "Unsure"
        case .sure: return "Sure"
        }
    }
}

struct MockExamItem: Codable, Sendable, Identifiable, Hashable {
    let item_id: String
    let lecture_id: String
    let lecture_title: String
    let kind: String
    let question: String
    let choices: [String]
    let correct_index: Int?
    let expected_answer: String
    let concept: String

    var id: String {
        let trimmed = item_id.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "\(lecture_id)::\(question)" : trimmed
    }

    enum CodingKeys: String, CodingKey {
        case item_id, lecture_id, lecture_title, kind, question, choices, correct_index, expected_answer, concept
    }

    init(
        item_id: String = "",
        lecture_id: String = "",
        lecture_title: String = "",
        kind: String = "short",
        question: String = "",
        choices: [String] = [],
        correct_index: Int? = nil,
        expected_answer: String = "",
        concept: String = ""
    ) {
        self.item_id = item_id
        self.lecture_id = lecture_id
        self.lecture_title = lecture_title
        self.kind = kind
        self.question = question
        self.choices = choices
        self.correct_index = correct_index
        self.expected_answer = expected_answer
        self.concept = concept
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        item_id = try c.decodeIfPresent(String.self, forKey: .item_id) ?? ""
        lecture_id = try c.decodeIfPresent(String.self, forKey: .lecture_id) ?? ""
        lecture_title = try c.decodeIfPresent(String.self, forKey: .lecture_title) ?? ""
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? "short"
        question = try c.decodeIfPresent(String.self, forKey: .question) ?? ""
        choices = try c.decodeIfPresent([String].self, forKey: .choices) ?? []
        correct_index = try c.decodeIfPresent(Int.self, forKey: .correct_index)
        expected_answer = try c.decodeIfPresent(String.self, forKey: .expected_answer) ?? ""
        concept = try c.decodeIfPresent(String.self, forKey: .concept) ?? ""
    }
}

struct MockExamCitation: Codable, Sendable, Identifiable, Hashable {
    let lecture_id: String
    let source: String
    let snippet: String

    var id: String { "\(lecture_id)::\(source)::\(snippet)" }

    enum CodingKeys: String, CodingKey {
        case lecture_id, source, snippet
    }

    init(lecture_id: String = "", source: String = "", snippet: String = "") {
        self.lecture_id = lecture_id
        self.source = source
        self.snippet = snippet
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        lecture_id = try c.decodeIfPresent(String.self, forKey: .lecture_id) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
        snippet = try c.decodeIfPresent(String.self, forKey: .snippet) ?? ""
    }
}

struct MockExamGradedItem: Codable, Sendable, Identifiable, Hashable {
    let item_id: String
    let lecture_id: String
    let question: String
    let concept: String
    let correct: Bool
    let user_answer: String
    let expected_answer: String
    let confidence: String
    let explanation: String
    let citations: [MockExamCitation]

    var id: String {
        let trimmed = item_id.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "\(lecture_id)::\(question)" : trimmed
    }

    enum CodingKeys: String, CodingKey {
        case item_id, lecture_id, question, concept, correct, user_answer, expected_answer, confidence, explanation, citations
    }

    init(
        item_id: String = "",
        lecture_id: String = "",
        question: String = "",
        concept: String = "",
        correct: Bool = false,
        user_answer: String = "",
        expected_answer: String = "",
        confidence: String = "unsure",
        explanation: String = "",
        citations: [MockExamCitation] = []
    ) {
        self.item_id = item_id
        self.lecture_id = lecture_id
        self.question = question
        self.concept = concept
        self.correct = correct
        self.user_answer = user_answer
        self.expected_answer = expected_answer
        self.confidence = confidence
        self.explanation = explanation
        self.citations = citations
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        item_id = try c.decodeIfPresent(String.self, forKey: .item_id) ?? ""
        lecture_id = try c.decodeIfPresent(String.self, forKey: .lecture_id) ?? ""
        question = try c.decodeIfPresent(String.self, forKey: .question) ?? ""
        concept = try c.decodeIfPresent(String.self, forKey: .concept) ?? ""
        correct = try c.decodeIfPresent(Bool.self, forKey: .correct) ?? false
        user_answer = try c.decodeIfPresent(String.self, forKey: .user_answer) ?? ""
        expected_answer = try c.decodeIfPresent(String.self, forKey: .expected_answer) ?? ""
        confidence = try c.decodeIfPresent(String.self, forKey: .confidence) ?? "unsure"
        explanation = try c.decodeIfPresent(String.self, forKey: .explanation) ?? ""
        citations = try c.decodeIfPresent([MockExamCitation].self, forKey: .citations) ?? []
    }
}

struct MockExamAnswerPayload: Codable, Sendable, Equatable {
    let item_id: String
    let answer: String
    let choice_index: Int?
    let confidence: String
}

enum MockExamHelpers {
    nonisolated static func encodeAnswers(_ payloads: [MockExamAnswerPayload]) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(payloads),
              let text = String(data: data, encoding: .utf8) else {
            return "[]"
        }
        return text
    }
}
