from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gi.repository import Gst


class EncoderPlugin(ABC):
    """Pluggable video/audio encoder — GStreamer native or external SDK."""

    name: str

    @abstractmethod
    def build_video_element(self, config: dict[str, Any]) -> Gst.Element:
        raise NotImplementedError

    @abstractmethod
    def build_audio_element(self, config: dict[str, Any]) -> Gst.Element:
        raise NotImplementedError


class FrameRateConverterPlugin(ABC):
    """Pluggable frame-rate conversion stage (e.g. Insync)."""

    name: str

    @abstractmethod
    def build_element(self, config: dict[str, Any]) -> Gst.Element:
        raise NotImplementedError
