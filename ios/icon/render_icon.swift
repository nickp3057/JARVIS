// Renders JARVIS's app icon (iPhone + iPad) with CoreGraphics. No assets,
// no dependencies:  swift ios/icon/render_icon.swift <out-dir>
//
// Design (2026-11-20): an original "reactor core", not Iron Man's actual
// reactor. A white-hot core with six cyan vanes inside a glass lens, a thin
// cyan orbit, and a segmented gold bezel (Nick's favourite suit, the Mark 85,
// is crimson and gold; gold only, never a red background). Deep navy to black
// behind it with a soft bloom. Three variants for iOS 18's icon styles:
// AppIcon.png (default), AppIcon-Dark.png (no backdrop: the system's dark
// background shows through) and AppIcon-Tinted.png (greyscale luminance,
// which iOS tints).
import AppKit
import CoreGraphics
import Foundation

let size: CGFloat = 1024
let center = CGPoint(x: size / 2, y: size / 2)

enum Variant: String, CaseIterable { case standard = "AppIcon", dark = "AppIcon-Dark", tinted = "AppIcon-Tinted" }

func rgb(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
}

func grey(_ color: CGColor) -> CGColor {
    // Rec. 709 luminance, so the tinted icon keeps the same light/dark shape.
    let c = color.components ?? [0, 1]
    if c.count < 4 { return CGColor(gray: c[0], alpha: c.count > 1 ? c[1] : 1) }  // already grey
    let l = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    return CGColor(gray: min(1, l * 1.25), alpha: c[3])
}

func radial(_ ctx: CGContext, _ stops: [(CGFloat, CGColor)], at point: CGPoint, radius: CGFloat, variant: Variant) {
    let colors = stops.map { variant == .tinted ? grey($0.1) : $0.1 } as CFArray
    let locations = stops.map(\.0)
    let space = variant == .tinted ? CGColorSpaceCreateDeviceGray() : CGColorSpace(name: CGColorSpace.sRGB)!
    guard let gradient = CGGradient(colorsSpace: space, colors: colors, locations: locations) else { return }
    ctx.drawRadialGradient(gradient, startCenter: point, startRadius: 0, endCenter: point, endRadius: radius,
                           options: [.drawsAfterEndLocation])
}

func arc(_ ctx: CGContext, radius: CGFloat, from: CGFloat, to: CGFloat, width: CGFloat, color: CGColor,
         glow: CGFloat = 0, cap: CGLineCap = .round, variant: Variant) {
    let color = variant == .tinted ? grey(color) : color
    ctx.saveGState()
    if glow > 0 { ctx.setShadow(offset: .zero, blur: glow, color: color.copy(alpha: 0.9)) }
    ctx.setStrokeColor(color)
    ctx.setLineWidth(width)
    ctx.setLineCap(cap)
    ctx.addArc(center: center, radius: radius, startAngle: from * .pi / 180, endAngle: to * .pi / 180, clockwise: false)
    ctx.strokePath()
    ctx.restoreGState()
}

func render(_ variant: Variant) -> CGImage {
    let space = CGColorSpace(name: CGColorSpace.sRGB)!
    let ctx = CGContext(data: nil, width: Int(size), height: Int(size), bitsPerComponent: 8, bytesPerRow: 0,
                        space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.setAllowsAntialiasing(true)
    ctx.setShouldAntialias(true)
    ctx.interpolationQuality = .high

    // Backdrop: deep navy, darker at the corners, a faint lift above centre.
    if variant == .standard {
        radial(ctx, [(0, rgb(0x0E2338)), (0.55, rgb(0x07111F)), (1, rgb(0x020409))],
               at: CGPoint(x: center.x, y: center.y + 60), radius: size * 0.78, variant: variant)
    }
    // Bloom behind the reactor.
    radial(ctx, [(0, rgb(0x3FD6FF, variant == .standard ? 0.30 : 0.2)), (0.6, rgb(0x1A8FC4, 0.07)), (1, rgb(0x000000, 0))],
           at: center, radius: 500, variant: variant)

    // Hairline outer ring with a tick every 30 degrees.
    arc(ctx, radius: 452, from: 0, to: 360, width: 3, color: rgb(0x5FD8FF, 0.24), variant: variant)
    for i in 0..<24 {
        let a = CGFloat(i) * 15 * .pi / 180
        let major = i % 6 == 0
        ctx.saveGState()
        ctx.setStrokeColor(variant == .tinted ? grey(rgb(0x7FE3FF, major ? 0.7 : 0.35)) : rgb(0x7FE3FF, major ? 0.7 : 0.35))
        ctx.setLineWidth(major ? 7 : 4)
        ctx.setLineCap(.round)
        let inner: CGFloat = major ? 424 : 438
        ctx.move(to: CGPoint(x: center.x + cos(a) * inner, y: center.y + sin(a) * inner))
        ctx.addLine(to: CGPoint(x: center.x + cos(a) * 452, y: center.y + sin(a) * 452))
        ctx.strokePath()
        ctx.restoreGState()
    }

    // Gold bezel: six machined segments, lit from above.
    let bezelInner: CGFloat = 352, bezelOuter: CGFloat = 404
    for i in 0..<6 {
        let start = (CGFloat(i) * 60 + 3.5) * .pi / 180, end = (CGFloat(i + 1) * 60 - 3.5) * .pi / 180
        let segment = CGMutablePath()
        segment.addArc(center: center, radius: bezelOuter, startAngle: start, endAngle: end, clockwise: false)
        segment.addArc(center: center, radius: bezelInner, startAngle: end, endAngle: start, clockwise: true)
        segment.closeSubpath()
        ctx.saveGState()  // warm glow under the metal
        ctx.setShadow(offset: .zero, blur: 26, color: variant == .tinted ? CGColor(gray: 0.8, alpha: 0.6) : rgb(0xF2B84B, 0.55))
        ctx.addPath(segment)
        ctx.setFillColor(variant == .tinted ? grey(rgb(0xB07A26)) : rgb(0xB07A26))
        ctx.fillPath()
        ctx.restoreGState()
        ctx.saveGState()  // brushed gold: light at the top, deep at the bottom
        ctx.addPath(segment)
        ctx.clip()
        let golds = [rgb(0xFBE09A), rgb(0xE8B95A), rgb(0xC1872B), rgb(0x83531A)].map { variant == .tinted ? grey($0) : $0 }
        let space = variant == .tinted ? CGColorSpaceCreateDeviceGray() : CGColorSpace(name: CGColorSpace.sRGB)!
        let gradient = CGGradient(colorsSpace: space, colors: golds as CFArray, locations: [0, 0.35, 0.7, 1])!
        ctx.drawLinearGradient(gradient, start: CGPoint(x: center.x, y: center.y + bezelOuter),
                               end: CGPoint(x: center.x, y: center.y - bezelOuter), options: [])
        ctx.restoreGState()
        // A bright inner lip and a dark outer edge give it thickness.
        arc(ctx, radius: bezelInner + 3, from: start * 180 / .pi + 0.6, to: end * 180 / .pi - 0.6, width: 4,
            color: rgb(0xFFF3CF, 0.85), cap: .butt, variant: variant)
        arc(ctx, radius: bezelOuter - 2, from: start * 180 / .pi, to: end * 180 / .pi, width: 3,
            color: rgb(0x5E3C0C, 0.7), cap: .butt, variant: variant)
    }

    // Cyan orbit just inside the bezel, open where it catches the light.
    arc(ctx, radius: 318, from: 120, to: 420, width: 12, color: rgb(0x3FD6FF), glow: 30, variant: variant)
    arc(ctx, radius: 318, from: 120, to: 420, width: 4, color: rgb(0xE2FAFF, 0.95), variant: variant)

    // Glass lens with a bevel.
    let lens: CGFloat = 262
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 30, color: CGColor(gray: 0, alpha: 0.7))
    ctx.setFillColor(variant == .tinted ? CGColor(gray: 0.1, alpha: 1) : rgb(0x061422))
    ctx.fillEllipse(in: CGRect(x: center.x - lens, y: center.y - lens, width: lens * 2, height: lens * 2))
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addEllipse(in: CGRect(x: center.x - lens, y: center.y - lens, width: lens * 2, height: lens * 2))
    ctx.clip()
    radial(ctx, [(0, rgb(0x14506E)), (0.65, rgb(0x0A2638)), (1, rgb(0x040E18))], at: center, radius: lens, variant: variant)
    ctx.restoreGState()
    arc(ctx, radius: lens, from: 0, to: 360, width: 6, color: rgb(0x0B2C40), variant: variant)
    arc(ctx, radius: lens - 4, from: 20, to: 160, width: 3, color: rgb(0xBFF2FF, 0.55), variant: variant)

    // Seven curved, tapered blades swirling round the core.
    for i in 0..<7 {
        let base = CGFloat(i) * (360 / 7) * .pi / 180
        let blade = CGMutablePath()
        let r0: CGFloat = 92, r1: CGFloat = 232, sweep: CGFloat = 0.5, width0: CGFloat = 0.34, width1: CGFloat = 0.09
        blade.move(to: CGPoint(x: center.x + cos(base) * r0, y: center.y + sin(base) * r0))
        blade.addQuadCurve(to: CGPoint(x: center.x + cos(base + sweep) * r1, y: center.y + sin(base + sweep) * r1),
                           control: CGPoint(x: center.x + cos(base + sweep * 0.2) * (r0 + r1) / 2,
                                            y: center.y + sin(base + sweep * 0.2) * (r0 + r1) / 2))
        blade.addLine(to: CGPoint(x: center.x + cos(base + sweep + width1) * r1, y: center.y + sin(base + sweep + width1) * r1))
        blade.addQuadCurve(to: CGPoint(x: center.x + cos(base + width0) * r0, y: center.y + sin(base + width0) * r0),
                           control: CGPoint(x: center.x + cos(base + sweep * 0.45 + width0) * (r0 + r1) / 2,
                                            y: center.y + sin(base + sweep * 0.45 + width0) * (r0 + r1) / 2))
        blade.closeSubpath()
        ctx.saveGState()
        let color = variant == .tinted ? grey(rgb(0x58DDFF, 0.9)) : rgb(0x58DDFF, 0.9)
        ctx.setShadow(offset: .zero, blur: 18, color: color)
        ctx.addPath(blade)
        ctx.setFillColor(color)
        ctx.fillPath()
        ctx.setShadow(offset: .zero, blur: 0, color: nil)
        ctx.addPath(blade)  // a lit leading edge
        ctx.setStrokeColor(variant == .tinted ? CGColor(gray: 1, alpha: 0.5) : rgb(0xDDF8FF, 0.6))
        ctx.setLineWidth(2)
        ctx.strokePath()
        ctx.restoreGState()
    }

    // The core: white-hot centre fading to cyan.
    radial(ctx, [(0, rgb(0xFFFFFF)), (0.3, rgb(0xE6FBFF)), (0.6, rgb(0x7FE6FF, 0.8)), (1, rgb(0x1FB5E8, 0))],
           at: center, radius: 190, variant: variant)
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 44, color: variant == .tinted ? CGColor(gray: 1, alpha: 1) : rgb(0xBFF4FF))
    ctx.setFillColor(CGColor(gray: 1, alpha: 1))
    ctx.fillEllipse(in: CGRect(x: center.x - 66, y: center.y - 66, width: 132, height: 132))
    ctx.restoreGState()
    arc(ctx, radius: 86, from: 0, to: 360, width: 5, color: rgb(0xBFF4FF, 0.8), glow: 12, variant: variant)

    // Specular highlight across the top of the lens.
    ctx.saveGState()
    ctx.addEllipse(in: CGRect(x: center.x - lens, y: center.y - lens, width: lens * 2, height: lens * 2))
    ctx.clip()
    radial(ctx, [(0, CGColor(gray: 1, alpha: 0.14)), (1, CGColor(gray: 1, alpha: 0))],
           at: CGPoint(x: center.x - 70, y: center.y + 170), radius: 230, variant: variant)
    ctx.restoreGState()

    return ctx.makeImage()!
}

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."
for variant in Variant.allCases {
    var image = render(variant)
    if variant == .standard {
        // The default icon must be opaque (App Store rule): flatten onto black.
        let flat = CGContext(data: nil, width: Int(size), height: Int(size), bitsPerComponent: 8, bytesPerRow: 0,
                             space: CGColorSpace(name: CGColorSpace.sRGB)!,
                             bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        flat.setFillColor(rgb(0x020409))
        flat.fill(CGRect(x: 0, y: 0, width: size, height: size))
        flat.draw(image, in: CGRect(x: 0, y: 0, width: size, height: size))
        image = flat.makeImage()!
    }
    let rep = NSBitmapImageRep(cgImage: image)
    let data = rep.representation(using: .png, properties: [:])!
    let path = (out as NSString).appendingPathComponent("\(variant.rawValue).png")
    try! data.write(to: URL(fileURLWithPath: path))
    print("wrote \(path)")
}
