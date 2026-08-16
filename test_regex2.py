import re

arg = "20 aug 2026 to 10 sept 2026 lol"
date_pattern = r'\d{1,2}\s+[a-zA-Z]{3,9}\s+\d{4}'
range_match = re.match(fr'^({date_pattern})\s+to\s+({date_pattern})(?:\s+(.+))?$', arg, re.IGNORECASE)
single_match = re.match(fr'^({date_pattern})(?:\s+(.+))?$', arg, re.IGNORECASE)

if range_match:
    print("Range match:", range_match.groups())
elif single_match:
    print("Single match:", single_match.groups())
else:
    print("No match!")

