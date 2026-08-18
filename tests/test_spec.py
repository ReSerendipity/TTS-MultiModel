"""tests for app.integrated_app.spec (TTS spec contract layer)."""
import pytest

from integrated_app import spec


def test_duration_to_samples():
    assert spec.duration_to_samples(1.0) == 48000
    assert spec.duration_to_samples(0.5) == 24000


def test_samples_to_duration():
    assert spec.samples_to_duration(48000) == 1.0
    assert spec.samples_to_duration(24000) == 0.5


def test_is_valid_text_length():
    assert spec.is_valid_text_length("a" * 100)
    assert spec.is_valid_text_length("a" * 200)
    assert not spec.is_valid_text_length("a" * 49)   # below MIN
    assert not spec.is_valid_text_length("a" * 201)  # above MAX


def test_split_text_long():
    assert spec.split_text_long("a" * 100) == ["a" * 100]
    assert spec.split_text_long("a" * 450) == ["a" * 200, "a" * 200, "a" * 50]


def test_supported_engine_names():
    names = spec.supported_engine_names()
    assert "voxcpm2" in names
    assert "indextts2" in names


def test_engine_spec_sample_rate():
    assert spec.ENGINES["voxcpm2"].sample_rate == 48000
    assert spec.ENGINES["indextts2"].requires_gpu is True
