import logging
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
MODEL_NAME: str = "all-MiniLM-L6-v2"
EMBEDDING_DIM: int = 384  # Dimension for all-MiniLM-L6-v2

_model = None
_model_attempted = False


def _get_model():
    """Lazily load SentenceTransformer model if available."""
    global _model, _model_attempted
    if not _model_attempted:
        _model_attempted = True
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(MODEL_NAME)
            logger.info(f"SentenceTransformer model '{MODEL_NAME}' loaded successfully.")
        except Exception as e:
            logger.warning(
                f"SentenceTransformer model not available ({e}). Using zero vectors for embeddings."
            )
            _model = None
    return _model


def get_embedding(text: str) -> np.ndarray:
    """Generates a sentence embedding for the given text.

    Splits the text into lines, encodes each line using the SentenceTransformer
    model if available, and returns the mean of the embeddings.
    Handles empty input text or missing model by returning a zero vector.
    """
    if not text or text.isspace():
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)

    model = _get_model()
    if model is None:
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)

    sentences = [line for line in text.split("\n") if line.strip()]
    if not sentences:
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)

    try:
        sentence_embeddings = model.encode(sentences)
        return np.mean(sentence_embeddings, axis=0, dtype=np.float32)
    except Exception as e:
        logger.error(f"Error generating embedding: {e}")
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Calculates the cosine similarity between two numpy vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    similarity = np.dot(a, b) / (norm_a * norm_b)
    return float(np.clip(similarity, -1.0, 1.0))
