"""CLIP zero-shot sur capture écran (V2 peaufiné).

Amélios vs V0 :
  - prompts multi-phrasing (moyenne embeddings texte)
  - détection noir réel (DRM / écran éteint) hors CLIP
  - seuil de confiance + lissage temporel (anti flip-flop)
  - classes un peu plus réalistes bureau / jeu / IDE

Local, pas de cloud.
"""

from __future__ import annotations

from collections import deque
from typing import Any

import cv2
import numpy as np

# (phrases EN pour CLIP, label FR overlay)
# Plusieurs phrases / classe = embedding texte moyen (plus stable)
SCREEN_CLASSES: list[tuple[list[str], str]] = [
    (
        [
            "a Windows desktop with icons and wallpaper",
            "computer desktop background with taskbar",
        ],
        "bureau",
    ),
    (
        [
            "a web browser showing a website",
            "Chrome or Firefox browser window with tabs",
        ],
        "navigateur",
    ),
    (
        [
            "video game gameplay 3D scene",
            "MMORPG or anime game with HUD interface",
            "colorful game world not a black screen",
        ],
        "jeu",
    ),
    (
        [
            "code editor IDE with source code",
            "Visual Studio Code or code editor with programming text",
        ],
        "code",
    ),
    (
        [
            "a completely black screen with nothing visible",
            "empty black rectangle video player DRM blank",
        ],
        "video_noir",
    ),
    (
        [
            "PDF document or text page with paragraphs",
            "word processor document with readable text",
        ],
        "document",
    ),
    (
        [
            "chat or messaging application conversation",
            "Discord or WhatsApp or Messenger chat window",
        ],
        "messagerie",
    ),
    (
        [
            "terminal command line with monospace text",
            "PowerShell or CMD console window",
        ],
        "terminal",
    ),
    (
        [
            "file explorer showing folders and files",
            "Windows File Explorer directory listing",
        ],
        "fichiers",
    ),
    (
        [
            "settings control panel or system preferences",
            "Windows Settings app",
        ],
        "reglages",
    ),
    (
        [
            "spreadsheet Excel grid with cells",
            "table of numbers in Excel or LibreOffice Calc",
        ],
        "tableur",
    ),
    (
        [
            "video player with visible movie or anime content",
            "streaming UI with subtitles and visible picture",
        ],
        "video",
    ),
]

# Noir « vrai » (DRM / écran off) : moyenne luminance + faible variance
BLACK_MEAN_MAX = 18.0
BLACK_STD_MAX = 12.0
# Score CLIP min pour accepter un label (sinon « indecis »)
MIN_SCORE = 0.22


class ClipLabelSmoother:
    """Majorité glissante pour éviter le flip-flop."""

    def __init__(self, window: int = 5, switch_need: int = 3) -> None:
        self.window = window
        self.switch_need = switch_need
        self._hist: deque[str] = deque(maxlen=window)
        self._current = "indecis"

    def update(self, label: str) -> str:
        self._hist.append(label)
        if len(self._hist) < self.switch_need:
            return self._current
        counts: dict[str, int] = {}
        for x in self._hist:
            counts[x] = counts.get(x, 0) + 1
        best = max(counts, key=counts.get)  # type: ignore[arg-type]
        if best != self._current and counts[best] >= self.switch_need:
            self._current = best
        return self._current

    def reset(self) -> None:
        self._hist.clear()
        self._current = "indecis"


def is_mostly_black(frame_bgr: np.ndarray) -> bool:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    # Échantillon rapide
    small = cv2.resize(gray, (160, 90))
    mean = float(np.mean(small))
    std = float(np.std(small))
    return mean <= BLACK_MEAN_MAX and std <= BLACK_STD_MAX


class ClipScreen:
    def __init__(
        self,
        model_name: str = "ViT-B-32",
        pretrained: str = "openai",
        min_score: float = MIN_SCORE,
    ) -> None:
        import open_clip
        import torch

        self._torch = torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self.min_score = min_score
        # openai ViT-B-32 = poids QuickGELU
        try:
            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32-quickgelu", pretrained=pretrained
            )
            tok_name = "ViT-B-32"
        except Exception:  # noqa: BLE001
            model, _, preprocess = open_clip.create_model_and_transforms(
                model_name, pretrained=pretrained
            )
            tok_name = model_name.replace("-quickgelu", "")

        model = model.to(self._device)
        model.eval()
        self._model = model
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(tok_name)

        self._labels_fr = [fr for _, fr in SCREEN_CLASSES]
        # Embedding texte = moyenne des phrases de chaque classe
        feats = []
        with torch.no_grad():
            for phrases, _fr in SCREEN_CLASSES:
                text = self._tokenizer(phrases).to(self._device)
                t = model.encode_text(text)
                t = t / t.norm(dim=-1, keepdim=True)
                feats.append(t.mean(dim=0, keepdim=True))
            text_features = torch.cat(feats, dim=0)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        self._text_features = text_features
        self.smoother = ClipLabelSmoother()

    def close(self) -> None:
        self._model = None
        self._text_features = None
        if self._device == "cuda":
            self._torch.cuda.empty_cache()

    def classify_bgr(self, frame_bgr: np.ndarray, top_k: int = 3) -> list[dict[str, Any]]:
        """Retourne [{label_fr, score, smoothed?}, ...] trié desc."""
        from PIL import Image

        if self._model is None:
            raise RuntimeError("ClipScreen ferme")

        # Raccourci DRM / ecran noir : pas besoin de CLIP
        if is_mostly_black(frame_bgr):
            raw = [{"label_fr": "video_noir", "score": 0.95}]
            smooth = self.smoother.update("video_noir")
            raw[0]["smoothed"] = smooth
            return raw

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        max_side = 768
        if max(h, w) > max_side:
            scale = max_side / max(h, w)
            rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)))

        image = Image.fromarray(rgb)
        image_in = self._preprocess(image).unsqueeze(0).to(self._device)

        with self._torch.no_grad():
            image_features = self._model.encode_image(image_in)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            # logits bruts (avant softmax)
            raw_logits = (100.0 * image_features @ self._text_features.T)[0]
            idx_noir = self._labels_fr.index("video_noir")
            # Hors noir pixel : video_noir ne concurrence PAS (anti faux positif DRM)
            raw_logits = raw_logits.clone()
            raw_logits[idx_noir] = -1e4
            probs = raw_logits.softmax(dim=-1).detach().float().cpu().numpy()

        order = np.argsort(-probs)[:top_k]
        out: list[dict[str, Any]] = []
        for i in order:
            label = self._labels_fr[int(i)]
            score = float(probs[int(i)])
            if label == "video_noir":
                continue
            out.append({"label_fr": label, "score": score})

        if not out:
            out = [{"label_fr": "indecis", "score": 0.0}]

        top_label = out[0]["label_fr"]
        top_score = out[0]["score"]
        if top_score < self.min_score:
            top_label = "indecis"
            out.insert(0, {"label_fr": "indecis", "score": top_score})

        smooth = self.smoother.update(top_label)
        out[0]["smoothed"] = smooth
        return out[:top_k]
