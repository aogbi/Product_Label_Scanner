import sys
import os
import re
from datetime import datetime

import dateparser
import dateparser.search

# easyocr is only needed at scan time. Import lazily so the parsing logic
# can be tested / imported on machines without easyocr installed.
try:
    import easyocr
except Exception:  # pragma: no cover
    easyocr = None


# ============================================================
# Config / constants
# ============================================================
MIN_YEAR = 2020
MAX_YEAR = 2035          # full 4-digit years 2020..2035
MIN_YY = MIN_YEAR - 2000  # 20
MAX_YY = MAX_YEAR - 2000  # 35

# Month names (English + French), used to detect "JUN", "JUIN", etc.
MONTH_NAMES_RE = re.compile(
    r'(?:jan(?:uary|vier)?|feb(?:ruary)?|f[eé]v(?:rier)?|'
    r'mar(?:ch|s)?|apr(?:il)?|avr(?:il)?|may|mai|'
    r'jun(?:e)?|juin|jul(?:y)?|juil(?:let)?|aug(?:ust)?|ao[uû]t|'
    r'sep(?:tember|tembre)?|oct(?:ober|obre)?|'
    r'nov(?:ember|embre)?|dec(?:ember|embre)?)',
    re.IGNORECASE,
)

# Labels that mark an EXPIRY date.
EXP_LABELS = (
    'EXP', 'EXPIRY', 'EXPIRE', 'EXPIRES', 'EXPIRATION',
    'USEBY', 'USE BY', 'BB', 'BBE', 'BEST BEFORE', 'BESTBEFORE',
    'PER', 'PEREMPTION', 'PEREMPTON', 'VALID', 'VALIDITY',
)

# Labels that mark a MANUFACTURE / production date. These must NOT be
# returned as the expiry. They are the main source of "FAB instead of EXP".
MFG_LABELS = (
    'FAB', 'FABRICATION', 'MFG', 'MFD', 'MFGD', 'MAN', 'MANUF',
    'MANUFACTURED', 'DOM', 'PROD', 'PRODUCTION', 'PRODUCED', 'DH',
)

# A token is "junk" (lot / barcode / GTIN) when it is a long run of digits
# that is not itself a clean date blob. Handled inside the parser.


# ============================================================
# Step 1: Group OCR blocks into lines
# ============================================================
def group_blocks_into_lines(blocks, y_threshold=20):
    """
    Group word boxes that sit on the same physical line.

    Two boxes belong to the same line when their vertical spans overlap
    enough. We use the box's own height to scale the tolerance, so big
    text and small text both group correctly, and slightly skewed / tilted
    labels still line up because we compare against the running vertical
    centre of the line rather than a single corner.

    Each block is expected to have: left_x, left_y, right_x, right_y, text.
    """
    def y_center(b):
        return (b['left_y'] + b['right_y']) / 2.0

    def y_height(b):
        return max(1, abs(b['right_y'] - b['left_y']))

    # Sort top-to-bottom, then left-to-right.
    blocks = sorted(blocks, key=lambda b: (y_center(b), b['left_x']))

    lines = []  # each line: {'blocks': [...], 'cy': running_center}
    for block in blocks:
        bc = y_center(block)
        bh = y_height(block)
        placed = False
        for line in lines:
            # Tolerance is the larger of the fixed threshold and ~60% of the
            # box height, so tall text gets a proportionally bigger window.
            tol = max(y_threshold, 0.6 * bh)
            if abs(line['cy'] - bc) <= tol:
                line['blocks'].append(block)
                # Update running centre (simple average of centres).
                centers = [y_center(b) for b in line['blocks']]
                line['cy'] = sum(centers) / len(centers)
                placed = True
                break
        if not placed:
            lines.append({'blocks': [block], 'cy': bc})

    # Sort words within each line left-to-right and return plain lists.
    grouped = []
    for line in lines:
        line['blocks'].sort(key=lambda b: b['left_x'])
        grouped.append(line['blocks'])
    # Sort the lines themselves top-to-bottom.
    grouped.sort(key=lambda ln: sum(y_center(b) for b in ln) / len(ln))
    return grouped


# ============================================================
# Step 2: Normalisation helpers
# ============================================================
def normalize_separators(text):
    """
    Turn date-ish separators into '/', collapse spaced single digits,
    and tidy whitespace around slashes.

    Examples:
      "06/2027"        -> "06/2027"
      "06-2027"        -> "06/2027"
      "06.2027"        -> "06/2027"
      "1 0 2 0 2 6"    -> "102026"
      "06 / 2027"      -> "06/2027"
    """
    result = text

    # Collapse runs of single spaced digits: "1 0 2 0 2 6" -> "102026".
    # (Two or more single digits separated by single spaces.)
    result = re.sub(
        r'\b\d(?:\s\d)+\b',
        lambda m: m.group(0).replace(' ', ''),
        result,
    )

    # Replace - . , between two digits with '/'  (don't touch "1,9mg").
    result = re.sub(r'(?<=\d)\s*[-.]\s*(?=\d)', '/', result)
    result = re.sub(r'(?<=\d)\s*,\s*(?=\d{2,})', '/', result)

    # Tidy spaces around '/'.
    result = re.sub(r'\s*/\s*', '/', result)

    return result.strip()


def is_junk_number(token):
    """
    A pure-digit token that is too long to be a date and is not a clean
    6-digit MMYYYY/YYYYMM blob -> treat as lot/barcode/GTIN junk.
    """
    if not token.isdigit():
        return False
    n = len(token)
    if n <= 4:
        return False           # 4 or fewer digits: could be MMYY or YYYY
    if n == 6:
        return parse_6_digits(token) is None  # junk only if it isn't a date
    return True                # 5, or 7+ digits -> junk (barcode / lot)


# ============================================================
# Step 3: Core number -> (month, year) helpers
# ============================================================
def _year_from_value(value):
    """
    Interpret an integer as a year.
    Returns a 4-digit year if plausible, else None.
      26   -> 2026
      2026 -> 2026
    """
    if MIN_YEAR <= value <= MAX_YEAR:
        return value
    if MIN_YY <= value <= MAX_YY:
        return 2000 + value
    return None


def parse_month_year_pair(a, b):
    """
    Given two numbers in EITHER order, decide which is the month and which
    is the year. The month is ALWAYS the value <= 12.

      (6, 2027)  -> 2027-06
      (2027, 6)  -> 2027-06
      (6, 27)    -> 2027-06
      (27, 6)    -> 2027-06
      (10, 26)   -> 2026-10
    Returns a datetime (day=1) or None.
    """
    a, b = int(a), int(b)

    candidates = []
    # a = month, b = year
    if 1 <= a <= 12:
        y = _year_from_value(b)
        if y:
            candidates.append((a, y))
    # b = month, a = year
    if 1 <= b <= 12:
        y = _year_from_value(a)
        if y:
            candidates.append((b, y))

    if not candidates:
        return None

    # If both orderings are possible (e.g. "06 11" -> both <=12), prefer the
    # interpretation whose "year" part is the larger / 4-digit one. In a tie,
    # keep the first reading (a=month).
    candidates.sort(key=lambda my: my[1], reverse=True)
    month, year = candidates[0]
    try:
        return datetime(year, month, 1)
    except ValueError:
        return None


def parse_6_digits(s):
    """
    A clean 6-digit blob is MMYYYY or YYYYMM.
      "062027" -> 2027-06   (MMYYYY)
      "202706" -> 2027-06   (YYYYMM)
    Returns "MM/YYYY" string or None.
    """
    if len(s) != 6 or not s.isdigit():
        return None

    first2, last4 = int(s[:2]), int(s[2:])
    first4, last2 = int(s[:4]), int(s[4:])

    # MMYYYY
    if 1 <= first2 <= 12 and MIN_YEAR <= last4 <= MAX_YEAR:
        return f"{first2:02d}/{last4}"
    # YYYYMM
    if MIN_YEAR <= first4 <= MAX_YEAR and 1 <= last2 <= 12:
        return f"{last2:02d}/{first4}"
    return None


def find_date_in_digit_blob(digits):
    """
    Locate a valid MM+YYYY (or YYYY+MM) inside a noisy run of digits.

    Last resort when OCR scrambles a date field into many small chunks,
    e.g. "Rn6 2 0 10 20 2 6" -> digits "620102026". We find a 4-digit year
    (20xx) and take the 1-2 digits immediately before or after it as the
    month, validating month <= 12. Tolerates leading/trailing garbage
    digits that OCR glued on.

    Returns (month, year) or None. Prefers a month directly before the year
    (common "MM YYYY" reading), then a month after it.
    """
    before_hits = []
    after_hits = []
    n = len(digits)
    for m in re.finditer(r'(20[2-3]\d)', digits):
        year = int(m.group(1))
        i, j = m.start(), m.end()
        if not (MIN_YEAR <= year <= MAX_YEAR):
            continue
        for mlen in (2, 1):  # month immediately BEFORE the year
            if i - mlen >= 0:
                mo = int(digits[i - mlen:i])
                if 1 <= mo <= 12:
                    before_hits.append((mo, year))
                    break
        for mlen in (2, 1):  # month immediately AFTER the year
            if j + mlen <= n:
                mo = int(digits[j:j + mlen])
                if 1 <= mo <= 12:
                    after_hits.append((mo, year))
                    break
    if before_hits:
        return before_hits[0]
    if after_hits:
        return after_hits[0]
    return None


def parse_4_digits(s):
    """
    A 4-digit blob is MMYY (or YYMM), but NOT a bare year.
      "0627" -> 2027-06   (MMYY)
      "2706" -> 2027-06   (YYMM)
      "2027" -> None      (bare year, no month -> not a usable expiry)
    Returns "MM/YYYY" string or None.
    """
    if len(s) != 4 or not s.isdigit():
        return None

    # A bare 4-digit year on its own is not a date we can use.
    if MIN_YEAR <= int(s) <= MAX_YEAR:
        return None

    first2, last2 = int(s[:2]), int(s[2:])

    # MMYY
    if 1 <= first2 <= 12 and MIN_YY <= last2 <= MAX_YY:
        return f"{first2:02d}/{2000 + last2}"
    # YYMM
    if MIN_YY <= first2 <= MAX_YY and 1 <= last2 <= 12:
        return f"{last2:02d}/{2000 + first2}"
    return None


# ============================================================
# Step 4: Parse one token into a datetime
# ============================================================
def parse_token(token):
    """
    Parse a single OCR token (already separator-normalised) into a datetime,
    or None if it is not a usable expiry-style date.

    Handles:
      "06/2027"      MM/YYYY
      "06/27"        MM/YY
      "2027/06"      YYYY/MM
      "08/05/2027"   DD/MM/YYYY
      "08/05/27"     DD/MM/YY
      "062027"       MMYYYY (6 digits)
      "202706"       YYYYMM
      "0627"         MMYY  (4 digits)
    Bare years and junk numbers return None.
    """
    token = token.strip().strip('/')
    if not token:
        return None

    if '/' in token:
        parts = [p for p in token.split('/') if p != '']
        nums = [p for p in parts if p.isdigit()]
        if len(nums) != len(parts):
            return None  # contains letters mixed with slashes -> not numeric date

        if len(nums) == 2:
            return parse_month_year_pair(nums[0], nums[1])

        if len(nums) == 3:
            # DD/MM/YYYY or DD/MM/YY. Day is the one that is clearly a day,
            # month is <= 12, year via _year_from_value.
            d, m, y = int(nums[0]), int(nums[1]), nums[2]
            year = _year_from_value(int(y))
            if year and 1 <= m <= 12 and 1 <= d <= 31:
                try:
                    return datetime(year, m, d)
                except ValueError:
                    return None
            # Try reversed (YYYY/MM/DD) just in case.
            d2, m2, y2 = int(nums[2]), int(nums[1]), int(nums[0])
            year2 = _year_from_value(y2)
            if year2 and 1 <= m2 <= 12 and 1 <= d2 <= 31:
                try:
                    return datetime(year2, m2, d2)
                except ValueError:
                    return None
        return None

    # Pure digits, no slash.
    if not token.isdigit():
        return None

    n = len(token)
    if n == 6:
        s = parse_6_digits(token)
        return parse_token(s) if s else None
    if n == 4:
        s = parse_4_digits(token)
        return parse_token(s) if s else None
    # 1-3 digits alone, or 5 / 7+ digits -> not a standalone date.
    return None


# ============================================================
# Step 5: label classification
# ============================================================
def classify_label(upper_token):
    """Return 'EXP', 'MFG', or None for a token treated as a label."""
    t = upper_token.strip(':').strip('.').strip()
    for lab in EXP_LABELS:
        if t == lab.replace(' ', '') or t == lab:
            return 'EXP'
    for lab in MFG_LABELS:
        if t == lab.replace(' ', '') or t == lab:
            return 'MFG'
    return None


# ============================================================
# Step 6: extract dated candidates from all lines
# ============================================================
def extract_date_candidates(grouped_lines):
    """
    Walk every line, pull out every parseable date, and tag each one with
    the nearest label (EXP / MFG / None).

    A label found on a line applies to dates on that same line that come
    after it; if a line is just a label with no date, that label carries
    forward to the next line's dates (covers the "label on its own line"
    layout).

    Returns a list of dicts:
      {'raw': token, 'date': datetime, 'kind': 'EXP'|'MFG'|None, 'has_day': bool}
    """
    candidates = []
    carried_label = None  # label from a previous label-only line

    for line_blocks in grouped_lines:
        raw_line = " ".join(b['text'] for b in line_blocks)

        # Lines with neither a month name nor any digit hold no date. But if
        # such a line is itself an EXP/MFG label (e.g. a line that is just
        # "FAB" or "EXP"), carry that label to the next line's date.
        if not MONTH_NAMES_RE.search(raw_line) and not re.search(r'\d', raw_line):
            lab_here = _line_label(raw_line)
            carried_label = lab_here if lab_here else None
            continue

        # ---- Case A: line contains a textual month name (JUN, JUIN...) ----
        if MONTH_NAMES_RE.search(raw_line):
            # Remove long digit runs (barcodes) so dateparser isn't confused.
            clean_for_dp = re.sub(r'\d{5,}', ' ', raw_line)
            found = dateparser.search.search_dates(
                clean_for_dp,
                settings={
                    'PREFER_DATES_FROM': 'future',
                    'PREFER_DAY_OF_MONTH': 'last',
                    'DATE_ORDER': 'DMY',
                },
            ) or []

            line_label = _line_label(raw_line) or carried_label
            for text_str, dt in found:
                if MIN_YEAR <= dt.year <= MAX_YEAR:
                    has_day = bool(re.search(r'\b([0-3]?\d)\b.*\b([0-3]?\d)\b', text_str))
                    candidates.append({
                        'raw': text_str.strip(),
                        'date': dt,
                        'kind': line_label,
                        'has_day': _text_has_day(text_str),
                        'reconstructed': False,
                    })
            # A month-name line is a "real" line; reset carry only if it had a date.
            carried_label = None if found else (line_label or carried_label)
            continue

        # ---- Case B: numeric line ----
        normalized = normalize_separators(raw_line)
        tokens = normalized.split()

        # Segment the line into fields separated by labels:
        #   "EXP: <tokensA> DOM: <tokensB>"  ->  field(EXP,[A]) field(DOM,[B])
        # Each field's tokens are then parsed together, so a date shattered
        # across several tokens ("2 0 10 20 2 6") can still be recovered.
        fields = []  # list of (label, [tokens])
        current_label = carried_label
        bucket = []
        for tok in tokens:
            lab = classify_label(tok.upper())
            if lab:
                # Close the current field, start a new one under this label.
                fields.append((current_label, bucket))
                current_label = lab
                bucket = []
            else:
                bucket.append(tok)
        fields.append((current_label, bucket))

        line_had_date = False
        line_labels_seen = [lab for lab in
                            (classify_label(t.upper()) for t in tokens) if lab]

        for field_label, field_tokens in fields:
            if not field_tokens:
                continue

            field_found = False

            # 1) Collect EVERY cleanly-parseable token in the field.
            for tok in field_tokens:
                if is_junk_number(tok):
                    continue
                dt = parse_token(tok)
                if dt and MIN_YEAR <= dt.year <= MAX_YEAR:
                    candidates.append({
                        'raw': tok,
                        'date': dt,
                        'kind': field_label,
                        'has_day': _token_has_day(tok),
                        'reconstructed': False,
                    })
                    field_found = True
                    line_had_date = True

            # 2) Fallback ONLY if nothing clean parsed: concatenate all digits
            #    in the field and hunt for a valid MM+YYYY window inside the
            #    noisy blob. Recovers scrambled OCR like "Rn6 2 0 10 20 2 6".
            if not field_found:
                digit_blob = re.sub(r'\D', '', ' '.join(field_tokens))
                hit = find_date_in_digit_blob(digit_blob)
                if hit:
                    month, year = hit
                    candidates.append({
                        'raw': digit_blob,
                        'date': datetime(year, month, 1),
                        'kind': field_label,
                        'has_day': False,
                        'reconstructed': True,
                    })
                    line_had_date = True

        # If the line was only a label (no date parsed), carry that label
        # forward to the next line. Otherwise clear the carry.
        if not line_had_date and line_labels_seen:
            carried_label = line_labels_seen[-1]
        else:
            carried_label = None

    return candidates


def _line_label(line_text):
    """Find the first EXP/MFG label anywhere in a (month-name) line."""
    for tok in re.split(r'\s+', line_text):
        lab = classify_label(tok.upper())
        if lab:
            return lab
    return None


def _text_has_day(text_str):
    """Heuristic: a parsed month-name phrase has a day if it has 2+ numbers."""
    return len(re.findall(r'\d+', text_str)) >= 2


def _token_has_day(tok):
    """A numeric token has a day if it parses to 3 numeric parts (DD/MM/YY)."""
    parts = [p for p in tok.split('/') if p.isdigit()]
    return len(parts) >= 3


# ============================================================
# Step 7: choose the expiry date from all candidates
# ============================================================
def select_expiry(candidates):
    """
    Pick the expiry date.

    Rule (per spec): labels (EXP / DOM / FAB ...) are NOT used to decide.
    We simply parse every date on the label, compare them, and take the
    LARGEST (latest) one.

    The only cleanup: a "reconstructed" candidate (a date salvaged from a
    scrambled digit blob like "Rn6 2 0 10 20 2 6") is dropped when it merely
    duplicates a date we already parsed cleanly — it is OCR noise, not a
    genuine extra date. This stops junk from inventing a larger fake date.

    Returns a candidate dict or None.
    """
    if not candidates:
        return None

    clean_dates = {c['date'] for c in candidates if not c.get('reconstructed')}

    usable = []
    for c in candidates:
        # Drop reconstructed candidates that just echo a date we already have.
        if c.get('reconstructed') and c['date'] in clean_dates:
            continue
        usable.append(c)

    if not usable:
        usable = list(candidates)

    # Largest (latest) date wins.
    usable.sort(key=lambda c: c['date'])
    return usable[-1]


# ============================================================
# LOT number extraction (kept, currently unused by scan_label)
# ============================================================
_LOT_REJECT = {'EXP', 'EXPIRY', 'BB', 'DOM', 'MFG', 'USE', 'BEST', 'PER', 'FAB', 'PPV', 'DH'}

_LOT_PATTERNS = [
    re.compile(r'(?:LOT|BATCH|Lo[Tt])[#:\s]*([A-Z0-9][\w\-\.]{1,30})(?!\s*:)', re.IGNORECASE),
    re.compile(r'L/?N[:\s]*([A-Z0-9][\w\-\.]{1,30})(?!\s*:)', re.IGNORECASE),
    re.compile(r'LOT\s*(?:NO|NUM|NUMBER)[.\s:]*([A-Z0-9][\w\-\.]{1,30})(?!\s*:)', re.IGNORECASE),
]


def extract_lot_number(grouped_lines):
    for line_blocks in grouped_lines:
        line_text = " ".join(b['text'] for b in line_blocks)
        cleaned = re.sub(r'(?<=\b\w) (?=\w\b)', '', line_text)
        cleaned = re.sub(r'(?<=\d) (?=\d)', '', cleaned)
        for pattern in _LOT_PATTERNS:
            match = pattern.search(cleaned)
            if match:
                lot = match.group(1).strip().rstrip('.')
                if lot.upper() in _LOT_REJECT:
                    continue
                if lot.isdigit() and len(lot) <= 4:
                    continue
                return lot
    return ""


# ============================================================
# Main scanner (Django integration)
# ============================================================
_reader = None


def get_reader():
    global _reader
    if _reader is None:
        if easyocr is None:
            raise RuntimeError("easyocr is not installed in this environment.")
        _reader = easyocr.Reader(['en'], gpu=False)
    return _reader


def scan_label(image_path):
    """
    Process a single label image and return extracted data.

    Pipeline:
      1. EasyOCR reads all text boxes.
      2. Boxes grouped into lines by vertical proximity.
      3. Date candidates extracted and tagged EXP / MFG / none.
      4. Expiry chosen (prefer EXP-labelled, never a MFG date).
    """
    reader = get_reader()
    result = reader.readtext(image_path)

    blocks = []
    raw_texts = []
    total_confidence = 0.0
    count = 0

    for (bbox, text, confidence) in result:
        raw_texts.append(text)
        total_confidence += confidence
        count += 1
        if confidence > 0.5:
            blocks.append({
                'left_x': int(bbox[0][0]),
                'left_y': int(bbox[0][1]),
                'right_x': int(bbox[1][0]),
                'right_y': int(bbox[1][1]),
                'text': text,
            })

    avg_confidence = (total_confidence / count) if count else 0.0
    raw_text_full = " ".join(raw_texts)

    grouped_lines = group_blocks_into_lines(blocks, y_threshold=10)

    candidates = extract_date_candidates(grouped_lines)
    chosen = select_expiry(candidates)

    expiry_date_str = ""
    expiry_date_parsed = None
    if chosen:
        dt = chosen['date']
        if chosen['has_day']:
            expiry_date_str = dt.strftime('%d/%m/%Y')
        else:
            expiry_date_str = dt.strftime('%m/%Y')
        expiry_date_parsed = dt.strftime('%Y-%m-%d')

    return {
        "raw_text": raw_text_full,
        "lot_number": "___",
        "expiry_date": expiry_date_str,
        "expiry_date_parsed": expiry_date_parsed,
        "confidence": avg_confidence,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if os.path.isdir(path):
            for filename in sorted(os.listdir(path)):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    res = scan_label(os.path.join(path, filename))
                    print(f"--- {filename} ---")
                    print(f"EXP: {res['expiry_date']} -> {res['expiry_date_parsed']}")
        else:
            print(scan_label(path))