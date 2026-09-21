"""Privacy and capture control layer for OpenRecall.

Provides centralized pause/resume state management and privacy-safe logging.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class PrivacyPolicy:
    """Centralized privacy and capture control state for OpenRecall.

    Attributes:
        _is_paused: Internal boolean flag indicating if capture is paused.
    """

    def __init__(self):
        self._is_paused: bool = False

    def pause(self) -> None:
        """Pauses the capture pipeline."""
        self._is_paused = True

    def resume(self) -> None:
        """Resumes the capture pipeline."""
        self._is_paused = False

    def is_paused(self) -> bool:
        """Returns True if capture pipeline is currently paused."""
        return self._is_paused

    def should_capture(self) -> bool:
        """Evaluates whether capture is permitted (returns False if paused)."""
        return not self._is_paused


def log_privacy_pause() -> None:
    """Emits a privacy-safe log message when capture is skipped due to pause."""
    logger.debug("Capture skipped while paused")


# Global singleton instance
_privacy_policy_instance: Optional[PrivacyPolicy] = None


def get_privacy_policy() -> PrivacyPolicy:
    """Returns the global active PrivacyPolicy singleton."""
    global _privacy_policy_instance
    if _privacy_policy_instance is None:
        _privacy_policy_instance = PrivacyPolicy()
    return _privacy_policy_instance


def reset_privacy_policy() -> None:
    """Resets the global PrivacyPolicy instance to defaults (useful for test isolation)."""
    global _privacy_policy_instance
    _privacy_policy_instance = PrivacyPolicy()
