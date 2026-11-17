import CoreImage
import UIKit
import Vision

/// "Remove background" for shopping photos: Vision's subject lifting (the
/// same as long-pressing a photo in Photos), all on the device.
enum BackgroundRemover {
    /// The subject on a transparent square with a little margin, so square
    /// thumbnails show the whole product; nil when Vision finds no subject.
    static func cutOut(_ image: UIImage) async -> UIImage? {
        // Bake any rotation in first so the mask and the pixels agree.
        let upright = image.imageOrientation == .up ? image : image.shrunk(maxSide: 1200)
        guard let cgImage = upright.cgImage else { return nil }
        return await Task.detached(priority: .userInitiated) { () -> UIImage? in
            let request = VNGenerateForegroundInstanceMaskRequest()
            let handler = VNImageRequestHandler(cgImage: cgImage)
            guard (try? handler.perform([request])) != nil,
                  let result = request.results?.first, !result.allInstances.isEmpty,
                  let buffer = try? result.generateMaskedImage(ofInstances: result.allInstances, from: handler,
                                                               croppedToInstancesExtent: true)
            else { return nil }
            let masked = CIImage(cvPixelBuffer: buffer)
            guard let cut = CIContext().createCGImage(masked, from: masked.extent) else { return nil }
            return padded(UIImage(cgImage: cut))
        }.value
    }

    /// At most 900 px a side, which keeps the PNG well under Core's 4 MB cap.
    private static func padded(_ image: UIImage) -> UIImage {
        let longest = max(image.size.width, image.size.height)
        let side = min(longest * 1.12, 900)
        let fit = side / 1.12 / longest
        let drawn = CGSize(width: image.size.width * fit, height: image.size.height * fit)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = false
        return UIGraphicsImageRenderer(size: CGSize(width: side, height: side), format: format).image { _ in
            image.draw(in: CGRect(x: (side - drawn.width) / 2, y: (side - drawn.height) / 2,
                                  width: drawn.width, height: drawn.height))
        }
    }
}
