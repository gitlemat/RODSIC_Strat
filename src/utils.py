import re
from typing import Dict, Any, Optional

def parse_symbol_expiry(symbol: str) -> Optional[str]:
    """
    Parses a futures symbol (e.g., 'ESU4', 'HEM26') to extract the expiry 
    in 'YYYYMM' format required by IB.
    
    Logic:
    - Ends with 1 or 2 digits (Year).
    - Preceded by 1 char (Month Code).
    """
    # Regex: Capture (Product)(MonthCode)(YearDigits)$
    match = re.search(r"^([A-Z0-9]+)([FGHJKMNQUVXZ])(\d{1,2})$", symbol)
    if not match:
        return None
        
    # product = match.group(1)
    month_code = match.group(2)
    year_digits = match.group(3)
    
    # Month Code to Number
    month_map = {
        "F": "01", "G": "02", "H": "03", "J": "04", "K": "05", "M": "06",
        "N": "07", "Q": "08", "U": "09", "V": "10", "X": "11", "Z": "12"
    }
    month_num = month_map.get(month_code)
    
    if not month_num:
        return None
        
    # Year Expansion
    # 4 -> 2024, 9 -> 2029, 0 -> 2030 (decade assumption? or just 2020?)
    # IB usually uses 1 digit for current decade.
    # 26 -> 2026.
    
    if len(year_digits) == 1:
        # Assume 202x
        year = f"202{year_digits}"
    else:
        # Assume 20xx
        year = f"20{year_digits}"
        
    return f"{year}{month_num}"
