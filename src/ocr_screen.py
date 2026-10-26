"""
vision-public : OCR zone écran (cadre blanc OCR).

Usage :
  python src/ocr_screen.py

1) Une capture plein écran s'affiche : dessine un rectangle (souris).
2) Entrée / double-clic = valider la zone.
3) Boucle live : OCR Tesseract sur la zone + cadres blancs sur le texte.

Touches (fenêtre OCR active) :
  r = resélectionner la zone
  o = forcer un OCR immédiat
  q / Échap = quitter

Vie privée : local uniquement, pas d'upload, pas de sauvegarde disque par défaut.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

import cv2
import numpy as np
import pytesseract
from PIL import ImageGrab

# Design OCR : blanc = texte OCR
COLOR_OCR = (255, 255, 255)
COLOR_ZONE = (200, 200, 200)
COLOR_UI = (240, 240, 240)

# Windows : binaire souvent ici. Ailleurs, pytesseract cherche `tesseract` dans le PATH.
if sys.platform == "win32":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


@dataclass
class Zone:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)

    @property
    def wh(self) -> tuple[int, int]:
        return (max(1, self.right - self.left), max(1, self.bottom - self.top))


def grab_bbox(bbox: tuple[int, int, int, int] | None = None) -> np.ndarray:
    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    rgb = np.array(img)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def select_zone_interactive() -> Zone | None:
    """Capture plein écran, l'utilisateur dessine un rectangle."""
    full = grab_bbox(None)
    h, w = full.shape[:2]
    # Réduire si énorme (multi-écrans) pour rester maniable
    max_w = 1400
    scale = min(1.0, max_w / w)
    view = cv2.resize(full, (int(w * scale), int(h * scale))) if scale < 1 else full.copy()
    vh, vw = view.shape[:2]

    state = {"x0": 0, "y0": 0, "x1": 0, "y1": 0, "drag": False, "done": False}

    win = "vision-public OCR : selectionne une zone"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    def on_mouse(event, x, y, _flags, _param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            state["drag"] = True
            state["x0"] = state["x1"] = x
            state["y0"] = state["y1"] = y
        elif event == cv2.EVENT_MOUSEMOVE and state["drag"]:
            state["x1"] = x
            state["y1"] = y
        elif event == cv2.EVENT_LBUTTONUP:
            state["drag"] = False
            state["x1"] = x
            state["y1"] = y
        elif event == cv2.EVENT_LBUTTONDBLCLK:
            state["done"] = True

    cv2.setMouseCallback(win, on_mouse)
    print("Dessine un rectangle sur la zone a OCR. Entree = valider, q = annuler.")

    while True:
        canvas = view.copy()
        x0, y0, x1, y1 = state["x0"], state["y0"], state["x1"], state["y1"]
        if (x0, y0) != (x1, y1):
            cv2.rectangle(canvas, (x0, y0), (x1, y1), COLOR_ZONE, 2)
        cv2.putText(
            canvas,
            "Drag zone | Enter = OK | q = annuler",
            (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            COLOR_UI,
            2,
        )
        cv2.imshow(win, canvas)
        key = cv2.waitKeyEx(20)
        if key != -1:
            k = key & 0xFF
            if k in (13, 10) or state["done"]:  # Enter
                break
            if k in (ord("q"), ord("Q"), 27):
                cv2.destroyWindow(win)
                return None
        if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
            return None

    cv2.destroyWindow(win)
    xa, xb = sorted((state["x0"], state["x1"]))
    ya, yb = sorted((state["y0"], state["y1"]))
    if xb - xa < 20 or yb - ya < 20:
        print("Zone trop petite.")
        return None

    # Remap vers coordonnées écran réelles
    left = int(xa / scale)
    top = int(ya / scale)
    right = int(xb / scale)
    bottom = int(yb / scale)
    left = max(0, min(left, w - 2))
    top = max(0, min(top, h - 2))
    right = max(left + 2, min(right, w))
    bottom = max(top + 2, min(bottom, h))
    return Zone(left, top, right, bottom)


def ocr_boxes(bgr: np.ndarray, lang: str, min_conf: int) -> list[tuple[int, int, int, int, str, int]]:
    """Retourne listes (x, y, w, h, text, conf)."""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    data = pytesseract.image_to_data(rgb, lang=lang, output_type=pytesseract.Output.DICT)
    out: list[tuple[int, int, int, int, str, int]] = []
    n = len(data["text"])
    for i in range(n):
        conf_s = data["conf"][i]
        try:
            conf = int(float(conf_s))
        except ValueError:
            conf = -1
        text = (data["text"][i] or "").strip()
        if conf < min_conf or not text:
            continue
        x, y, bw, bh = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        if bw < 2 or bh < 2:
            continue
        out.append((x, y, bw, bh, text, conf))
    return out


def draw_ocr(
    frame: np.ndarray,
    boxes: list[tuple[int, int, int, int, str, int]],
    show_text: bool,
) -> None:
    for x, y, bw, bh, text, conf in boxes:
        cv2.rectangle(frame, (x, y), (x + bw, y + bh), COLOR_OCR, 1)
        if show_text:
            label = text[:24]
            cv2.putText(
                frame,
                label,
                (x, max(12, y - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                COLOR_OCR,
                1,
                cv2.LINE_AA,
            )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="vision-public OCR zone ecran")
    p.add_argument("--lang", default="fra+eng", help="Langues Tesseract (defaut fra+eng)")
    p.add_argument("--interval", type=float, default=1.0, help="Secondes entre OCR (defaut 1.0)")
    p.add_argument("--min-conf", type=int, default=50, help="Confiance min Tesseract 0-100")
    p.add_argument("--no-labels", action="store_true", help="Cadres seuls, sans texte overlay")
    p.add_argument(
        "--region",
        type=str,
        default="",
        help="Zone fixe left,top,right,bottom (skip selection)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        ver = pytesseract.get_tesseract_version()
        print(f"Tesseract {ver}  lang={args.lang}")
    except Exception as exc:  # noqa: BLE001
        print(f"ERREUR Tesseract : {exc}")
        print(f"Attendu : {_DEFAULT_TESS}")
        return 1

    if args.region:
        parts = [int(x.strip()) for x in args.region.split(",")]
        if len(parts) != 4:
            print("--region attend left,top,right,bottom")
            return 1
        zone = Zone(*parts)
    else:
        zone = select_zone_interactive()
        if zone is None:
            print("Selection annulee.")
            return 0

    print(f"Zone ecran : {zone.bbox}  ({zone.wh[0]}x{zone.wh[1]})")
    print("r=reselect  o=OCR now  q/Esc=quitter (fenetre active)")

    win = "vision-public OCR"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    boxes: list[tuple[int, int, int, int, str, int]] = []
    last_ocr = 0.0
    last_text_preview = ""

    while True:
        frame = grab_bbox(zone.bbox)
        now = time.perf_counter()
        if now - last_ocr >= args.interval or not boxes:
            boxes = ocr_boxes(frame, args.lang, args.min_conf)
            last_ocr = now
            last_text_preview = " ".join(t for *_, t, _c in boxes[:12])
            if last_text_preview:
                print(f"OCR ({len(boxes)} mots) : {last_text_preview[:120]}")

        draw_ocr(frame, boxes, show_text=not args.no_labels)
        h, _w = frame.shape[:2]
        cv2.putText(
            frame,
            f"OCR boxes {len(boxes)}  every {args.interval:.1f}s  bank:screen",
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            COLOR_UI,
            2,
        )
        cv2.putText(
            frame,
            "r=zone  o=OCR  q/Esc=quit",
            (10, h - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (180, 180, 180),
            1,
        )

        cv2.imshow(win, frame)
        key = cv2.waitKeyEx(30)
        if key != -1:
            k = key & 0xFF
            if k in (ord("q"), ord("Q"), 27):
                break
            if k in (ord("o"), ord("O")):
                last_ocr = 0.0
            if k in (ord("r"), ord("R")):
                cv2.destroyWindow(win)
                new_zone = select_zone_interactive()
                if new_zone is not None:
                    zone = new_zone
                    boxes = []
                    last_ocr = 0.0
                    print(f"Nouvelle zone : {zone.bbox}")
                cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
            break

    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
