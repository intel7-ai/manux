// Camera brightness for the low-light warning (hand-mouse/src/qt_runtime.py: 64x36 gray mean, dark below 70).
export const LOW_LIGHT_THRESHOLD = 70;
const SAMPLE_WIDTH = 64, SAMPLE_HEIGHT = 36;

/** Mean luma (0..255) of RGBA pixel data; -1 when there are no pixels. */
export function meanLuma(data) {
  const pixels = Math.floor((data?.length ?? 0) / 4);
  if (!pixels) return -1;
  let sum = 0;
  // ITU-R BT.601, the weights OpenCV's BGR2GRAY uses in the Qt app.
  for (let i = 0; i < pixels * 4; i += 4) sum += .299 * data[i] + .587 * data[i + 1] + .114 * data[i + 2];
  return sum / pixels;
}

export function brightnessReading(level, threshold = LOW_LIGHT_THRESHOLD) {
  if (!(level >= 0)) return {level:-1, percent:null, lowLight:false};
  return {level, percent:Math.round(level / 2.55), lowLight:level < threshold};
}

/** Samples a downscaled frame of a playing <video>. Only the average leaves this function. */
export class BrightnessSampler {
  constructor(document = globalThis.document) {
    this.canvas = document?.createElement('canvas');
    if (this.canvas) { this.canvas.width = SAMPLE_WIDTH; this.canvas.height = SAMPLE_HEIGHT; }
    this.context = this.canvas?.getContext('2d', {willReadFrequently:true});
  }
  sample(video) {
    if (!this.context || !video || video.readyState < 2) return brightnessReading(-1);
    try {
      this.context.drawImage(video, 0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT);
      return brightnessReading(meanLuma(this.context.getImageData(0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT).data));
    } catch { return brightnessReading(-1); }
  }
}
