# OCR Product Label Scanner: Challenges & Solutions

This document outlines the major technical challenges encountered during the development of the Pharmaceutical Label OCR pipeline, along with the implemented solutions.

## 1. Fragmented OCR Text (The Spatial Grouping Problem)
* **Problem:** `EasyOCR` reads labels in disconnected fragments rather than coherent sentences. For example, a date like `10/2026` might be detected as four separate bounding boxes: `["10", "20", "2", "6"]`. This made standard regex extraction impossible.
* **Solution:** We built a custom **Spatial Grouping Algorithm**. By capturing the bounding box coordinates, we grouped text blocks that shared similar Y-axis values (`abs(avg_y - current_y) <= 30 pixels`). We then sorted these horizontal groups by their X-coordinates to reconstruct a perfect left-to-right reading order, effectively rebuilding the label's actual sentences.

## 2. False Positives in Date Extraction (Noisy Labels)
* **Problem:** Pharmaceutical labels are incredibly noisy. They contain barcodes, lot numbers, and dosages. Standard date regexes were catching false positives like `971019` (a random code) or `1,9mg` (a dosage interpreted as `1/9`).
* **Solution:** 
  - **Negative Lookaheads:** We implemented rules to ignore numbers attached to pharmaceutical units (`mg`, `ml`, `gm`, `kg`).
  - **Year Bounding:** We enforced strict logical boundaries on parsed years (`2020 <= year <= 2035`) to ensure we only capture realistic expiration windows.
  - **Keyword Targeting:** The system prioritizes dates located near specific keywords (`EXP`, `PER`, `USE BY`). 

## 3. Ambiguity in 6-Digit Coded Dates
* **Problem:** Manufacturers frequently compress dates into 6 digits to save space on small bottles (e.g., `102026` or `202804`). Without delimiters like `/`, it is difficult for standard parsers to know if it is `MMYYYY` or `YYYYMM`.
* **Solution:** Created a deterministic `parse_6_digits` algorithm. It splits the string and tests both ends: if the first two digits are `<= 12` and the last four are between `2020-2035`, it confidently maps it to `MMYYYY`. If the reverse is true, it maps to `YYYYMM`.

## 4. Unpredictable Library Behavior (Month vs. Day Swapping)
* **Problem:** We utilized the `dateparser` library to handle complex multilingual text dates (e.g., `FEV 2025` or `JUN 30 26`). However, when fed numeric strings like `08/2025`, `dateparser` would unpredictably swap the day and month depending on its internal settings.
* **Solution:** Built a `_safe_parse` wrapper around the library. The script now attempts to explicitly match absolute formats (`MM/YYYY`, `MM/YY`, `DD/MM/YYYY`) using strict RegEx first. It only falls back to `dateparser` when it encounters alphabetic month names, completely eliminating the swapping bug.

## 5. Frontend & Backend State Synchronization
* **Problem:** In the web UI, if the OCR failed to find a date, the user could type it manually and hit "Confirm & Store". The Django backend updated the database perfectly via a `PATCH` request, but the local "Scan History" grid on the screen remained unchanged until the user hit F5 to refresh the page.
* **Solution:** Identified a missing callback in the JavaScript. Updated the asynchronous `btnSave` event listener to trigger the `loadHistory()` function immediately upon a `200 OK` response from the API, ensuring a seamless Single Page Application (SPA) experience.

## 6. AI Model Initialization Overhead
* **Problem:** The `EasyOCR` AI model is extremely heavy. Initially, the model was instantiated inside the API view. This meant every time a user uploaded an image, the server wasted time re-loading the model into memory, causing massive API latency.
* **Solution:** Implemented **Lazy Global Initialization**. The `easyocr.Reader` is now instantiated outside the API request loop (`get_reader()`). It loads into RAM only once when the Django server boots, drastically reducing response times for all subsequent scans.
