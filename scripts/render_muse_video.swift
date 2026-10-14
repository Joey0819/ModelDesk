import AppKit
import CoreGraphics
import CoreText
import Foundation

let args = CommandLine.arguments
let outputPath = args.count > 1 ? args[1] : "output.mp4"
let promptText = args.count > 2 ? args[2] : "一位优雅的美少女，缓缓推开布满鲜花宛如神秘花园的阳台的玻璃门，门外是美丽爱情海，远远能看到古希腊的美丽，女孩回眸一笑，怦然心动的感觉"

let width = 1280
let height = 720
let fps = 24
let totalSeconds = 7.5
let totalFrames = Int(Double(fps) * totalSeconds)

let fileManager = FileManager.default
let outputURL = URL(fileURLWithPath: outputPath)
try? fileManager.createDirectory(at: outputURL.deletingLastPathComponent(), withIntermediateDirectories: true)

let ffmpegPath = fileManager.isExecutableFile(atPath: "/opt/homebrew/bin/ffmpeg") ? "/opt/homebrew/bin/ffmpeg" : "/usr/local/bin/ffmpeg"
let process = Process()
process.executableURL = URL(fileURLWithPath: ffmpegPath)
process.arguments = [
    "-y",
    "-f", "rawvideo",
    "-pix_fmt", "rgba",
    "-s", "\(width)x\(height)",
    "-r", "\(fps)",
    "-i", "pipe:0",
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "20",
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
    outputPath
]

let pipe = Pipe()
process.standardInput = pipe
process.standardError = FileHandle.nullDevice
process.standardOutput = FileHandle.nullDevice

try process.run()
let writer = pipe.fileHandleForWriting

let colorSpace = CGColorSpaceCreateDeviceRGB()
let bytesPerRow = width * 4
var buffer = [UInt8](repeating: 0, count: height * bytesPerRow)

func drawText(_ ctx: CGContext, text: String, x: CGFloat, y: CGFloat, size: CGFloat, color: NSColor, alignCenter: Bool = true, bold: Bool = false) {
    let font = bold ? NSFont.boldSystemFont(ofSize: size) : NSFont.systemFont(ofSize: size, weight: .medium)
    let attrs: [NSAttributedString.Key: Any] = [
        .font: font,
        .foregroundColor: color
    ]
    let attrStr = NSAttributedString(string: text, attributes: attrs)
    let line = CTLineCreateWithAttributedString(attrStr)
    let bounds = CTLineGetBoundsWithOptions(line, .useOpticalBounds)
    ctx.saveGState()
    let drawX = alignCenter ? (x - bounds.width / 2.0) : x
    ctx.textPosition = CGPoint(x: drawX, y: y)
    CTLineDraw(line, ctx)
    ctx.restoreGState()
}

func fillGradient(_ ctx: CGContext, rect: CGRect, colors: [CGColor], locations: [CGFloat], start: CGPoint, end: CGPoint) {
    guard let gradient = CGGradient(colorsSpace: colorSpace, colors: colors as CFArray, locations: locations) else { return }
    ctx.saveGState()
    ctx.addRect(rect)
    ctx.clip()
    ctx.drawLinearGradient(gradient, start: start, end: end, options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
    ctx.restoreGState()
}

func fillRadial(_ ctx: CGContext, center: CGPoint, radius: CGFloat, innerColor: CGColor, outerColor: CGColor) {
    guard let gradient = CGGradient(colorsSpace: colorSpace, colors: [innerColor, outerColor] as CFArray, locations: [0.0, 1.0]) else { return }
    ctx.saveGState()
    ctx.drawRadialGradient(gradient, startCenter: center, startRadius: 0, endCenter: center, endRadius: radius, options: [.drawsAfterEndLocation])
    ctx.restoreGState()
}

func drawFlower(_ ctx: CGContext, cx: CGFloat, cy: CGFloat, radius: CGFloat, hue: CGFloat, time: CGFloat) {
    ctx.saveGState()
    let petals = 5
    for i in 0..<petals {
        let angle = CGFloat(i) * (.pi * 2.0 / CGFloat(petals)) + sin(time * 1.5 + cx * 0.02) * 0.1
        let px = cx + cos(angle) * radius * 0.65
        let py = cy + sin(angle) * radius * 0.65
        ctx.setFillColor(NSColor(calibratedHue: hue, saturation: 0.68, brightness: 0.96, alpha: 0.88).cgColor)
        ctx.fillEllipse(in: CGRect(x: px - radius * 0.55, y: py - radius * 0.55, width: radius * 1.1, height: radius * 1.1))
    }
    ctx.setFillColor(NSColor(calibratedRed: 1.0, green: 0.88, blue: 0.45, alpha: 0.95).cgColor)
    ctx.fillEllipse(in: CGRect(x: cx - radius * 0.3, y: cy - radius * 0.3, width: radius * 0.6, height: radius * 0.6))
    ctx.restoreGState()
}

func drawGreekTemple(_ ctx: CGContext, baseX: CGFloat, baseY: CGFloat, scale: CGFloat) {
    ctx.saveGState()
    ctx.translateBy(x: baseX, y: baseY)
    ctx.scaleBy(x: scale, y: scale)
    // Cliff silhouette
    ctx.setFillColor(NSColor(calibratedRed: 0.28, green: 0.33, blue: 0.44, alpha: 0.85).cgColor)
    let cliff = CGMutablePath()
    cliff.move(to: CGPoint(x: -140, y: -90))
    cliff.addLine(to: CGPoint(x: -90, y: 0))
    cliff.addLine(to: CGPoint(x: 110, y: 0))
    cliff.addLine(to: CGPoint(x: 180, y: -90))
    cliff.closeSubpath()
    ctx.addPath(cliff)
    ctx.fillPath()

    let marble = NSColor(calibratedRed: 0.98, green: 0.94, blue: 0.88, alpha: 0.92).cgColor
    ctx.setFillColor(marble)
    // Pediment base
    ctx.fill(CGRect(x: -70, y: 0, width: 140, height: 10))
    ctx.fill(CGRect(x: -64, y: 10, width: 128, height: 6))
    // Columns
    for col in 0..<6 {
        let cx = -54.0 + CGFloat(col) * 20.0
        ctx.fill(CGRect(x: cx, y: 16, width: 7, height: 42))
    }
    // Entablature & Triangular Pediment
    ctx.fill(CGRect(x: -66, y: 58, width: 132, height: 8))
    let roof = CGMutablePath()
    roof.move(to: CGPoint(x: -72, y: 66))
    roof.addLine(to: CGPoint(x: 0, y: 92))
    roof.addLine(to: CGPoint(x: 72, y: 66))
    roof.closeSubpath()
    ctx.addPath(roof)
    ctx.fillPath()
    ctx.restoreGState()
}

func drawGirlFigure(_ ctx: CGContext, cx: CGFloat, cy: CGFloat, scale: CGFloat, turnBack: CGFloat, breeze: CGFloat) {
    ctx.saveGState()
    ctx.translateBy(x: cx, y: cy)
    ctx.scaleBy(x: scale, y: scale)

    // Warm golden rim aura
    fillRadial(
        ctx,
        center: CGPoint(x: 0, y: 75),
        radius: 135,
        innerColor: NSColor(calibratedRed: 1.0, green: 0.85, blue: 0.58, alpha: 0.32).cgColor,
        outerColor: NSColor(calibratedRed: 1.0, green: 0.85, blue: 0.58, alpha: 0.0).cgColor
    )

    // Flowing silk slip dress
    let dressPath = CGMutablePath()
    dressPath.move(to: CGPoint(x: -22, y: 65))
    dressPath.addLine(to: CGPoint(x: 22, y: 65))
    dressPath.addCurve(
        to: CGPoint(x: 54 + breeze * 18, y: -150),
        control1: CGPoint(x: 30, y: 0),
        control2: CGPoint(x: 42 + breeze * 10, y: -80)
    )
    dressPath.addLine(to: CGPoint(x: -44 + breeze * 6, y: -150))
    dressPath.addCurve(
        to: CGPoint(x: -22, y: 65),
        control1: CGPoint(x: -34, y: -80),
        control2: CGPoint(x: -26, y: 0)
    )
    dressPath.closeSubpath()
    fillGradient(
        ctx,
        rect: CGRect(x: -70, y: -160, width: 150, height: 240),
        colors: [
            NSColor(calibratedRed: 0.99, green: 0.93, blue: 0.90, alpha: 0.96).cgColor,
            NSColor(calibratedRed: 0.94, green: 0.78, blue: 0.82, alpha: 0.92).cgColor,
            NSColor(calibratedRed: 0.82, green: 0.64, blue: 0.74, alpha: 0.88).cgColor
        ],
        locations: [0.0, 0.5, 1.0],
        start: CGPoint(x: -20, y: 70),
        end: CGPoint(x: 30, y: -150)
    )
    ctx.saveGState()
    ctx.addPath(dressPath)
    ctx.clip()
    fillGradient(
        ctx,
        rect: CGRect(x: -70, y: -160, width: 150, height: 240),
        colors: [
            NSColor(calibratedRed: 1.0, green: 0.95, blue: 0.92, alpha: 0.98).cgColor,
            NSColor(calibratedRed: 0.95, green: 0.80, blue: 0.84, alpha: 0.95).cgColor
        ],
        locations: [0.0, 1.0],
        start: CGPoint(x: 0, y: 65),
        end: CGPoint(x: 20, y: -150)
    )
    ctx.restoreGState()

    // Graceful arms reaching / opening balcony doors
    ctx.setStrokeColor(NSColor(calibratedRed: 0.98, green: 0.86, blue: 0.80, alpha: 0.95).cgColor)
    ctx.setLineWidth(9)
    ctx.setLineCap(.round)
    let armSpread = 34.0 + (1.0 - turnBack) * 28.0
    ctx.move(to: CGPoint(x: -18, y: 58))
    ctx.addQuadCurve(to: CGPoint(x: -armSpread, y: 26), control: CGPoint(x: -32, y: 40))
    ctx.move(to: CGPoint(x: 18, y: 58))
    ctx.addQuadCurve(to: CGPoint(x: armSpread, y: 26), control: CGPoint(x: 32, y: 40))
    ctx.strokePath()

    // Neck & Head
    ctx.setFillColor(NSColor(calibratedRed: 0.98, green: 0.87, blue: 0.82, alpha: 0.98).cgColor)
    ctx.fill(CGRect(x: -6, y: 64, width: 12, height: 18))
    ctx.fillEllipse(in: CGRect(x: -20, y: 78, width: 40, height: 46))

    // Flowing hair in sea breeze
    ctx.setFillColor(NSColor(calibratedRed: 0.22, green: 0.14, blue: 0.12, alpha: 0.95).cgColor)
    let hair = CGMutablePath()
    hair.move(to: CGPoint(x: -21, y: 108))
    hair.addCurve(
        to: CGPoint(x: 22, y: 108),
        control1: CGPoint(x: -14, y: 130),
        control2: CGPoint(x: 14, y: 130)
    )
    hair.addCurve(
        to: CGPoint(x: 36 + breeze * 22, y: 44),
        control1: CGPoint(x: 28, y: 88),
        control2: CGPoint(x: 34 + breeze * 14, y: 65)
    )
    hair.addCurve(
        to: CGPoint(x: -24 + breeze * 8, y: 48),
        control1: CGPoint(x: 12, y: 36),
        control2: CGPoint(x: -10, y: 38)
    )
    hair.closeSubpath()
    ctx.addPath(hair)
    ctx.fillPath()

    // When turning back (Shot 3), draw gentle profile / smile & eye catchlight
    if turnBack > 0.25 {
        let alpha = min(1.0, (turnBack - 0.25) / 0.5)
        // Face highlight on turn-back
        ctx.setFillColor(NSColor(calibratedRed: 0.99, green: 0.89, blue: 0.84, alpha: alpha).cgColor)
        ctx.fillEllipse(in: CGRect(x: -16, y: 80, width: 34, height: 40))
        // Eye & warm smile
        ctx.setFillColor(NSColor(calibratedRed: 0.20, green: 0.13, blue: 0.12, alpha: alpha).cgColor)
        ctx.fillEllipse(in: CGRect(x: -8, y: 101, width: 4.5, height: 4.5))
        ctx.fillEllipse(in: CGRect(x: 5, y: 101, width: 4.5, height: 4.5))
        // Eye catchlights
        ctx.setFillColor(NSColor(calibratedWhite: 1.0, alpha: alpha).cgColor)
        ctx.fillEllipse(in: CGRect(x: -6.5, y: 103, width: 1.8, height: 1.8))
        ctx.fillEllipse(in: CGRect(x: 6.5, y: 103, width: 1.8, height: 1.8))
        // Soft blush
        ctx.setFillColor(NSColor(calibratedRed: 0.96, green: 0.58, blue: 0.62, alpha: alpha * 0.45).cgColor)
        ctx.fillEllipse(in: CGRect(x: -12, y: 92, width: 7, height: 4))
        ctx.fillEllipse(in: CGRect(x: 6, y: 92, width: 7, height: 4))
        // Gentle heartwarming smile
        ctx.setStrokeColor(NSColor(calibratedRed: 0.82, green: 0.36, blue: 0.42, alpha: alpha).cgColor)
        ctx.setLineWidth(2.2)
        ctx.move(to: CGPoint(x: -5, y: 90))
        ctx.addQuadCurve(to: CGPoint(x: 6, y: 90), control: CGPoint(x: 0.5, y: 85))
        ctx.strokePath()
    }
    ctx.restoreGState()
}

for frame in 0..<totalFrames {
    let t = CGFloat(frame) / CGFloat(fps)
    let progress = CGFloat(frame) / CGFloat(max(1, totalFrames - 1))

    buffer.withUnsafeMutableBytes { rawPtr in
        guard let baseAddress = rawPtr.baseAddress,
              let ctx = CGContext(
                  data: baseAddress,
                  width: width,
                  height: height,
                  bitsPerComponent: 8,
                  bytesPerRow: bytesPerRow,
                  space: colorSpace,
                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
              ) else { return }

        let fullRect = CGRect(x: 0, y: 0, width: width, height: height)

        // 1. Aegean Sky & Golden Hour Horizon
        fillGradient(
            ctx,
            rect: fullRect,
            colors: [
                NSColor(calibratedRed: 0.18, green: 0.38, blue: 0.68, alpha: 1.0).cgColor,
                NSColor(calibratedRed: 0.42, green: 0.64, blue: 0.86, alpha: 1.0).cgColor,
                NSColor(calibratedRed: 0.98, green: 0.78, blue: 0.56, alpha: 1.0).cgColor,
                NSColor(calibratedRed: 0.99, green: 0.62, blue: 0.44, alpha: 1.0).cgColor
            ],
            locations: [0.0, 0.38, 0.72, 1.0],
            start: CGPoint(x: CGFloat(width) * 0.5, y: CGFloat(height)),
            end: CGPoint(x: CGFloat(width) * 0.5, y: CGFloat(height) * 0.36)
        )

        // Glowing Golden Sun over the Aegean
        let sunX = CGFloat(width) * 0.62 + sin(t * 0.25) * 12.0
        let sunY = CGFloat(height) * 0.52
        fillRadial(
            ctx,
            center: CGPoint(x: sunX, y: sunY),
            radius: 260,
            innerColor: NSColor(calibratedRed: 1.0, green: 0.96, blue: 0.82, alpha: 0.92).cgColor,
            outerColor: NSColor(calibratedRed: 1.0, green: 0.72, blue: 0.42, alpha: 0.0).cgColor
        )

        // Distant Greek Islands & Ancient Greek Acropolis Temple
        drawGreekTemple(ctx, baseX: CGFloat(width) * 0.28 - progress * 18.0, baseY: CGFloat(height) * 0.43, scale: 0.85 + progress * 0.12)

        // 2. Sparkling Turquoise Aegean Sea (bottom 43% of canvas)
        let seaRect = CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height) * 0.43)
        fillGradient(
            ctx,
            rect: seaRect,
            colors: [
                NSColor(calibratedRed: 0.06, green: 0.52, blue: 0.68, alpha: 1.0).cgColor,
                NSColor(calibratedRed: 0.04, green: 0.36, blue: 0.56, alpha: 1.0).cgColor,
                NSColor(calibratedRed: 0.03, green: 0.20, blue: 0.38, alpha: 1.0).cgColor
            ],
            locations: [0.0, 0.55, 1.0],
            start: CGPoint(x: CGFloat(width) * 0.5, y: seaRect.maxY),
            end: CGPoint(x: CGFloat(width) * 0.5, y: 0)
        )

        // Shimmering sea wave highlights
        for w in 0..<36 {
            let wy = seaRect.maxY - CGFloat(w) * 8.2
            let waveShift = sin(t * 2.4 + CGFloat(w) * 0.7) * 24.0
            let waveWidth = 45.0 + CGFloat(w) * 4.5
            ctx.setFillColor(NSColor(calibratedRed: 1.0, green: 0.88, blue: 0.65, alpha: max(0.08, 0.55 - CGFloat(w) * 0.012)).cgColor)
            ctx.fill(CGRect(x: sunX - waveWidth * 0.5 + waveShift, y: wy, width: waveWidth, height: 2.4))
        }

        // 3. Camera Choreography across 3 Cinematic Shots
        // Shot 1 (0 - 2.5s): Dolly-in & opening French glass balcony doors
        // Shot 2 (2.5 - 5.0s): Stepping out onto the flower-draped balcony overlooking Aegean & Ancient Greece
        // Shot 3 (5.0 - 7.5s): Slow-motion close-up turn-back smile & floating petals
        let doorOpen = min(1.0, max(0.0, t / 2.8))
        let cameraZoom: CGFloat = t < 2.5 ? (1.0 + (t / 2.5) * 0.16) : (t < 5.0 ? (1.16 + ((t - 2.5) / 2.5) * 0.14) : (1.35 + ((t - 5.0) / 2.5) * 0.22))
        let turnBack: CGFloat = t < 4.6 ? 0.0 : min(1.0, (t - 4.6) / 1.4)
        let breeze = sin(t * 2.1) * 0.6 + 0.4

        ctx.saveGState()
        ctx.translateBy(x: CGFloat(width) * 0.5, y: CGFloat(height) * 0.46)
        ctx.scaleBy(x: cameraZoom, y: cameraZoom)
        ctx.translateBy(x: -CGFloat(width) * 0.5, y: -CGFloat(height) * 0.46)

        // Marble balcony balustrade
        ctx.setFillColor(NSColor(calibratedRed: 0.95, green: 0.91, blue: 0.86, alpha: 0.92).cgColor)
        ctx.fill(CGRect(x: 120, y: 170, width: width - 240, height: 14))
        for p in 0..<18 {
            let px = 150.0 + CGFloat(p) * 56.0
            ctx.fill(CGRect(x: px, y: 95, width: 12, height: 75))
        }

        // Girl figure at the center of the balcony
        drawGirlFigure(ctx, cx: CGFloat(width) * 0.52, cy: CGFloat(height) * 0.34, scale: 1.05, turnBack: turnBack, breeze: breeze)

        // French Glass Balcony Doors swinging open
        let doorOffset = doorOpen * 270.0
        let leftDoorX = CGFloat(width) * 0.5 - 260.0 - doorOffset
        let rightDoorX = CGFloat(width) * 0.5 + doorOffset
        for doorX in [leftDoorX, rightDoorX] {
            ctx.setFillColor(NSColor(calibratedRed: 0.96, green: 0.94, blue: 0.90, alpha: 0.32).cgColor)
            ctx.fill(CGRect(x: doorX, y: 60, width: 260, height: 580))
            ctx.setStrokeColor(NSColor(calibratedRed: 0.92, green: 0.86, blue: 0.78, alpha: 0.82).cgColor)
            ctx.setLineWidth(8)
            ctx.stroke(CGRect(x: doorX, y: 60, width: 260, height: 580))
            // Glass panes
            ctx.setLineWidth(3)
            for row in 1..<4 {
                let py = 60.0 + CGFloat(row) * 145.0
                ctx.move(to: CGPoint(x: doorX, y: py))
                ctx.addLine(to: CGPoint(x: doorX + 260, y: py))
            }
            ctx.strokePath()
        }

        // Secret Garden Flower Arch framing the balcony
        for i in 0..<28 {
            let angle = CGFloat(i) / 27.0 * .pi
            let fx = CGFloat(width) * 0.5 + cos(angle) * 430.0
            let fy = CGFloat(height) * 0.42 + sin(angle) * 250.0
            let hue: CGFloat = (i % 3 == 0) ? 0.94 : ((i % 3 == 1) ? 0.08 : 0.85)
            drawFlower(ctx, cx: fx, cy: fy, radius: 22.0 + CGFloat(i % 4) * 4.0, hue: hue, time: t)
        }

        ctx.restoreGState()

        // 4. Floating Flower Petals & Golden Bokeh Particles
        for p in 0..<32 {
            let seed = CGFloat(p * 37)
            let px = (seed * 43.0 + t * (38.0 + CGFloat(p % 5) * 12.0)).truncatingRemainder(dividingBy: CGFloat(width))
            let py = CGFloat(height) - (seed * 29.0 + t * (28.0 + CGFloat(p % 7) * 9.0)).truncatingRemainder(dividingBy: CGFloat(height))
            let radius = 4.0 + CGFloat(p % 4) * 2.5
            ctx.setFillColor(NSColor(calibratedRed: 1.0, green: 0.84, blue: 0.88, alpha: 0.68).cgColor)
            ctx.fillEllipse(in: CGRect(x: px, y: py, width: radius * 1.5, height: radius))
        }

        // Anamorphic Lens Flare & Cinema Letterbox Bars (54px top & bottom)
        if turnBack > 0.1 {
            ctx.setFillColor(NSColor(calibratedRed: 1.0, green: 0.88, blue: 0.65, alpha: 0.18 * turnBack).cgColor)
            ctx.fill(CGRect(x: 0, y: CGFloat(height) * 0.54, width: CGFloat(width), height: 4))
        }

        ctx.setFillColor(NSColor(calibratedWhite: 0.04, alpha: 0.92).cgColor)
        ctx.fill(CGRect(x: 0, y: 0, width: width, height: 56))
        ctx.fill(CGRect(x: 0, y: height - 56, width: width, height: 56))

        // Shot indicator & Bilingual Cinema Subtitle
        let shotLabel: String
        let subtitle: String
        if t < 2.5 {
            shotLabel = "MUSE CINEMA 24FPS · SHOT 01/03 · DOLLY-IN"
            subtitle = "缓缓推开布满繁花宛如神秘花园的阳台玻璃门 · Opening the secret garden balcony doors"
        } else if t < 5.0 {
            shotLabel = "MUSE CINEMA 24FPS · SHOT 02/03 · AEGEAN PANORAMA"
            subtitle = "门外是璀璨爱琴海与远方古希腊遗迹 · Sparkling Aegean Sea & distant ancient Greek marble"
        } else {
            shotLabel = "MUSE CINEMA 24FPS · SHOT 03/03 · SLOW-MOTION CLOSE-UP"
            subtitle = "微风拂发，女孩回眸一笑，怦然心动 · She turns back with a breathtaking, heartwarming smile"
        }

        drawText(ctx, text: shotLabel, x: 28, y: CGFloat(height) - 36, size: 13, color: NSColor(calibratedWhite: 0.88, alpha: 0.9), alignCenter: false, bold: true)
        drawText(ctx, text: "ModelDesk × Muse Video Engine", x: CGFloat(width) - 240, y: CGFloat(height) - 36, size: 13, color: NSColor(calibratedRed: 0.95, green: 0.78, blue: 0.52, alpha: 0.9), alignCenter: false, bold: false)
        drawText(ctx, text: subtitle, x: CGFloat(width) * 0.5, y: 20, size: 16, color: NSColor(calibratedWhite: 0.96, alpha: 0.96), alignCenter: true, bold: false)
    }

    writer.write(Data(buffer))
}

try? writer.close()
process.waitUntilExit()
if process.terminationStatus != 0 {
    exit(process.terminationStatus)
}
print("Generated Muse video:", outputPath)
