"""Parser interface for AviAtlas document parsing."""

from abc import ABC, abstractmethod
from pathlib import Path

from aviatlas.parsing.models import ParsedDocument


class DocumentParser(ABC):
    """Interface for converting source documents into AviAtlas's document model."""

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedDocument:
        """Parse a source document into AviAtlas's internal representation."""
        raise NotImplementedError