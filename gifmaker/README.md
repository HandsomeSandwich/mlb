# GIF Maker 🎞️

A make-a-GIF app for your phone. Add pictures from your camera roll or find
them with the built-in image search, put text on them, and save an animated
GIF you can drop straight into a message.

It's a web app, so there's no app store and nothing to install — you open it in
your phone's browser and add it to your home screen, after which it opens full
screen like any other app and works without a connection.

## What it does

- **Pictures in** — pick several from your camera roll at once, or take one on
  the spot. Big photos are shrunk on the way in, and sideways ones are turned
  the right way up.
- **Image search** — search Wikimedia Commons or Openverse without signing up
  for anything. Add a free Pexels key in Settings if you want stock
  photography too.
- **Text like a word processor** — bold, italic, underline, font, size,
  colour, alignment, ALL CAPS, plus the things captions actually need:
  outline, highlight, drop shadow, line spacing, rotation and opacity. Drag
  the text around the picture with your finger.
- **Per-frame or every-frame text** — a caption can sit on the whole animation
  or on one frame only, which is how you make a punchline land.
- **Timing** — set the speed for all frames at once or tune a single frame.
  Reverse the order, duplicate a frame to hold it longer, or add a bounce so
  the loop runs back to the start.
- **Shape and size** — square, portrait, tall, landscape or wide; fill the
  frame or letterbox it; pick the background colour. Drop the size or the
  colour count when you need a smaller file.
- **Saving** — "Share" hands the GIF to Messages, Photos or anywhere else on
  iOS and Android. Otherwise press and hold the preview to save it.

Your work is kept on the device as you go, so closing the tab doesn't lose it.

## Getting it onto your phone

### The quick way — over your wi-fi

On your computer:

```bash
python3 gifmaker/serve.py
```

It prints two addresses. Type the `http://192.168.…:8420/` one into your
phone's browser while both are on the same wi-fi.

- **iPhone:** Share → *Add to Home Screen*
- **Android:** menu → *Install app*

Nothing to install — it's Python's standard library only. The script serves
the app and adds two small helpers: a server-side image search and an image
downloader, used automatically when a search provider's CORS rules stop the
browser fetching something directly.

Your computer needs to be running for the *search* to work over this route.
Everything else — your own photos, text, export — works offline once the app
is on your home screen.

### The permanent way — host the files

The app is plain static files with no build step, so `gifmaker/` can be
dropped on any static host (GitHub Pages, Netlify, an S3 bucket) and used from
anywhere. Image search then talks to the providers directly from the browser.
It must be served over **https** — a service worker, and therefore home-screen
installation, won't run otherwise.

## How it works

| File | What's in it |
| --- | --- |
| `index.html`, `app.css` | the interface — phone-first, 44px tap targets |
| `app.js` | state, canvas rendering, text layers, dragging, autosave |
| `gif-encoder.js` | GIF89a encoder: median-cut palettes, dithering, LZW |
| `gif-worker.js` | runs the encoder off the UI thread so nothing freezes |
| `search.js` | image search providers and CORS-safe downloading |
| `serve.py` | local server plus the search/download helpers |
| `sw.js`, `manifest.webmanifest` | offline caching, home-screen install |
| `icons/make_icons.py` | regenerates the app icons (they're committed) |

There are no third-party libraries and no CDN links, which is why the whole
thing works with the network off.

Every frame is drawn on a canvas — background, then the picture, then the text
layers — and the same drawing code produces both the preview and the exported
frames, so the GIF matches what you saw.

The encoder gives each frame its own 256-colour palette chosen by median cut,
optionally with Floyd–Steinberg dithering, then compresses it with the
variable-width LZW that the GIF format requires.

### Tests

`test_gif_encoder.py` (in the repository root, and part of CI) encodes fixture
frames with node and decodes them again with a GIF parser written from scratch
in Python, checking the palettes, frame delays, looping and pixels all survive
the round trip.

```bash
python3 test_gif_encoder.py
```

## Notes and limits

- Up to 60 frames; imported pictures are resized to 1280px on the longest edge.
- GIF plays no faster than about 20ms per frame — that's the format, not the app.
- Only still pictures can be imported; an animated GIF used as a source
  contributes its first frame.
- Search results come from other people's collections. Wikimedia and Openverse
  results show the creator and licence — check them before you post anything
  publicly.
