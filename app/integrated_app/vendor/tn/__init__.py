"""tn (Text Normalization) vendor stub package.

This package provides a pass-through Normalizer implementation for environments
where pynini/WeTextProcessing cannot be compiled (e.g., Windows + MSVC).

When pynini is available (conda-forge install), the real tn package should
take precedence. This stub only serves as a fallback to prevent ImportError.
"""
