import os
import time
from typing import List

import mss
import numpy as np
from PIL import Image

from openrecall.config import args, screenshots_path
from openrecall.database import insert_entry
from openrecall.nlp import get_embedding
from openrecall.ocr import extract_text_from_image
from openrecall.utils import (
    get_active_app_name,
    get_active_window_title,
    is_user_active,
)


def mean_structured_similarity_index(
    img1: np.ndarray, img2: np.ndarray, L: int = 255
) -> float:
    """Calculates the Mean Structural Similarity Index (MSSIM) between two images."""
    K1, K2 = 0.01, 0.03
    C1, C2 = (K1 * L) ** 2, (K2 * L) ** 2

    def rgb2gray(img: np.ndarray) -> np.ndarray:
        return 0.2989 * img[..., 0] + 0.5870 * img[..., 1] + 0.1140 * img[..., 2]

    img1_gray: np.ndarray = rgb2gray(img1)
    img2_gray: np.ndarray = rgb2gray(img2)
    mu1: float = np.mean(img1_gray)
    mu2: float = np.mean(img2_gray)
    sigma1_sq = np.var(img1_gray)
    sigma2_sq = np.var(img2_gray)
    sigma12 = np.mean((img1_gray - mu1) * (img2_gray - mu2))
    ssim_index = ((2 * mu1 * mu2 + C1) * (2 * sigma12 + C2)) / (
        (mu1**2 + mu2**2 + C1) * (sigma1_sq + sigma2_sq + C2)
    )
    return ssim_index


def is_similar(
    img1: np.ndarray, img2: np.ndarray, similarity_threshold: float = 0.9
) -> bool:
    """Checks if two images are similar based on MSSIM."""
    similarity: float = mean_structured_similarity_index(img1, img2)
    return similarity >= similarity_threshold


def take_screenshots() -> List[np.ndarray]:
    """Takes screenshots of all connected monitors or primary monitor."""
    screenshots: List[np.ndarray] = []
    with mss.mss() as sct:
        monitor_indices = range(1, len(sct.monitors))

        if args.primary_monitor_only:
            monitor_indices = [1]

        for i in monitor_indices:
            if i < len(sct.monitors):
                monitor_info = sct.monitors[i]
                sct_img = sct.grab(monitor_info)
                screenshot = np.array(sct_img)[:, :, [2, 1, 0]]
                screenshots.append(screenshot)
            else:
                print(f"Warning: Monitor index {i} out of bounds. Skipping.")

    return screenshots


def record_screenshots_thread() -> None:
    """Continuously records screenshots, processes them, and stores relevant data."""
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    last_screenshots: List[np.ndarray] = take_screenshots()

    while True:
        if not is_user_active():
            time.sleep(3)
            continue

        current_screenshots: List[np.ndarray] = take_screenshots()

        if len(last_screenshots) != len(current_screenshots):
            last_screenshots = current_screenshots
            time.sleep(3)
            continue

        for i, current_screenshot in enumerate(current_screenshots):
            last_screenshot = last_screenshots[i]

            if not is_similar(current_screenshot, last_screenshot):
                last_screenshots[i] = current_screenshot
                image = Image.fromarray(current_screenshot)
                timestamp = int(time.time())
                filename = f"{timestamp}_{i}.webp"
                filepath = os.path.join(screenshots_path, filename)
                image.save(
                    filepath,
                    format="webp",
                    lossless=True,
                )
                text: str = extract_text_from_image(current_screenshot)
                if text.strip():
                    embedding: np.ndarray = get_embedding(text)
                    active_app_name: str = get_active_app_name() or "Unknown App"
                    active_window_title: str = get_active_window_title() or "Unknown Title"
                    insert_entry(
                        text, timestamp, embedding, active_app_name, active_window_title
                    )

        time.sleep(3)
