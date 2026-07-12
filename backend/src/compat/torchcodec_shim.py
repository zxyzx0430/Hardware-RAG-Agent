"""torchcodec compatibility shim for self-hosted Windows environments.

sentence-transformers >= 5.5 unconditionally imports torchcodec for video/audio
decoding features that Hardware RAG Agent does not use. torchcodec in turn
requires FFmpeg shared libraries (avcodec/avformat/avutil) which are often not
installed on Windows self-hosted environments.

This shim attempts to import the real torchcodec. If that fails because FFmpeg
is missing, it installs a minimal stub in sys.modules so that the rest of the
import chain (sentence-transformers -> transformers) can proceed. The stub does
not provide any real decoding functionality, but our RAG pipeline only uses
sentence-transformers for text embedding/reranking, so this is safe.

To use the real torchcodec, install FFmpeg full-shared build and ensure its
bin directory is on PATH.
"""

import importlib.util
import logging
import sys
import types

logger = logging.getLogger(__name__)

_TORCHCODEC_STUBBED = False


def _install_stub() -> None:
    """Install a minimal torchcodec stub module in sys.modules."""
    global _TORCHCODEC_STUBBED
    if _TORCHCODEC_STUBBED:
        return
    if "torchcodec" in sys.modules:
        return

    stub = types.ModuleType("torchcodec")
    stub.__spec__ = importlib.util.spec_from_loader("torchcodec", loader=None)
    stub.__file__ = __file__

    for sub in ("decoders", "encoders", "samplers", "transforms"):
        sub_mod = types.ModuleType(f"torchcodec.{sub}")
        sub_mod.__spec__ = importlib.util.spec_from_loader(
            f"torchcodec.{sub}", loader=None
        )
        stub.__dict__[sub] = sub_mod
        sys.modules[f"torchcodec.{sub}"] = sub_mod

    # sentence_transformers.base.modality_types imports:
    #   from torchcodec.decoders import AudioDecoder, VideoDecoder
    stub.decoders.AudioDecoder = object  # type: ignore[attr-defined]
    stub.decoders.VideoDecoder = object  # type: ignore[attr-defined]

    sys.modules["torchcodec"] = stub
    _TORCHCODEC_STUBBED = True
    logger.warning(
        "torchcodec import failed (FFmpeg shared libraries missing). "
        "Installed a minimal stub; video/audio decoding features are disabled. "
        "RAG text embedding/reranking will continue to work. "
        "To enable real torchcodec, install FFmpeg full-shared build."
    )


def install_shim_if_needed() -> None:
    """Try to import torchcodec; if it fails, install the stub.

    Handles two cases:
    1. torchcodec not installed (ModuleNotFoundError) — install stub
    2. torchcodec installed but FFmpeg DLL missing (OSError libtorchcodec) — install stub
    """
    if "torchcodec" in sys.modules:
        return
    try:
        import torchcodec  # noqa: F401
    except ModuleNotFoundError:
        # torchcodec not installed — transformers 5.x text-only usage doesn't need it
        _install_stub()
    except Exception as exc:
        err = str(exc).lower()
        if "libtorchcodec" in err or "ffmpeg" in err:
            _install_stub()
        else:
            logger.warning("torchcodec import failed for an unexpected reason: %s", exc)
            raise
