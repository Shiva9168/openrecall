import logging

logger = logging.getLogger(__name__)

_ocr_engine = None
_ocr_engine_attempted = False


def _get_ocr_engine():
    """Lazily load python-doctr OCR predictor if installed."""
    global _ocr_engine, _ocr_engine_attempted
    if not _ocr_engine_attempted:
        _ocr_engine_attempted = True
        try:
            from doctr.models import ocr_predictor

            _ocr_engine = ocr_predictor(
                pretrained=True,
                det_arch="db_mobilenet_v3_large",
                reco_arch="crnn_mobilenet_v3_large",
            )
            logger.info("python-doctr OCR engine loaded successfully.")
        except Exception as e:
            logger.warning(
                f"python-doctr OCR engine not available ({e}). "
                "Text extraction will return empty strings until local OCR engine is configured."
            )
            _ocr_engine = None
    return _ocr_engine


def extract_text_from_image(image):
    """Extracts text from an image using the active OCR engine."""
    engine = _get_ocr_engine()
    if engine is None:
        return ""

    try:
        result = engine([image])
        text = ""
        for page in result.pages:
            for block in page.blocks:
                for line in block.lines:
                    for word in line.words:
                        text += word.value + " "
                    text += "\n"
                text += "\n"
        return text
    except Exception as e:
        logger.error(f"Error during OCR extraction: {e}")
        return ""
