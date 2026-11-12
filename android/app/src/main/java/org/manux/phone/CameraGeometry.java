package org.manux.phone;

/** Rotate the luma and interleaved VU planes together, without decoding a JPEG. */
final class CameraGeometry {
    static byte[] uprightNv21(byte[] source, int width, int height, int rotation) {
        if (width <= 0 || height <= 0 || width % 2 != 0 || height % 2 != 0
                || source.length < width * height * 3 / 2
                || (rotation != 0 && rotation != 90 && rotation != 180 && rotation != 270))
            throw new IllegalArgumentException("Invalid NV21 geometry");
        if (rotation == 0) return source;
        byte[] target = new byte[width * height * 3 / 2];
        rotatePlane(source, target, 0, width, height, 1, rotation);
        rotatePlane(source, target, width * height, width / 2, height / 2, 2, rotation);
        return target;
    }

    private static void rotatePlane(byte[] source, byte[] target, int offset, int width, int height, int stride, int rotation) {
        int outputWidth = rotation == 180 ? width : height;
        for (int y = 0; y < height; y++) {
            for (int x = 0; x < width; x++) {
                int nx = rotation == 90 ? height - 1 - y : rotation == 180 ? width - 1 - x : y;
                int ny = rotation == 90 ? x : rotation == 180 ? height - 1 - y : width - 1 - x;
                int from = offset + (y * width + x) * stride;
                int to = offset + (ny * outputWidth + nx) * stride;
                for (int channel = 0; channel < stride; channel++) target[to + channel] = source[from + channel];
            }
        }
    }

    private CameraGeometry() { }
}
