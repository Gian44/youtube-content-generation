# The narrator's voice (since 2026-10-25)

`narrator.wav` is the channel's own voice: an original voice **designed from a text description** with
Qwen3-TTS-12Hz-1.7B-VoiceDesign (Apache-2.0) on Gian's PC — it is not a copy of any person or product voice.
Gian picked it ("C · Velvet low London") from four designs. Description used:

> A man in his forties with a smooth, low, velvety voice and a light London accent. Quiet and intimate, very
> relaxed pace, as if speaking close to a microphone late at night.

Every night the pipeline speaks the script in this voice with Kyutai's Pocket TTS (weights CC-BY-4.0, gated on
Hugging Face — the workflow uses the `HF_TOKEN` secret; its terms forbid cloning anyone without consent, which
this designed voice satisfies). Kokoro (`bm_george:0.6,bm_lewis:0.4`) is the automatic fallback.
