"""Moondream3 (preview) : description semantique d'un ecran.

Local only (Hugging Face), pas d'API cloud.
Charge lazy + optionnel .compile() (lent au 1er appel, plus rapide ensuite).

ADR-0003 V3. Licence BSL : usage perso / interne OK.
"""

from __future__ import annotations

import os
from typing import Any

import cv2
import numpy as np

DEFAULT_MODEL = "moondream/moondream3-preview"
FALLBACK_MODEL = "vikhyatk/moondream2"

DEFAULT_QUESTION_FR = (
    "Decris cet ecran d'ordinateur en francais, en 1 ou 2 phrases courtes. "
    "Quelle application ou quel contenu est visible ?"
)


class MoondreamScreen:
    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        compile_model: bool = True,
        max_side: int = 1024,
        local_files_only: bool = False,
    ) -> None:
        # local_files_only=True : ne jamais aller sur le reseau. Sans lui,
        # from_pretrained demande a Hugging Face si « main » a bouge et peut
        # retelecharger une revision neuve (18 Go) en silence. Les outils a la
        # demande (instant_screen --vlm, lire_cases) le passent a True.
        #
        # En plus, mode hors ligne force. Il doit etre pose AVANT l'import de
        # transformers : huggingface_hub lit HF_HUB_OFFLINE une seule fois, a
        # l'import (mesure audit 23/10 : pose apres, la constante restait False).
        # Le message « A new version of ... hf_moondream.py was downloaded » vu
        # le 23/10 vient de la copie locale du code du modele : la date du
        # fichier n'a pas bouge (mesure audit), ce n'etait pas un telechargement.
        if local_files_only:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch
        from transformers import AutoModelForCausalLM

        self._torch = torch
        self.max_side = max_side
        self.model_id = model_id
        self._model: Any = None

        if not torch.cuda.is_available():
            raise RuntimeError("Moondream V0 attend un GPU CUDA")
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        last_err: Exception | None = None
        for mid in (model_id, FALLBACK_MODEL):
            if mid != model_id:
                print(f"Moondream : fallback vers {mid}")
            try:
                print(f"Chargement Moondream ({mid})…")
                model = AutoModelForCausalLM.from_pretrained(
                    mid,
                    trust_remote_code=True,
                    dtype=dtype,
                    device_map={"": "cuda"},
                    local_files_only=local_files_only,
                )
                model.eval()
                if compile_model and hasattr(model, "compile") and mid == DEFAULT_MODEL:
                    try:
                        print("Moondream compile() (warmup, peut prendre 1-2 min)…")
                        model.compile()
                    except Exception as exc:  # noqa: BLE001
                        print(f"compile() ignore ({exc})")
                self._model = model
                self.model_id = mid
                print(f"Moondream pret ({mid})")
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                print(f"Echec {mid} : {exc}")
        if self._model is None:
            raise RuntimeError(f"Impossible de charger Moondream : {last_err}")

    def close(self) -> None:
        self._model = None
        self._torch.cuda.empty_cache()

    def _to_pil(self, frame_bgr: np.ndarray):
        from PIL import Image

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        if max(h, w) > self.max_side:
            scale = self.max_side / max(h, w)
            rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)))
        return Image.fromarray(rgb)

    def caption_bgr(self, frame_bgr: np.ndarray, length: str = "short") -> str:
        """Caption courte."""
        image = self._to_pil(frame_bgr)
        model = self._model
        if hasattr(model, "caption"):
            try:
                out = model.caption(image, length=length)
            except TypeError:
                out = model.caption(image)
            if isinstance(out, dict):
                return str(out.get("caption") or out.get("answer") or out).strip()
            return str(out).strip()
        return self.query_bgr(frame_bgr, "Describe this computer screen briefly.")

    def query_bgr(self, frame_bgr: np.ndarray, question: str | None = None) -> str:
        question = question or DEFAULT_QUESTION_FR
        image = self._to_pil(frame_bgr)
        model = self._model

        if hasattr(model, "query"):
            try:
                out = model.query(image=image, question=question, reasoning=False)
            except TypeError:
                out = model.query(image=image, question=question)
            if isinstance(out, dict):
                return str(out.get("answer") or out).strip()
            return str(out).strip()

        # Fallback moondream2 API
        enc = model.encode_image(image)
        tok = getattr(model, "tokenizer", None)
        if tok is None:
            from transformers import AutoTokenizer

            tok = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=True)
        ans = model.answer_question(enc, question, tok)
        return str(ans).strip()
