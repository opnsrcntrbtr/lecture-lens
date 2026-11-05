// Build: swiftc -O tools/vision_ocr.swift -o tools/vision_ocr
// vision_ocr: print the text Apple's Vision framework reads in one image, one line per
// observation, top to bottom. Local only.   vision_ocr <image>
import Foundation
import Vision
import AppKit

let args = Array(CommandLine.arguments.dropFirst())
let asJSON = args.first == "--json"   // one JSON object per line: text plus its box (0-1, origin top-left)
guard let path = args.last, args.count == (asJSON ? 2 : 1),
      let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("usage: vision_ocr [--json] <image>\n".data(using: .utf8)!)
    exit(2)
}
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = true
do {
    try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
} catch {
    FileHandle.standardError.write("vision failed: \(error)\n".data(using: .utf8)!)
    exit(1)
}
let obs = (req.results ?? []).sorted { $0.boundingBox.minY > $1.boundingBox.minY }
for o in obs {
    guard let t = o.topCandidates(1).first?.string else { continue }
    if asJSON {
        let b = o.boundingBox
        let d: [String: Any] = ["text": t, "x": b.minX, "y": 1 - b.maxY, "w": b.width, "h": b.height]
        if let j = try? JSONSerialization.data(withJSONObject: d), let s = String(data: j, encoding: .utf8) { print(s) }
    } else { print(t) }
}
