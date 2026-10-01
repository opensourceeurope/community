# Logo assets

This directory holds the Open Source Europe logo as a vector source and a raster
copy of each variant. Every file has a transparent background.

Two shapes appear below. The full logo is the eight dots next to the words "open
source europe". The mark is the eight dots on their own.

## Files

Pick the variant by the background you are placing it on.

| File | Size | Put it on |
|---|---|---|
| `ose-logo-white.svg` | 119x50 | A dark background |
| `ose-logo-white.png` | 1190x500 | A dark background |
| `ose-logo-gradient.svg` | 119x50 | A light background |
| `ose-logo-gradient.png` | 1190x500 | A light background |
| `ose-mark-gradient.svg` | 50x50 | A square crop, such as an avatar or a favicon |
| `ose-mark-gradient.png` | 512x512 | A square crop, such as an avatar or a favicon |

The white logo is white throughout, so it disappears on a light page. GitHub
renders a transparent PNG on white in light mode, so `ose-logo-white.png` looks
empty in a file preview even though the artwork is there.

The SVG of each pair is the source. Scale that, and regenerate the PNG from it
rather than resizing the PNG.

## The gradient

Both gradient files carry one horizontal ramp, from `#043E5C` on the left to
`#F7AD1A` on the right. The ramp spans the width of the artwork it fills. On the
full logo it reaches amber only at the last letters of "europe". On the mark it
reaches amber at the right-hand dots.

`ose-mark-gradient.svg` reproduces the GitHub organization avatar, and both stops
come from measuring that image. The application form uses `#F7AD1A` as well, in
`automation/n8n/form-ose.json`.

## Where these came from

`ose-logo-white.svg` is an unchanged copy of `chat/ose-logo.svg` from
[opensourceeurope/ose-knowledge-mcp](https://github.com/opensourceeurope/ose-knowledge-mcp).
The two gradient files reuse its path data and change only the fill. Nobody
redrew the artwork here, so a change to the shapes belongs upstream first.

## Regenerating the PNGs

The following commands rebuild the three raster files from their SVG sources.
They need `rsvg-convert`, which ships with librsvg.

```bash
rsvg-convert -w 1190 -h 500 -o ose-logo-white.png    ose-logo-white.svg
rsvg-convert -w 1190 -h 500 -o ose-logo-gradient.png ose-logo-gradient.svg
rsvg-convert -w 512  -h 512 -o ose-mark-gradient.png ose-mark-gradient.svg
```

## Licence

This directory sits outside `automation/`, so the repository licenses it under
[CC-BY-4.0](../LICENSE) along with the rest of the content. A logo is also a
trademark. CC-BY-4.0 lets anyone alter the mark and publish the result, which is
a right a trademark owner usually keeps. To ask instead that people use the logo
unchanged, add a licence note to this directory and a third entry to the root
README.
