"""English text normalizer stub (pass-through).

This module provides a minimal Normalizer class that passes text through
with only whitespace stripping. It is used as a fallback when the real
pynini/WeTextProcessing-based tn package is not available.

For full normalization (numbers, dates, etc.), install:
    conda install -c conda-forge pynini we_text_processing
"""

import re


class Normalizer:
    """Pass-through English text normalizer.

    Strips leading/trailing whitespace and collapses multiple spaces.
    Does NOT perform number/date/abbreviation normalization.
    """

    def normalize(self, text: str) -> str:
        """Normalize text by stripping whitespace only.

        Args:
            text: Input text to normalize.

        Returns:
            Text with leading/trailing whitespace removed and
            internal whitespace collapsed to single spaces.
        """
        if not text:
            return ""
        return re.sub(r"\s+", " ", text).strip()
