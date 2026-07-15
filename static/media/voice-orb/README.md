# Voice Orb media

`motivational-abstract.webm` is an original, silent demonstration loop generated
entirely from FFmpeg's `gradients` source. It contains no downloaded footage,
audio, voice, actor likeness, font, or third-party artwork.

The Odysseus Voice Orb contributors dedicate this asset to the public domain
under CC0 1.0. The allowlist metadata and immutable SHA-256 checksum live in
`/static/voice-orb-media.json`.

Reproduction command:

```sh
ffmpeg -f lavfi -i "gradients=s=1024x576:r=30:d=6:c0=0x030712:c1=0x6a32ff:c2=0x00d6e6:c3=0xffb45c:n=4:t=spiral:speed=0.08" -an -c:v libvpx-vp9 -crf 38 -b:v 0 -row-mt 1 -pix_fmt yuv420p motivational-abstract.webm
```
