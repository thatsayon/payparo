"""
kyc_ocr.py — Tesseract-based OCR extraction for KYC ID card images.

Tuned for the Bangladesh National ID (NID) card, which typically prints
these English labels:
  NID No / ID No         → id_number
  Name                   → full_name
  Father                 → father_name
  Mother                 → mother_name
  Date of Birth / DOB    → date_of_birth  (e.g. "19 Jul 1992")
  Present Address        → present_address
  Permanent Address      → permanent_address
  Blood Group (m/f text) → gender

All fields default to empty string if not found. Callers should treat results
as pre-fill suggestions the user can review/correct before submitting KYC.
"""

import io
import re
import logging

from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Image pre-processing
# ──────────────────────────────────────────────

def _preprocess(image_file) -> Image.Image:
    """
    Scale, sharpen, and binarise the uploaded image so Tesseract reads it
    more accurately regardless of lighting or card background colour.
    """
    image_file.seek(0)
    img = Image.open(io.BytesIO(image_file.read()))

    img = img.convert("RGB")

    # Upscale if the card is small — Tesseract needs ≥300 DPI equivalent
    min_width = 1400
    if img.width < min_width:
        scale = min_width / img.width
        img = img.resize(
            (int(img.width * scale), int(img.height * scale)),
            Image.LANCZOS,
        )

    img = img.convert("L")                     # grayscale
    img = img.filter(ImageFilter.SHARPEN)      # crisp edges
    img = img.filter(ImageFilter.SHARPEN)      # double-sharpen
    img = ImageOps.autocontrast(img, cutoff=2) # boost contrast

    return img


# ──────────────────────────────────────────────
# Field-level extractors
# ──────────────────────────────────────────────

# BD NID label variants (handles OCR typos / abbreviations)
_LABEL_MAP = {
    "id_number": [
        r"NID\s*(?:No|Number|#)?",
        r"ID\s*No",
        r"Card\s*No",
        r"National\s*ID",
        r"Identification\s*No",
    ],
    "full_name": [
        r"Name",
        r"Full\s*Name",
    ],
    "father_name": [
        r"Father(?:'?s)?\s*(?:Name)?",
        r"Father",
    ],
    "mother_name": [
        r"Mother(?:'?s)?\s*(?:Name)?",
        r"Mother",
    ],
    "date_of_birth": [
        r"Date\s*of\s*Birth",
        r"D\.?O\.?B\.?",
        r"Birth\s*Date",
        r"Born",
    ],
    "present_address": [
        r"Present\s*Address",
        r"Present",
        r"Current\s*Address",
    ],
    "permanent_address": [
        r"Permanent\s*Address",
        r"Permanent",
    ],
}

_SKIP_KEYWORDS = frozenset([
    "name", "father", "mother", "address", "date", "birth",
    "gender", "nid", "dob", "blood", "nationality", "sex",
    "male", "female", "valid", "expir", "issued",
])


def _clean(value: str) -> str:
    """Strip stray punctuation / OCR artefacts from an extracted value."""
    return re.sub(r"^[\s:;|,\-]+|[\s:;|,\-]+$", "", value).strip()


def _after_label(lines: list[str], *patterns: str) -> str:
    """
    Search lines for any label pattern; return the value found either
    on the same line ("Label: value") or on the very next non-empty line.
    """
    combined = "|".join(f"(?:{p})" for p in patterns)
    label_re = re.compile(combined, re.IGNORECASE)

    for i, line in enumerate(lines):
        if not label_re.search(line):
            continue

        # Same-line value: "Name: Mr Barit Miya"
        inline = re.sub(combined, "", line, flags=re.IGNORECASE)
        inline = _clean(inline)
        if inline and not any(kw in inline.lower() for kw in _SKIP_KEYWORDS):
            return inline

        # Next non-empty line
        for j in range(i + 1, min(i + 5, len(lines))):
            candidate = lines[j].strip()
            if not candidate:
                continue
            if any(kw in candidate.lower() for kw in _SKIP_KEYWORDS):
                continue
            # Reject lines that look like another label
            if label_re.search(candidate):
                continue
            return _clean(candidate)

    return ""


def _extract_id_number(text: str) -> str:
    """
    Extracts the ID / NID number.
    - Handles OCR garbles: 'lD', '1D' instead of 'ID'
    - Handles dashes within digit groups (e.g. '0018-5978')
    - Falls back to any standalone digit group of 6+ digits
    """
    # Label variants including common OCR misreads of 'ID' → '1D' / 'lD'
    label_re = re.compile(
        r"(?:NID|[I1l]D\s*No|Card\s*No|National\s*ID|Identification\s*No)"
        r"[^\d\-]{0,15}"
        r"([\d][\d\s\-]{4,20}[\d])",
        re.IGNORECASE,
    )
    m = label_re.search(text)
    if m:
        # Strip spaces and dashes to get the raw digit string
        raw = re.sub(r"[\s\-]+", "", m.group(1))
        if len(raw) >= 6:
            return raw

    # Fallback: any long bare digit run (10–17 digits, no dashes)
    candidates = re.findall(r"\b\d{10,17}\b", text)
    if candidates:
        return candidates[0]

    # Wider fallback: digit groups joined by dashes, total ≥6 digits
    dashed = re.findall(r"\b(?:\d+\-)+\d+\b", text)
    for d in dashed:
        digits = d.replace("-", "")
        if len(digits) >= 6:
            return d  # keep dashes intact for readability

    return ""


_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

# Pre-compile date patterns (most specific first)
_DATE_PATTERNS = [
    # DD MMM YYYY  — "19 Jul 1992"  or  "01 APR/AVR 1992"
    re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})(?:\s*/[A-Za-z]+)?\s+(\d{4})\b"),
    # YYYY-MM-DD
    re.compile(r"\b(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})\b"),
    # DD/MM/YYYY  DD-MM-YYYY  DD.MM.YYYY
    re.compile(r"\b(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{4})\b"),
    # DD MMM YY   — "01 APR 85" (2-digit year)
    re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})(?:\s*/[A-Za-z]+)?\s+(\d{2})\b"),
]


def _extract_date(text: str) -> str:
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        a, b, c = m.groups()

        # DD MMM YYYY / DD MMM YY
        if not b.isdigit():
            month = _MONTH_MAP.get(b[:3].lower(), "??")
            year = c if len(c) == 4 else ("19" + c if int(c) > 24 else "20" + c)
            return f"{year}-{month}-{a.zfill(2)}"

        # YYYY-MM-DD
        if len(a) == 4:
            return f"{a}-{b.zfill(2)}-{c.zfill(2)}"

        # DD/MM/YYYY
        return f"{c}-{b.zfill(2)}-{a.zfill(2)}"

    return ""


def _extract_gender(text: str) -> str:
    # 1. Full word after label: "Sex: Male" / "Gender: Female"
    m = re.search(
        r"(?:Sex|Gender)\s*[:/\\\-]?(?:[A-Za-z/]+)?\s*[.\n]?\s*(Male|Female|M\b|F\b)",
        text, re.IGNORECASE,
    )
    if m:
        val = m.group(1).strip().upper()
        return "Male" if val.startswith("M") else "Female"

    # 2. Single M / F on its own line immediately after a Sex/Gender label line
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"\bSex\b|\bGender\b", line, re.IGNORECASE):
            for j in range(i + 1, min(i + 4, len(lines))):
                candidate = lines[j].strip()
                if re.match(r"^M(\s|$|[^a-z])", candidate, re.IGNORECASE):
                    return "Male"
                if re.match(r"^F(\s|$|[^a-z])", candidate, re.IGNORECASE):
                    return "Female"

    # 3. Bare keyword anywhere in text
    if re.search(r"\bFemale\b", text, re.IGNORECASE):
        return "Female"
    if re.search(r"\bMale\b", text, re.IGNORECASE):
        return "Male"
    return ""


# ──────────────────────────────────────────────
# Name fallback (when label is garbled by watermark)
# ──────────────────────────────────────────────

# Words that are definitely NOT someone's name
_NON_NAME_WORDS = frozenset([
    "specimen", "government", "gouvernement", "canada", "permanent",
    "resident", "card", "carte", "du", "of", "de", "landing", "place",
    "pr", "since", "eyes", "yeux", "height", "taille", "cod", "dob",
    "valid", "expir", "issued", "from", "to", "date", "naissance",
    "birth", "sex", "gender", "nationality", "nationalite", "blood",
    "name", "nom", "address", "adresse", "father", "mother",
    "utp", "ottawa", "mar", "mars", "apr", "avr",
])


def _extract_name_fallback(lines: list[str], id_number: str) -> str:
    """
    Fallback name extractor: scans lines that appear BEFORE the ID number
    for a human-name-like line — at least 2 words, no digits, no known
    non-name keywords.

    Handles cases where the Name label is garbled by security watermarks
    but the actual name value is still readable.
    """
    # Find the line index where the ID number or its label appears
    id_line_idx = len(lines)
    id_label_re = re.compile(r"(?:NID|[I1l]D\s*No|Card\s*No)", re.IGNORECASE)
    for i, line in enumerate(lines):
        if id_label_re.search(line) or (id_number and id_number.replace("-", "") in line.replace("-", "").replace(" ", "")):
            id_line_idx = i
            break

    candidates = []
    for line in lines[:id_line_idx]:
        clean = line.strip()
        if not clean:
            continue
        # Must have at least 2 words
        words = clean.split()
        if len(words) < 2:
            continue
        # No digits
        if re.search(r"\d", clean):
            continue
        # No lines with special characters (garbled OCR noise)
        if re.search(r"[|<>{}/\\@#$%^&*=+~`]", clean):
            continue
        # Skip lines that are mostly punctuation/noise
        letter_ratio = sum(c.isalpha() for c in clean) / max(len(clean), 1)
        if letter_ratio < 0.6:
            continue
        # Skip if any word is a known non-name word
        words_lower = [w.lower().strip(".,;:") for w in words]
        if any(w in _NON_NAME_WORDS for w in words_lower):
            continue
        candidates.append(clean)

    # Return the last clean candidate (name tends to be just before the ID fields)
    if candidates:
        best = candidates[-1]
        # Title-case if it was all-caps (OCR often uppercases names)
        return best.title() if best.isupper() else best

    return ""


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def extract_id_card_fields(image_file) -> dict:
    """
    Run Tesseract OCR on *image_file* and return a dict:

        { id_number, full_name, father_name, mother_name,
          date_of_birth, present_address, permanent_address, gender,
          _raw_ocr }

    All string values; empty string = not found.
    Any error is caught so the upload endpoint always succeeds.
    """
    try:
        import pytesseract
    except ImportError:
        logger.warning("pytesseract is not installed; OCR skipped.")
        return {}

    try:
        img = _preprocess(image_file)

        # --oem 3  = best available LSTM engine
        # --psm 4  = assume a single column of text (suited for NID cards)
        raw_text = pytesseract.image_to_string(img, config="--oem 3 --psm 4")
        logger.debug("KYC OCR raw:\n%s", raw_text)

        lines = [ln.strip() for ln in raw_text.splitlines()]

        fields = {
            "id_number":         _extract_id_number(raw_text),
            "full_name":         _after_label(lines, *_LABEL_MAP["full_name"]),
            "father_name":       _after_label(lines, *_LABEL_MAP["father_name"]),
            "mother_name":       _after_label(lines, *_LABEL_MAP["mother_name"]),
            "date_of_birth":     _extract_date(raw_text),
            "present_address":   _after_label(lines, *_LABEL_MAP["present_address"]),
            "permanent_address": _after_label(lines, *_LABEL_MAP["permanent_address"]),
            "gender":            _extract_gender(raw_text),
        }

        # If the Name label was garbled, try position-based fallback
        if not fields["full_name"]:
            fields["full_name"] = _extract_name_fallback(lines, fields["id_number"])

        return fields

    except Exception as exc:  # noqa: BLE001
        logger.warning("KYC OCR failed: %s", exc, exc_info=True)
        return {}
