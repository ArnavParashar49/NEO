"""Unit tests for core/audio_pipeline.py — PCM helpers, transcript, resampling."""

from __future__ import annotations

from types import SimpleNamespace

from core.audio_pipeline import extract_live_audio


def test_pcm_to_bands_empty():
    from core.audio_pipeline import pcm_to_bands
    bands = pcm_to_bands(b"", n_bands=8)
    assert len(bands) == 8
    assert all(b == 0.0 for b in bands)


def test_pcm_to_bands_valid():
    from core.audio_pipeline import pcm_to_bands
    import numpy as np
    samples = (np.random.randn(1024) * 1000).astype(np.int16).tobytes()
    bands = pcm_to_bands(samples, n_bands=16)
    assert len(bands) == 16
    assert all(0.0 <= b <= 1.0 for b in bands)


def test_resample_pcm16_same_rate():
    from core.audio_pipeline import resample_pcm16
    data = b"\x00\x01\x02\x03"
    assert resample_pcm16(data, 16000, 16000) == data


def test_resample_pcm16_empty():
    from core.audio_pipeline import resample_pcm16
    assert resample_pcm16(b"", 16000, 24000) == b""


def test_resample_pcm16_different_rate():
    from core.audio_pipeline import resample_pcm16
    import numpy as np
    samples = np.array([0, 1000, 2000, 3000], dtype=np.int16).tobytes()
    result = resample_pcm16(samples, 16000, 8000)
    assert len(result) > 0
    assert len(result) < len(samples)


def test_clean_transcript():
    from core.session_manager import _clean_transcript
    assert _clean_transcript("hello<ctrl99> world") == "hello world"
    assert _clean_transcript("  spaced  ") == "spaced"
    assert _clean_transcript("") == ""


def test_merge_transcript():
    from core.session_manager import _merge_transcript
    assert _merge_transcript([], "hello") == ["hello"]
    assert _merge_transcript(["hello"], "hello world") == ["hello world"]
    assert _merge_transcript(["hello"], "hello") == ["hello"]


def test_extract_live_audio_ignores_text_and_thought_parts():
    response = SimpleNamespace(
        server_content=SimpleNamespace(
            model_turn=SimpleNamespace(
                parts=[
                    SimpleNamespace(text="private thought", inline_data=None, thought=True),
                    SimpleNamespace(
                        text=None,
                        thought=False,
                        inline_data=SimpleNamespace(data=b"audio", mime_type="audio/pcm"),
                    ),
                ]
            )
        )
    )

    assert extract_live_audio(response) == b"audio"


def test_input_fallback_prefers_real_microphone(monkeypatch):
    from core import audio_pipeline as audio

    devices = [
        {"name": "Stereo Mix", "max_input_channels": 2},
        {"name": "Line In", "max_input_channels": 2},
        {"name": "Microphone (Realtek)", "max_input_channels": 1},
    ]
    monkeypatch.setattr(audio.sd, "query_devices", lambda index=None: devices if index is None else devices[index])
    monkeypatch.setattr(audio, "_usable_input_device", lambda index: index in {0, 1, 2})

    assert audio._fallback_input_device() == 2


def test_invalid_default_input_uses_fallback(monkeypatch):
    from core import audio_pipeline as audio

    monkeypatch.setattr(audio.sd.default, "device", (99, 5))
    monkeypatch.setattr(audio, "_usable_input_device", lambda index: index == 8)
    monkeypatch.setattr(audio, "_usable_output_device", lambda index: index == 5)
    monkeypatch.setattr(audio, "_fallback_input_device", lambda: 8)

    assert audio.default_audio_devices() == (8, 5)
