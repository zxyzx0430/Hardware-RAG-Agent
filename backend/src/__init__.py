"""Backend source package."""

# Install torchcodec stub early if FFmpeg shared libraries are missing.
# sentence-transformers >= 5.5 imports torchcodec unconditionally, but our
# RAG pipeline only needs text embedding/reranking, so the stub is sufficient.
from src.compat.torchcodec_shim import install_shim_if_needed

install_shim_if_needed()
