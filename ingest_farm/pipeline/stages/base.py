from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gi.repository import Gst

    from ingest_farm.schemas import ChannelConfig


class PipelineStage(ABC):
    """Base class for composable GStreamer pipeline stages."""

    @abstractmethod
    def link(self, upstream: Gst.Element, config: ChannelConfig, ctx: dict[str, Any]) -> Gst.Element:
        """Attach this stage after upstream and return the new tail element."""


class SourceStage(PipelineStage):
    """Creates the pipeline source element(s). upstream is ignored."""


class ProcessingStage(PipelineStage):
    """Transforms media between source and output (passthrough, demux, encode)."""


class OutputStage(PipelineStage):
    """Terminal stage — typically a splitmuxsink or filesink."""
