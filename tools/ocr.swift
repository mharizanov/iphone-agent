// ocr — print text lines found in an image with their centre in POINTS.
// usage: ocr <image> [points-width]   (default 375; height scales with the image)
// Output: "x,y<TAB>text" per line, top to bottom. Uses Apple Vision on the Mac.
import AppKit
import Vision

let args = CommandLine.arguments
guard args.count >= 2, let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
  FileHandle.standardError.write("usage: ocr <image> [points-width]\n".data(using: .utf8)!)
  exit(2)
}
let ptW = args.count >= 3 ? Double(args[2]) ?? 375 : 375
let ptH = ptW * Double(cg.height) / Double(cg.width)

let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = false
req.recognitionLanguages = ["en-US", "bg-BG"]
try VNImageRequestHandler(cgImage: cg).perform([req])

let lines = (req.results ?? []).compactMap { o -> (Double, Double, String)? in
  guard let t = o.topCandidates(1).first?.string else { return nil }
  let b = o.boundingBox   // normalised, origin bottom-left
  return ((b.midX) * ptW, (1 - b.midY) * ptH, t)
}.sorted { $0.1 != $1.1 ? $0.1 < $1.1 : $0.0 < $1.0 }
for (x, y, t) in lines { print("\(Int(x)),\(Int(y))\t\(t)") }
