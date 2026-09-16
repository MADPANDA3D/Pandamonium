# Myinstants soundboard — MAD-968

Myinstants supplies native search, detail, recent sounds and owner favorites. Settings → Soundboard provides search, preview, favorites and independent effects volume/mute. Disabling preserves favorites; removing hides the tab while preserving owner data. Website-account favorites are separate.

Jarvis retrieves a sound and places its returned `[[sound:ID]]` immediately after the selected word, before sentence punctuation. Text shows a titled manual play/pause button, never autoplay. Streaming/history share the renderer. Persistence validates resolved identities and stores distinct message/session/cue IDs and UTF-16 positions. Code examples do not become sounds; speech cleanup removes markers.

## Voice

The current owner's enabled soundboard is available in voice mode without a separate engagement click. The speech turn resolves cues against the exact cleaned spoken text and repeated-word occurrence. Its existing sentence blocks and PCM schedule are unchanged.

The local Chatterbox Turbo patch adds optional `return_alignment=True` to the existing generation. Observed Q/K projections yield approximate sample positions without a second model or changed sampling. Bounded `cbtm` RIFF metadata carries normalized text, word spans, sample rate and end samples beside unchanged speech PCM. The application validates it and supplies matching block cues. Markers arriving after a block was emitted reuse its retained word clock without delaying or resynthesizing speech. Missing or inconsistent timings skip the effect with a visible notice and server warning.

The browser starts fetching the effect alongside synthesis, then schedules a separate source/gain node using the existing speech frame clock. Cue IDs deduplicate playback. Interrupt, call end, disable and mute cancel pending/playing effects. Effects over 15 seconds or 8 MB are rejected; downloads more than 150 ms late skip visibly without stalling speech. Effect tails finish before the existing call resumes microphone capture. No speech ducking, resynthesis, changed microphone settings or Whisper.

Timing patch: [Chatterbox PR563](https://github.com/resemble-ai/chatterbox/pull/563), Leo Lara/MADPANDA3D, commit `3bae920df5b576fa63c342d6373e523fb30c7e2d`. Deploy the patch through the existing PC Chatterbox service; its `/health` exposes `alignment: turbo-attention-v1`. Ordinary Chatterbox callers retain the tensor-returning default.

## Preparation and verification

```sh
.venv/bin/python scripts/prepare_soundboard_package.py --output /tmp/soundboard-package
sh scripts/check_package_runtimes.sh .venv/bin/python tests/deployed/soundboard_native.py /tmp/soundboard-package /tmp/soundboard-proof
```

Use fresh output directories. Preparation, installation, search, favorites and playback make zero model calls. The real native check covers install, Myinstants search/detail, MP3 retrieval, owner isolation, persistence, disable/enable/remove and retained favorites. The recipe pins upstream `435d40d309ab2790e21567b00c9727509badeedd`, verifies TLS and uses distro PHP CLI/curl where installed, with a checksum-pinned Arch PHP fallback. Missing runtime prerequisites fail setup. An enabled service recovers on its next use after installer or app exit only when its owner, package digest and configuration still match the saved validation; disabled or changed packages cannot restart, and active scopes with invalid resource limits fail closed.

Final package 1.1.1: SHA-256 `b6102d0e58b826442e90ce300ad2d1b6bec9258c53a8d52db6d1b9f69c64cd81`, 28,002 bytes; tree digest `f3cbab6a060d203a2f086992f618cd3b4420b22261618e54eb0b7f1c5da80ea6`. A checksum-pinned static curl 8.21.0 performs bounded, certificate-verified requests inside the package sandbox; this fixes the older Debian client being rejected by Myinstants. The same native lifecycle and real MP3 retrieval pass on both the workstation and CT103. Signed marketplace publication independently revalidates all four operations. Minimum application version is 1.0.68; no production compatibility override.

Verified evidence:

- Live native lifecycle and Myinstants operations; 21,230-byte Vine Boom MP3; foreign owner denied and favorites retained across disable/enable/removal.
- Real Settings/manual text playback, saved response rendering and reload. Manual browser-stream loopback correlated 0.9999999994 with the selected sound.
- Six real Turbo baseline/observer pairs (three sentences, seeds 968/969) preserved waveform and vocoder tokens exactly. Browser-loopback effects matched all six scheduled positions, correlation 0.999994–0.9999998. Leo listened and explicitly accepted the behavior: “That is working exactly how I want it to - no that was a success.” This supersedes the earlier strict acoustic-boundary classification; approximate spectrogram estimates are diagnostic, not an acceptance veto.
- Actual authenticated app, self-hosted GPT-OSS with no paid fallback: Jarvis called the native favorite tool, inserted its cue after the second boom, and the normal Chatterbox/PCM/browser path scheduled one separate effect while two contiguous speech frames played. An initial timing spike and late download were reproduced and fixed; all six previously accepted cue positions remain unchanged.
- Final focused Python soundboard/PCM/session checks: 40 passed; four Chromium soundboard checks cover preview/favorites/mute, text/history, and independent cue clock/deduplication/cancellation/late handling. Both existing Node voice lifecycle/chunk checks pass. Full exact-candidate CI is the release gate.

Sanitized early receipts: [proof.json](evidence/mad-968/proof.json). Raw private audio remains local under `/tmp/mad968-real/` and `/tmp/mad968-service/`; it is not published in the repository. The full-app test uses fake microphone hardware and a test entry into the existing turn function, actual model/tools/TTS/PCM/playback, and no generated-response/audio mocks. It does not certify acoustic echo cancellation on every physical microphone.

![Actual Settings](evidence/mad-968/settings-live.png)
![Actual self-hosted Jarvis response](evidence/mad-968/jarvis-response-live.png)

## Release scope and rollback

The requested 1.0.68 train includes the already-merged MAD-957–MAD-963 infrastructure plus this scoped feature. Paused MAD-964/PR272 is excluded; this task installs only Myinstants. Public package, signed application release and installed deployment are separate facts; current installed readback and user feedback are tracked in MAD-968.

CT103 requires the native runtime's persistent bounded filesystem, bubblewrap, systemd user manager/cgroup controllers and distro PHP CLI/curl. The scoped setup adds a dedicated 2 GiB volume under app data and a service environment drop-in, retaining security/resource checks. The protected updater backs up app data/config and preserves signed v1.0.67 plus its rollback snapshot. The PC retains its original Chatterbox image, service unit and source for reversal. Never hand-edit an immutable live release, replace model defaults, delete owner data or continue to another plugin automatically.
