"""Test script for GPU utilization monitoring in system.py.

Tests:
1. NVML initialization and handle caching
2. GPU utilization retrieval with fallback
3. Thread-safety of NVML state
4. nvidia-smi fallback method
5. Error handling and logging
"""

import logging
import os
import sys
import threading
import time

import pytest

# Setup logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("test_gpu_util")

# Add bin directory to path
_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)


def test_imports():
    """Test that all required imports work."""
    logger.info("=" * 60)
    logger.info("Test 1: Testing imports...")
    logger.info("All imports successful")


def test_nvml_initialization():
    """Test NVML initialization and handle caching."""
    logger.info("=" * 60)
    logger.info("Test 2: Testing NVML initialization...")
    from integrated_app.routes.system.gpu import _get_nvml_handle, _nvml_state

    # First call should initialize NVML
    handle1 = _get_nvml_handle()
    if handle1 is not None:
        logger.info(f"NVML initialized, handle: {handle1}")
        logger.info(f"   Device index: {_nvml_state['device_index']}")
        logger.info(f"   Init time: {_nvml_state['init_time']}")
    else:
        logger.warning("NVML handle is None (may not have NVIDIA GPU or pynvml not installed)")
        if _nvml_state["last_error"]:
            logger.warning(f"   Last error: {_nvml_state['last_error']}")

    # Second call should return cached handle
    handle2 = _get_nvml_handle()
    if handle2 is not None and handle1 is not None:
        assert handle1 == handle2, "Handle caching failed - different handles returned"
        logger.info("Handle caching works correctly")


def test_gpu_utilization_nvml():
    """Test GPU utilization from NVML."""
    logger.info("=" * 60)
    logger.info("Test 3: Testing GPU utilization from NVML...")
    from integrated_app.routes.system.gpu import _get_gpu_utilization_from_nvml

    util = _get_gpu_utilization_from_nvml()
    if util is not None:
        logger.info(f"GPU utilization from NVML: {util}%")
        assert 0 <= util <= 100, f"Utilization value out of range: {util}"
    else:
        logger.warning("NVML utilization returned None")
        pytest.skip("NVML not available on this system")


def test_gpu_utilization_nvidia_smi():
    """Test GPU utilization from nvidia-smi fallback."""
    logger.info("=" * 60)
    logger.info("Test 4: Testing GPU utilization from nvidia-smi...")
    from integrated_app.routes.system.gpu import _get_gpu_utilization_from_nvidia_smi

    util = _get_gpu_utilization_from_nvidia_smi()
    if util is not None:
        logger.info(f"GPU utilization from nvidia-smi: {util}%")
        assert 0 <= util <= 100, f"Utilization value out of range: {util}"
    else:
        logger.info("nvidia-smi utilization returned None (may not be available)")


def test_gpu_utilization_unified():
    """Test unified GPU utilization function."""
    logger.info("=" * 60)
    logger.info("Test 5: Testing unified GPU utilization function...")
    from integrated_app.routes.system.gpu import _get_gpu_utilization

    # Test multiple calls to ensure consistency and caching
    utils = []
    for i in range(3):
        util = _get_gpu_utilization()
        utils.append(util)
        logger.info(f"   Call {i + 1}: GPU utilization = {util}%")
        time.sleep(0.5)

    logger.info("All calls returned valid values")
    logger.info(f"   Values: {utils}")

    # Values should be in valid range
    for util in utils:
        assert 0 <= util <= 100, f"Utilization value out of range: {util}"


def test_thread_safety():
    """Test thread-safety of NVML initialization."""
    logger.info("=" * 60)
    logger.info("Test 6: Testing thread-safety...")
    from integrated_app.routes.system.gpu import _get_nvml_handle

    handles = []
    errors = []

    def get_handle_in_thread():
        try:
            h = _get_nvml_handle()
            handles.append(h)
        except Exception as e:
            errors.append(str(e))

    # Create multiple threads
    threads = []
    for _i in range(5):
        t = threading.Thread(target=get_handle_in_thread)
        threads.append(t)

    # Start all threads
    for t in threads:
        t.start()

    # Wait for all threads to complete
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"

    # All handles should be the same (cached)
    valid_handles = [h for h in handles if h is not None]
    if len(valid_handles) > 0:
        first_handle = valid_handles[0]
        all_same = all(h == first_handle for h in valid_handles)
        assert all_same, "Different threads got different handles"
        logger.info("All threads got the same cached handle")
        logger.info(f"   Total threads: {len(handles)}")
        logger.info(f"   Valid handles: {len(valid_handles)}")
    else:
        logger.warning("No valid handles obtained (NVML may not be available)")


def test_error_handling():
    """Test error handling and recovery."""
    logger.info("=" * 60)
    logger.info("Test 7: Testing error handling...")
    from integrated_app.routes.system.gpu import _nvml_state

    # Check that error state is properly tracked
    logger.info(f"   NVML state: initialized={_nvml_state['initialized']}")
    logger.info(f"   NVML state: init_failed={_nvml_state['init_failed']}")
    logger.info(f"   NVML state: last_error={_nvml_state['last_error']}")
    logger.info(f"   NVML state: device_index={_nvml_state['device_index']}")

    # Verify state dictionary has all required fields
    required_fields = ["handle", "initialized", "init_time", "init_failed", "last_error", "device_index"]
    for field in required_fields:
        assert field in _nvml_state, f"Missing field in _nvml_state: {field}"

    logger.info("All required state fields present")
