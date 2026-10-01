"""The sentence-embedding model behind every vector search.

jina-embeddings-v2-base-es (Jina AI, Apache-2.0) is trained on Spanish and English
together, so an English query finds Spanish sentences and vice versa. In a bilingual
check it scored "Hace mucho calor hoy" vs. "It is very hot today" at 0.895 and vs.
"Me gusta leer libros" at 0.038, a far wider margin than multilingual-e5-base.

Its architecture isn't built into transformers, so loading it executes Python code
downloaded from Hugging Face (trust_remote_code). Both the model and that code are pinned
to exact commits so the downloaded code can't change underneath us. That code imports
transformers.onnx, which transformers 5 removed, hence the transformers<5 pin in
pyproject.toml.
"""

from functools import cache

from langchain_huggingface import HuggingFaceEmbeddings

MODEL_ID = "jinaai/jina-embeddings-v2-base-es"
MODEL_REVISION = "8e2d780d8fd38f81ca9123ee28e4c5a968aaf21e"
# The architecture code lives in a separate repo, referenced from the model's config.
CODE_REVISION = "d7eb81eb85ad2ef05cf3954e614c4fb6c9a898ce"
DIMENSIONS = 768


@cache
def load_embeddings() -> HuggingFaceEmbeddings:
    """The pinned model on CPU, returning unit-length vectors (cosine = dot product)."""
    return HuggingFaceEmbeddings(
        model_name=MODEL_ID,
        model_kwargs={
            "device": "cpu",
            "revision": MODEL_REVISION,
            "trust_remote_code": True,
            "config_kwargs": {"code_revision": CODE_REVISION},
            "model_kwargs": {"code_revision": CODE_REVISION},
        },
        encode_kwargs={"normalize_embeddings": True, "batch_size": 64},
    )
