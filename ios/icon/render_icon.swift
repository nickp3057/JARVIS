// Renders JARVIS's app icon (iPhone + iPad) with CoreGraphics. No assets,
// no dependencies:  swift ios/icon/render_icon.swift <out-dir>
//
// Design (2026-11-21, Nick's pick "C · HUD rings" from ios/icon/concepts.swift;
// the gold reactor of 2026-11-20 was "not it"): thin JARVIS interface arcs
// of different lengths round a white-hot core, on deep navy with a soft bloom. Three variants for iOS 18's icon styles:
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

    // Backdrop: deep navy, lighter at the top.
    if variant == .standard {
        let stops = [rgb(0x102A44), rgb(0x02060C)] as CFArray
        let g = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB)!, colors: stops, locations: [0, 1])!
        ctx.drawLinearGradient(g, start: CGPoint(x: center.x, y: size), end: CGPoint(x: center.x, y: 0), options: [])
    }
    // Bloom behind the rings.
    radial(ctx, [(0, rgb(0x2FC8F5, variant == .standard ? 0.3 : 0.2)), (1, rgb(0x2FC8F5, 0))], at: center, radius: 460, variant: variant)

    // Interface arcs of different lengths: outer hairline, the bold main
    // arc, a broken middle ring and a faint inner circle.
    arc(ctx, radius: 400, from: 200, to: 520, width: 6, color: rgb(0x59E0FF, 0.45), variant: variant)
    arc(ctx, radius: 340, from: 110, to: 410, width: 22, color: rgb(0x59E0FF), glow: 34, variant: variant)
    arc(ctx, radius: 340, from: 110, to: 410, width: 7, color: rgb(0xE6FBFF), variant: variant)
    arc(ctx, radius: 270, from: -40, to: 150, width: 10, color: rgb(0x7FE6FF, 0.85), glow: 20, variant: variant)
    arc(ctx, radius: 270, from: 175, to: 290, width: 10, color: rgb(0x7FE6FF, 0.5), variant: variant)
    arc(ctx, radius: 205, from: 0, to: 360, width: 3, color: rgb(0x7FE6FF, 0.4), variant: variant)

    // The core: white-hot, fading to cyan.
    radial(ctx, [(0, rgb(0xFFFFFF)), (0.35, rgb(0xC9F6FF)), (0.7, rgb(0x59E0FF, 0.6)), (1, rgb(0x59E0FF, 0))],
           at: center, radius: 170, variant: variant)
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 40, color: variant == .tinted ? CGColor(gray: 1, alpha: 1) : rgb(0xBFF4FF))
    ctx.setFillColor(CGColor(gray: 1, alpha: 1))
    ctx.fillEllipse(in: CGRect(x: center.x - 62, y: center.y - 62, width: 124, height: 124))
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
    if variant == .standard {
        // JARVIS for Mac: macOS doesn't mask icons, so the tile is drawn here
        // at Apple's proportions (824 pt body, 185 pt corners, soft shadow).
        let mac = CGContext(data: nil, width: Int(size), height: Int(size), bitsPerComponent: 8, bytesPerRow: 0,
                            space: CGColorSpace(name: CGColorSpace.sRGB)!,
                            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        let body = CGRect(x: 100, y: 112, width: 824, height: 824)
        let tile = CGPath(roundedRect: body, cornerWidth: 185, cornerHeight: 185, transform: nil)
        mac.saveGState()
        mac.setShadow(offset: CGSize(width: 0, height: -12), blur: 28, color: CGColor(gray: 0, alpha: 0.45))
        mac.addPath(tile)
        mac.setFillColor(rgb(0x020409))
        mac.fillPath()
        mac.restoreGState()
        mac.addPath(tile)
        mac.clip()
        mac.draw(image, in: body)
        let macRep = NSBitmapImageRep(cgImage: mac.makeImage()!)
        let macPath = (out as NSString).appendingPathComponent("AppIcon-Mac.png")
        try! macRep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: macPath))
        print("wrote \(macPath)")
    }
    let rep = NSBitmapImageRep(cgImage: image)
    let data = rep.representation(using: .png, properties: [:])!
    let path = (out as NSString).appendingPathComponent("\(variant.rawValue).png")
    try! data.write(to: URL(fileURLWithPath: path))
    print("wrote \(path)")
}
