import easyocr
import sys
import os
import re
from datetime import datetime
import dateparser

# ============================================================
# Month name regex (English + French)
# ============================================================
MONTH_NAMES_RE = re.compile(
    r'(?:jan(?:uary|vier)?|feb(?:ruary)?|f[eé]v(?:rier)?|'
    r'mar(?:ch|s)?|apr(?:il)?|avr(?:il)?|may|mai|'
    r'jun(?:e)?|juin|jul(?:y)?|juil(?:let)?|aug(?:ust)?|ao[uû]t|'
    r'sep(?:tember|tembre)?|oct(?:ober|obre)?|'
    r'nov(?:ember|embre)?|dec(?:ember|embre)?)',
    re.IGNORECASE
)

# ============================================================
# Step 1: Group OCR blocks into lines by Y proximity (abs 30)
# ============================================================
def group_blocks_into_lines(blocks, y_threshold=30):
    """Group blocks whose left_y values are within y_threshold pixels."""
    blocks = sorted(blocks, key=lambda b: (b['left_y'], b['left_x']))
    lines = []
    for block in blocks:
        placed = False
        for line in lines:
            # Compare against the average Y of the line
            avg_y = sum(b['left_y'] for b in line) / len(line)
            if abs(avg_y - block['left_y']) <= y_threshold:
                line.append(block)
                placed = True
                break
        if not placed:
            lines.append([block])
    # Sort items within each line by X (left to right)
    for line in lines:
        line.sort(key=lambda b: b['left_x'])
    return lines

# ============================================================
# Step 2: Check if a line contains date-worthy content
# ============================================================
def line_has_date_content(line_text):
    """
    A line is interesting if it contains:
    - A month name (JAN, FEB, etc.)
    - Or digits that could be a date
    But NOT if it's purely alphabetic text with no numbers.
    """
    has_month = bool(MONTH_NAMES_RE.search(line_text))
    has_digits = bool(re.search(r'\d', line_text))
    return has_month or has_digits

def line_is_only_text(line_text):
    """True if the line has NO digits and NO month names."""
    cleaned = re.sub(r'[^a-zA-Z0-9]', '', line_text)
    if not cleaned:
        return True
    has_digits = bool(re.search(r'\d', cleaned))
    has_month = bool(MONTH_NAMES_RE.search(line_text))
    if not has_digits and not has_month:
        return True
    return False

# ============================================================
# Step 3: Clean a line for date parsing
# ============================================================
def clean_line(line_text):
    """
    - Remove spaces between digits: "2 0 2 6" -> "2026"
    - Replace - . , with /
    - Remove spaces around /
    """
    # Remove spaces between single digits: "0 4 2 0 2 8" -> "042028"
    result = re.sub(r'(?<=\d)\s+(?=\d)', '', line_text)
    # Replace - and . between digits only (not 1,9mg or 2.025 gm)
    result = re.sub(r'(?<=\d)[-.](?=\d)', '/', result)
    # Do NOT replace commas followed by digit+letter (like 1,9mg)
    result = re.sub(r'(?<=\d),(?=\d{1,2}(?!\d|[a-zA-Z]))', '/', result)
    # Remove spaces around /
    result = re.sub(r'\s*/\s*', '/', result)
    return result.strip()

# ============================================================
# Step 4: Parse a 6-digit number into a date
# ============================================================
def parse_6_digits(s):
    """
    6 digits can be:
    - MMYYYY (e.g., 102026 -> Oct 2026)
    - YYYYMM (e.g., 202610 -> Oct 2026)
    
    Rules:
    - If first 2 digits <= 12: first two are Month, last four are Year
    - Else if last 2 digits <= 12: first four are Year, last two are Month
    - Check 2020 < YYYY < 2035
    - If something is wrong, it's not a date -> return None
    """
    if len(s) != 6 or not s.isdigit():
        return None
    
    first2 = int(s[:2])
    last4 = int(s[2:6])
    first4 = int(s[:4])
    last2 = int(s[4:6])
    
    # Try MMYYYY first: first 2 are Month, last 4 are Year
    if 1 <= first2 <= 12 and 2020 <= last4 <= 2035:
        return f"{first2:02d}/{last4}"
    
    # Try YYYYMM: first 4 are Year, last 2 are Month
    if 2020 <= first4 <= 2035 and 1 <= last2 <= 12:
        return f"{last2:02d}/{first4}"
    
    return None

# ============================================================
# Step 5: Validate and fix a slash-separated date
# ============================================================
def validate_slash_date(date_str):
    """
    Given a date string with / separators, validate it.
    - Count total digits. If > 8, there are extra numbers -> try to fix.
    - Month must be <= 12
    - Year (YYYY) must be 2020 < y < 2035, or (YY) must be 20 < y < 35
    - Day must be <= 31
    """
    parts = date_str.split('/')
    # Remove empty parts
    parts = [p.strip() for p in parts if p.strip()]
    
    if not parts:
        return None
    
    # Count total digit characters
    total_digits = sum(len(re.sub(r'\D', '', p)) for p in parts)
    
    # Reject month 00
    if parts and parts[0] == '00':
        return None
    
    if total_digits > 8:
        # Too many digits - try to extract a valid date
        return _extract_date_from_noisy(date_str)
    
    # Rejoin and let dateparser handle it
    cleaned = '/'.join(parts)
    return cleaned

def _extract_date_from_noisy(date_str):
    """
    When there are too many digits (>8), try to find a valid
    MM/YY, MM/YYYY, DD/MM/YY, or DD/MM/YYYY inside the string.
    
    Rules:
    - Month <= 12
    - Year (YYYY): 2020 < y < 2035 or (YY): 20 < y < 35
    - Day <= 31
    """
    # Extract all numbers separated by /
    nums = re.findall(r'\d+', date_str)
    
    # Try every combination of 2 consecutive numbers as MM/YY or MM/YYYY
    for i in range(len(nums)):
        for j in range(i + 1, min(i + 3, len(nums))):
            candidate_parts = nums[i:j + 1]
            
            if len(candidate_parts) == 2:
                a, b = int(candidate_parts[0]), int(candidate_parts[1])
                # MM/YYYY
                if 1 <= a <= 12 and 2020 <= b <= 2035:
                    return f"{a:02d}/{b}"
                # MM/YY
                if 1 <= a <= 12 and 20 <= b <= 35:
                    return f"{a:02d}/{b}"
                # YYYY/MM (reversed)
                if 2020 <= a <= 2035 and 1 <= b <= 12:
                    return f"{b:02d}/{a}"
            
            if len(candidate_parts) == 3:
                a, b, c = int(candidate_parts[0]), int(candidate_parts[1]), int(candidate_parts[2])
                # DD/MM/YYYY
                if 1 <= a <= 31 and 1 <= b <= 12 and 2020 <= c <= 2035:
                    return f"{a:02d}/{b:02d}/{c}"
                # DD/MM/YY
                if 1 <= a <= 31 and 1 <= b <= 12 and 20 <= c <= 35:
                    return f"{a:02d}/{b:02d}/{c}"
    
    return None

# ============================================================
# Step 6: Process a single word (no slash) that is pure digits
# ============================================================
def process_pure_number_word(word):
    """
    Process a word that contains only digits (no /):
    - More than 6 digits -> skip (not a date)
    - Exactly 6 digits -> try parse_6_digits
    - 4 digits -> could be MMYY or YYYY
    - 2 digits -> too short alone, skip
    """
    digits = re.sub(r'\D', '', word)
    
    if len(digits) > 6:
        return None  # Too many digits, not a date
    
    if len(digits) == 6:
        return parse_6_digits(digits)
    
    if len(digits) == 4:
        first2 = int(digits[:2])
        last2 = int(digits[2:4])
        # MMYY
        if 1 <= first2 <= 12 and 20 <= last2 <= 35:
            return f"{first2:02d}/{last2}"
        # Could be a year alone (2025, 2026...)
        year = int(digits)
        if 2020 <= year <= 2035:
            return None  # Just a year with no month, skip
    
    return None

# ============================================================
# Safe date parser (tries explicit formats before dateparser)
# ============================================================
def _safe_parse(date_str):
    """
    Parse a date string. Try explicit format matching first
    to avoid dateparser swapping MM and DD.
    """
    date_str = date_str.strip()
    
    # Try explicit MM/YYYY (e.g., "08/2025")
    m = re.fullmatch(r'(\d{1,2})/(\d{4})', date_str)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 2020 <= year <= 2035:
            return datetime(year, month, 1)
    
    # Try explicit MM/YY (e.g., "07/25", "10/27")
    m = re.fullmatch(r'(\d{1,2})/(\d{2})', date_str)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 20 <= year <= 35:
            return datetime(2000 + year, month, 1)
    
    # Try explicit DD/MM/YYYY
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', date_str)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31 and 2020 <= year <= 2035:
            try:
                return datetime(year, month, day)
            except ValueError:
                pass
    
    # Try explicit DD/MM/YY
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{2})', date_str)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31 and 20 <= year <= 35:
            try:
                return datetime(2000 + year, month, day)
            except ValueError:
                pass
    
    # Fallback to dateparser for text month names (JUN 30 2026, etc.)
    dt = dateparser.parse(
        date_str,
        settings={
            'PREFER_DATES_FROM': 'future',
            'PREFER_DAY_OF_MONTH': 'last',
            'DATE_ORDER': 'DMY'
        }
    )
    return dt

# ============================================================
# Step 7: The main line-by-line extraction
# ============================================================
def extract_dates_from_lines(grouped_lines):
    """
    Go line by line:
    1. Clean the line (remove spaces between digits, replace separators)
    2. Check if the line has date content (month name or digits)
    3. If only text (no digits, no month name) -> skip
    4. If month name found -> extract the month + nearby number as date
    5. If only numbers -> check word by word
    """
    found_dates = []
    
    for line_blocks in grouped_lines:
        # Build the line text
        raw_line = " ".join(b['text'] for b in line_blocks)
        
        # Skip lines that are only text (no date content)
        if line_is_only_text(raw_line):
            continue
        
        if not line_has_date_content(raw_line):
            continue
        
        # Clean the line
        cleaned = clean_line(raw_line)
        
        # --- Case A: Line contains a month name ---
        month_match = MONTH_NAMES_RE.search(cleaned)
        if month_match:
            # Extract the month name and any number next to it
            # Look for patterns like "JUN 30 2026", "30 JUN 26", "JUN 26", etc.
            dt = _safe_parse(cleaned)
            if dt and 2020 <= dt.year <= 2035:
                found_dates.append((cleaned, dt))
                continue
        
        # --- Case B: Line has digits (no month name) ---
        # Split into words and process each
        words = cleaned.split()
        
        for word in words:
            # Skip words that are purely alphabetic
            if not re.search(r'\d', word):
                continue
            
            # Skip words attached to units (like 1,9mg, 2.025gm)
            if re.search(r'\d+[/,.]\d+\s*(mg|gm|ml|kg|g|l)\b', word, re.IGNORECASE):
                continue
            
            # Remove any leading/trailing non-digit characters
            # but keep / inside (e.g., "08/05/2024")
            word = re.sub(r'^[^0-9/]+|[^0-9/]+$', '', word)
            
            if not word:
                continue
            
            # --- Sub-case B1: Word contains / (like "08/05/2024" or "10/27") ---
            if '/' in word:
                validated = validate_slash_date(word)
                if validated:
                    dt = _safe_parse(validated)
                    if dt and 2020 <= dt.year <= 2035:
                        found_dates.append((word, dt))
                        continue
            
            # --- Sub-case B2: Pure digits (no /) ---
            else:
                result = process_pure_number_word(word)
                if result:
                    dt = _safe_parse(result)
                    if dt and 2020 <= dt.year <= 2035:
                        found_dates.append((word, dt))
                        continue
    
    return found_dates


# ============================================================
# Main Scanner Function (for Django Integration)
# ============================================================
# Initialize globally to avoid reloading on every request
_reader = None

def get_reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(['en'], gpu=False)
    return _reader

def scan_label(image_path):
    """
    Process a single label image and return extracted data.
    """
    reader = get_reader()
    
    # Run OCR
    result = reader.readtext(image_path)
    
    blocks = []
    raw_texts = []
    total_confidence = 0
    count = 0
    
    for (bbox, text, confidence) in result:
        raw_texts.append(text)
        total_confidence += confidence
        count += 1
        
        if confidence > 0.7:
            blocks.append({
                'left_x': int(bbox[0][0]),
                'left_y': int(bbox[0][1]),
                'right_x': int(bbox[1][0]),
                'right_y': int(bbox[1][1]),
                'text': text
            })
            
    # Calculate overall confidence
    avg_confidence = (total_confidence / count) if count > 0 else 0
    raw_text_full = " ".join(raw_texts)
    
    # Sort and Group
    blocks.sort(key=lambda b: (b['left_y'], b['left_x']))
    grouped_lines = group_blocks_into_lines(blocks, y_threshold=30)
    
    # Extract dates
    found_dates = extract_dates_from_lines(grouped_lines)
    
    expiry_date_str = ""
    expiry_date_parsed = None
    
    if found_dates:
        # Sort oldest to newest
        found_dates.sort(key=lambda x: x[1])
        # The latest date is our expiry
        latest_date = found_dates[-1]
        
        # Standardize the output format to DD/MM/YYYY
        expiry_date_str = latest_date[1].strftime('%d/%m/%Y')
        
        # Return as YYYY-MM-DD string for Django model (DateField)
        expiry_date_parsed = latest_date[1].strftime('%Y-%m-%d')
        
    return {
        "raw_text": raw_text_full,
        "lot_number": "",  # To be implemented later if needed
        "expiry_date": expiry_date_str,
        "expiry_date_parsed": expiry_date_parsed,
        "confidence": avg_confidence
    }

if __name__ == "__main__":
    # Optional testing from CLI
    import sys
    import os
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if os.path.isdir(path):
            for filename in sorted(os.listdir(path)):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    res = scan_label(os.path.join(path, filename))
                    print(f"--- {filename} ---")
                    print(f"EXP: {res['expiry_date']} -> {res['expiry_date_parsed']}")
        else:
            res = scan_label(path)
            print(res)