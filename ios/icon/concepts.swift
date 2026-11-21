// Icon directions to choose from (2026-11-21): swift ios/icon/concepts.swift <out-dir>
// Writes concept-A..D.png (1024) and concepts.png (a comparison sheet).
import AppKit
import CoreGraphics
import CoreText
import Foundation

let S: CGFloat = 1024
let C = CGPoint(x: S / 2, y: S / 2)
let space = CGColorSpace(name: CGColorSpace.sRGB)!

func rgb(_ hex: UInt32, _ a: CGFloat = 1) -> CGColor {
    CGColor(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255, blue: CGFloat(hex & 0xFF) / 255, alpha: a)
}

func canvas() -> CGContext {
    let ctx = CGContext(data: nil, width: Int(S), height: Int(S), bitsPerComponent: 8, bytesPerRow: 0, space: space,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.setAllowsAntialiasing(true)
    ctx.interpolationQuality = .high
    return ctx
}

func gradient(_ stops: [(CGFloat, CGColor)]) -> CGGradient {
    CGGradient(colorsSpace: space, colors: stops.map(\.1) as CFArray, locations: stops.map(\.0))!
}

func radial(_ ctx: CGContext, _ stops: [(CGFloat, CGColor)], at p: CGPoint, r: CGFloat) {
    ctx.drawRadialGradient(gradient(stops), startCenter: p, startRadius: 0, endCenter: p, endRadius: r, options: [.drawsAfterEndLocation])
}

func linear(_ ctx: CGContext, _ stops: [(CGFloat, CGColor)], from a: CGPoint, to b: CGPoint) {
    ctx.drawLinearGradient(gradient(stops), start: a, end: b, options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
}

func ring(_ ctx: CGContext, r: CGFloat, from: CGFloat = 0, to: CGFloat = 360, width: CGFloat, color: CGColor, glow: CGFloat = 0) {
    ctx.saveGState()
    if glow > 0 { ctx.setShadow(offset: .zero, blur: glow, color: color) }
    ctx.setStrokeColor(color)
    ctx.setLineWidth(width)
    ctx.setLineCap(.round)
    ctx.addArc(center: C, radius: r, startAngle: from * .pi / 180, endAngle: to * .pi / 180, clockwise: false)
    ctx.strokePath()
    ctx.restoreGState()
}

func disc(_ ctx: CGContext, r: CGFloat, at p: CGPoint = C) -> CGRect { CGRect(x: p.x - r, y: p.y - r, width: r * 2, height: r * 2) }

func navy(_ ctx: CGContext) {
    linear(ctx, [(0, rgb(0x102A44)), (1, rgb(0x02060C))], from: CGPoint(x: C.x, y: S), to: CGPoint(x: C.x, y: 0))
}

// A · Orb: one luminous glass sphere.
func orb() -> CGImage {
    let ctx = canvas()
    navy(ctx)
    radial(ctx, [(0, rgb(0x2FC8F5, 0.45)), (1, rgb(0x2FC8F5, 0))], at: C, r: 470)
    let r: CGFloat = 300
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 90, color: rgb(0x3FD6FF, 0.9))
    ctx.setFillColor(rgb(0x0B4E73))
    ctx.fillEllipse(in: disc(ctx, r: r))
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addEllipse(in: disc(ctx, r: r))
    ctx.clip()
    radial(ctx, [(0, rgb(0xFFFFFF)), (0.18, rgb(0xC9F6FF)), (0.5, rgb(0x38C9F2)), (0.85, rgb(0x0A5D8C)), (1, rgb(0x06304D))],
           at: CGPoint(x: C.x, y: C.y - 30), r: r * 1.15)
    // Glass: a soft rim light and a top highlight.
    radial(ctx, [(0, CGColor(gray: 1, alpha: 0.55)), (1, CGColor(gray: 1, alpha: 0))], at: CGPoint(x: C.x - 90, y: C.y + 170), r: 170)
    ctx.restoreGState()
    ring(ctx, r: r - 3, width: 6, color: rgb(0xBDF3FF, 0.55))
    return ctx.makeImage()!
}

// B · Monogram: a white J in a thin glowing ring.
func monogram() -> CGImage {
    let ctx = canvas()
    navy(ctx)
    radial(ctx, [(0, rgb(0x2FC8F5, 0.22)), (1, rgb(0x2FC8F5, 0))], at: C, r: 520)
    ring(ctx, r: 360, width: 14, color: rgb(0x59E0FF), glow: 40)
    ring(ctx, r: 360, width: 4, color: rgb(0xE6FBFF))
    let font = CTFontCreateWithName("SF Pro Rounded Bold" as CFString, 520, nil)
    let fallback = CTFontCreateUIFontForLanguage(.system, 520, nil)!
    let attrs: [NSAttributedString.Key: Any] = [.font: CTFontCopyFullName(font) as String == "SF Pro Rounded Bold" ? font : fallback,
                                                .foregroundColor: CGColor(gray: 1, alpha: 1)]
    let line = CTLineCreateWithAttributedString(NSAttributedString(string: "J", attributes: attrs))
    let bounds = CTLineGetImageBounds(line, ctx)
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 50, color: rgb(0x59E0FF, 0.9))
    ctx.textPosition = CGPoint(x: C.x - bounds.midX, y: C.y - bounds.midY)
    CTLineDraw(line, ctx)
    ctx.restoreGState()
    return ctx.makeImage()!
}

// C · HUD rings: thin arcs of different lengths round a bright core.
func hud() -> CGImage {
    let ctx = canvas()
    navy(ctx)
    radial(ctx, [(0, rgb(0x2FC8F5, 0.3)), (1, rgb(0x2FC8F5, 0))], at: C, r: 460)
    ring(ctx, r: 400, from: 200, to: 520, width: 6, color: rgb(0x59E0FF, 0.45))
    ring(ctx, r: 340, from: 110, to: 410, width: 22, color: rgb(0x59E0FF), glow: 34)
    ring(ctx, r: 340, from: 110, to: 410, width: 7, color: rgb(0xE6FBFF))
    ring(ctx, r: 270, from: -40, to: 150, width: 10, color: rgb(0x7FE6FF, 0.85), glow: 20)
    ring(ctx, r: 270, from: 175, to: 290, width: 10, color: rgb(0x7FE6FF, 0.5))
    ring(ctx, r: 205, width: 3, color: rgb(0x7FE6FF, 0.4))
    radial(ctx, [(0, rgb(0xFFFFFF)), (0.35, rgb(0xC9F6FF)), (0.7, rgb(0x59E0FF, 0.6)), (1, rgb(0x59E0FF, 0))], at: C, r: 170)
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 40, color: rgb(0xBFF4FF))
    ctx.setFillColor(CGColor(gray: 1, alpha: 1))
    ctx.fillEllipse(in: disc(ctx, r: 62))
    ctx.restoreGState()
    return ctx.makeImage()!
}

// D · Liquid glass: a frosted disc on a deep aurora gradient.
func liquid() -> CGImage {
    let ctx = canvas()
    linear(ctx, [(0, rgb(0x0B2A6B)), (0.5, rgb(0x0E7C9A)), (1, rgb(0x4B2C8F))], from: CGPoint(x: 0, y: S), to: CGPoint(x: S, y: 0))
    radial(ctx, [(0, rgb(0x6FF0FF, 0.55)), (1, rgb(0x6FF0FF, 0))], at: CGPoint(x: S * 0.3, y: S * 0.7), r: 520)
    radial(ctx, [(0, rgb(0xB57BFF, 0.45)), (1, rgb(0xB57BFF, 0))], at: CGPoint(x: S * 0.78, y: S * 0.25), r: 480)
    let r: CGFloat = 310
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -24), blur: 60, color: CGColor(gray: 0, alpha: 0.35))
    ctx.setFillColor(CGColor(gray: 1, alpha: 0.16))
    ctx.fillEllipse(in: disc(ctx, r: r))
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addEllipse(in: disc(ctx, r: r))
    ctx.clip()
    linear(ctx, [(0, CGColor(gray: 1, alpha: 0.32)), (0.55, CGColor(gray: 1, alpha: 0.06)), (1, CGColor(gray: 1, alpha: 0.14))],
           from: CGPoint(x: C.x - r, y: C.y + r), to: CGPoint(x: C.x + r, y: C.y - r))
    ctx.restoreGState()
    ring(ctx, r: r - 2, width: 4, color: CGColor(gray: 1, alpha: 0.6))
    radial(ctx, [(0, rgb(0xFFFFFF)), (0.4, rgb(0xDDFBFF)), (1, rgb(0x9FF0FF, 0))], at: C, r: 130)
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 30, color: CGColor(gray: 1, alpha: 0.9))
    ctx.setFillColor(CGColor(gray: 1, alpha: 1))
    ctx.fillEllipse(in: disc(ctx, r: 56))
    ctx.restoreGState()
    return ctx.makeImage()!
}

func save(_ image: CGImage, _ path: String) {
    try! NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: path))
}

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."
let concepts: [(String, String, CGImage)] = [("A", "Orb", orb()), ("B", "Monogram", monogram()),
                                              ("C", "HUD rings", hud()), ("D", "Liquid glass", liquid())]
for (letter, _, image) in concepts { save(image, "\(out)/concept-\(letter).png") }

// The sheet: each concept as a masked icon, big and at home-screen size.
let tile: CGFloat = 440, small: CGFloat = 120, pad: CGFloat = 60
let W = pad + CGFloat(concepts.count) * (tile + pad), H: CGFloat = 820
let sheet = CGContext(data: nil, width: Int(W), height: Int(H), bitsPerComponent: 8, bytesPerRow: 0, space: space,
                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
sheet.setFillColor(rgb(0x1C1C1E))
sheet.fill(CGRect(x: 0, y: 0, width: W, height: H))
for (i, (letter, name, image)) in concepts.enumerated() {
    let x = pad + CGFloat(i) * (tile + pad)
    for (size, y) in [(tile, H - pad - tile), (small, H - pad - tile - 60 - small)] {
        let rect = CGRect(x: x + (tile - size) / 2, y: y, width: size, height: size)
        sheet.saveGState()
        sheet.addPath(CGPath(roundedRect: rect, cornerWidth: size * 0.225, cornerHeight: size * 0.225, transform: nil))
        sheet.clip()
        sheet.draw(image, in: rect)
        sheet.restoreGState()
    }
    let label = NSAttributedString(string: "\(letter) · \(name)", attributes: [
        .font: NSFont.systemFont(ofSize: 34, weight: .semibold), .foregroundColor: NSColor.white])
    let line = CTLineCreateWithAttributedString(label)
    let w = CTLineGetTypographicBounds(line, nil, nil, nil)
    sheet.textPosition = CGPoint(x: x + (tile - w) / 2, y: 40)
    CTLineDraw(line, sheet)
}
save(sheet.makeImage()!, "\(out)/concepts.png")
print("wrote \(out)/concepts.png")
