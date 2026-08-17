/*
 * Encoding worker. Quantising and LZW-compressing a few hundred thousand
 * pixels per frame will freeze a phone's UI thread for seconds, so all of it
 * happens here and the page just listens for progress.
 *
 * Message in:  { frames: [{ data: Uint8ClampedArray, delayMs }], width, height, options }
 * Messages out: { type: 'progress', done, total } then { type: 'done', bytes }
 */
importScripts('gif-encoder.js');

self.onmessage = function (event) {
  var msg = event.data || {};
  var frames = msg.frames || [];
  var width = msg.width;
  var height = msg.height;
  var options = msg.options || {};

  try {
    var quantised = [];
    for (var i = 0; i < frames.length; i++) {
      var frame = GifEncoder.quantizeFrame(frames[i].data, width, height, options);
      frame.delayMs = frames[i].delayMs;
      quantised.push(frame);
      // Quantisation is the slow half, so report against it.
      self.postMessage({ type: 'progress', done: i + 1, total: frames.length });
    }

    var bytes = GifEncoder.writeGif(quantised, width, height, options);
    self.postMessage({ type: 'done', bytes: bytes.buffer }, [bytes.buffer]);
  } catch (err) {
    self.postMessage({ type: 'error', message: String((err && err.message) || err) });
  }
};
