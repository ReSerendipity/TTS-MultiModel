#!/usr/bin/env python
"""
Test script to verify that all new modules can be imported and basic functionality works.
This script checks syntax, imports, and basic class/function availability without requiring full dependencies.
"""

import os
import sys

import pytest

pytestmark = pytest.mark.integration

# Add the integrated_app directory to the path
_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)


def test_module_imports():
    """Test that all new modules can be imported."""
    # Test GPU utils
    from integrated_app.gpu_utils import free_gpu_memory, is_oom_error

    assert callable(is_oom_error)
    assert callable(free_gpu_memory)

    # Test model registry
    from integrated_app.model_registry import ModelRegistry

    assert ModelRegistry is not None

    # Test config models
    from integrated_app.config_models import AppConfig

    assert AppConfig is not None

    # Test estimator
    from integrated_app.estimator import GenerationTimeEstimator

    assert GenerationTimeEstimator is not None

    # Test engine interface
    from integrated_app.engine_interface import ControllableTTSEngine, TTSEngine

    assert TTSEngine is not None
    assert ControllableTTSEngine is not None

    # Test monitor
    from integrated_app.monitor import HealthMonitor, get_health_monitor

    assert HealthMonitor is not None
    assert callable(get_health_monitor)

    # Test audio processing
    from integrated_app.audio_processing import enhance_audio, normalize_loudness

    assert callable(enhance_audio)
    assert callable(normalize_loudness)

    # Test history db
    from integrated_app.history_db import HistoryDatabase, create_history_db

    assert HistoryDatabase is not None
    assert callable(create_history_db)

    # Test persona metadata
    from integrated_app.persona_metadata import PersonaExporter, PersonaMetadata, load_persona_metadata

    assert PersonaMetadata is not None
    assert PersonaExporter is not None
    assert callable(load_persona_metadata)


def test_basic_functionality():
    """Test basic functionality of key components."""
    # Test GenerationTimeEstimator
    import tempfile

    from integrated_app.estimator import GenerationTimeEstimator

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        temp_file = f.name

    try:
        estimator = GenerationTimeEstimator(data_file=temp_file, max_entries=10)

        # Record some dummy data
        estimator.record(100, 5.0, "voxcpm2", segment_count=1)
        estimator.record(200, 8.0, "voxcpm2", segment_count=1)

        # Test estimation
        est_time = estimator.estimate(150, segment_count=1)
        assert est_time is not None, "Estimation should not be None"
        assert est_time > 0, "Estimation should be positive"
    finally:
        os.unlink(temp_file)

    # Test HistoryDB
    from integrated_app.history_db import create_history_db

    with tempfile.TemporaryDirectory() as temp_dir:
        db = create_history_db(temp_dir)

        # Insert a record
        record = {
            "filename": "test.wav",
            "filepath": "/test/test.wav",
            "created_at": "2024-01-01T00:00:00",
            "file_size_bytes": 1024,
            "duration_seconds": 5.5,
            "text_preview": "Hello world",
            "engine": "voxcpm2",
            "model_type": "声音设计",
            "model_size": "VoxCPM2",
            "persona_name": None,
            "output_format": "wav",
            "is_success": True,
            "error_msg": None,
        }

        db.insert(record)

        # Query records
        records = db.query(limit=10)
        assert len(records) == 1, f"Should have 1 record, got {len(records)}"
        assert records[0]["filename"] == "test.wav"

    # Test PersonaMetadata
    from integrated_app.persona_metadata import PersonaMetadata

    meta = PersonaMetadata(
        name="Test Voice",
        description="A test voice",
        tags=["female", "young"],
        category="Custom",
        voice_type="Sweet",
        traits="Clear and bright",
    )

    # Test serialization
    data = meta.to_dict()
    assert data["name"] == "Test Voice"
    assert "female" in data["tags"]

    # Test deserialization
    meta2 = PersonaMetadata.from_dict(data)
    assert meta2.name == meta.name
    assert meta2.description == meta.description
    assert meta2.tags == meta.tags


if __name__ == "__main__":
    print("=" * 50)
    print("TTS MultiModel - Integration Test Suite")
    print("=" * 50)

    success = True

    # Test imports
    if not test_module_imports():
        success = False

    # Test functionality (only if imports passed)
    if success and not test_basic_functionality():
        success = False

    print("\n" + "=" * 50)
    if success:
        print("All tests passed!")
    else:
        print("Some tests failed!")
    print("=" * 50)

    sys.exit(0 if success else 1)
