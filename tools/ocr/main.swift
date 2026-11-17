// Prints the text in an image as JSON lines: {"text", "left", "top", "width", "height", "conf"}, boxes
// normalised 0...1 from the top-left. Apple Vision, accurate mode, on-device.
//   ocr IMAGE [IMAGE ...]   -> one JSON array per image, one per line
import Foundation
import Vision
import ImageIO

func ocr(_ path: String) -> [[String: Any]] {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return [] }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
    return (req.results ?? []).compactMap { o in
        guard let c = o.topCandidates(1).first else { return nil }
        let b = o.boundingBox
        return ["text": c.string, "left": b.minX, "top": 1 - b.maxY, "width": b.width, "height": b.height,
                "conf": Double(c.confidence)]
    }
}

for p in CommandLine.arguments.dropFirst() {
    let data = try! JSONSerialization.data(withJSONObject: ocr(p))
    print(String(data: data, encoding: .utf8)!)
}
