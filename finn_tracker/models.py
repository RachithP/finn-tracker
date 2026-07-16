"""
Expense Tracker - Core Data Models
All data stays local. No network calls. No PII displayed.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
import re


# Built-in category list (user can extend)
DEFAULT_CATEGORIES = [
    "Food & Dining",
    "Groceries",
    "Transportation",
    "Gas & Fuel",
    "Shopping",
    "Entertainment",
    "Health & Medical",
    "Fitness",
    "Education",
    "Utilities",
    "Housing & Rent",
    "Travel",
    "Insurance",
    "Subscriptions",
    "Donations",
    "Income",
    "Payments",
    "Fees & Charges",
    "Uncategorized",
]


_AUTOCAT_RULES = [
    # Payments — card autopayments; checked first so they aren't grabbed by other rules
    (re.compile(
        r'AUTOPAY\s*PYMT|AUTOPAY\s*PMT|AUTOMATIC\s*PAYMENT|MOBILE\s*PYMT'
        r'|PAYMENT\s*THANK\s*YOU|ATM\s*WITHDRAWAL|ONLINE\s*TRANSFER',
        re.I), "Payments"),

    # Income
    (re.compile(
        r'PAYROLL|DIRECT\s*DEP(?:OSIT)?|FREELANCE\s*INVOICE|MILEAGE\s*REIMB'
        r'|ZELLE\s*PAYMENT\s*FROM|SALARY|ACH\s*CREDIT|TAX\s*REFUND',
        re.I), "Income"),

    # Fees & Charges
    (re.compile(
        r'MEMBER(?:SHIP)?\s*FEE|ANNUAL\s*FEE|LATE\s*FEE|INTEREST\s*CHARGE'
        r'|FOREIGN\s*TRANSACTION\s*FEE|SERVICE\s*CHARGE|OVERDRAFT|RETURNED\s*ITEM'
        r'|\bUSCIS\b|FRAGOMEN|FACEBK\s*\*|BV\s*\*BEENVERIFIED'
        r'|PURCHASE\s*ADJUSTMENT|NYX\s*\*NAYAX',
        re.I), "Fees & Charges"),

    # Groceries — before Food & Dining so grocery stores with food keywords land here
    (re.compile(
        r'WHOLE\s*FOODS|TRADER\s*JOE|COSTCO|GROCERY\s*OUTLET|SAFEWAY|KROGER'
        r'|QFC|FRED\s*MEYER|ALBERTSONS|WINCO|SPROUTS|ALDI|PUBLIX|MEIJER|VONS'
        r'|RALPHS|H\s*MART|HMART|99\s*RANCH|RANCH\s*99|FOOD\s*MAXX|LUCKY\s*STORE'
        r'|INDIA\s*CASH|INDIA\s*METRO|BHARAT\s*BAZAR|APNI\s*MANDI|APNA\s*BAZAR'
        r'|NEW\s*INDIA\s*BAZAR|MAYURI|SAAGAR\s*GROCER|SAARS\s*MARKET'
        r'|ASIAN\s*FAMILY\s*MARKET|MUSIC\s*CITY\s*CENTER\s*MKT'
        r'|SQ\s*\*.*(?:FARM|ORCHARD|GROCER|PRODUCE|MARKET\s*PLACE)'
        r'|WHOLEFDS|\bPCC\b|LION\s*FOOD|GROC\b'
        r'|GROCER(?:Y|IES)?|SUPERMARKET|SUPER\s*MARKET|FARMERS\s*MARKET',
        re.I), "Groceries"),

    # Education — before Subscriptions so Coursera/Duolingo/LeetCode land here
    (re.compile(
        r'UDEMY|EDX\b|KHAN\s*ACADEMY|SKILLSHARE|COURSERA|DUOLINGO|LEETCODE'
        r'|CHEGG|\bBURSAR\b|\bTUITION\b|STUDENT\s*LOAN'
        r'|GRAMMARLY|GRADESCOPE|CAMPUSBOOKRENTALS',
        re.I), "Education"),

    # Subscriptions — before Shopping so "AMAZON PRIME" lands here, not Shopping
    (re.compile(
        r'NETFLIX|SPOTIFY|APPLE\.COM|APPLE\s*ONE|APPLE\s*TV|APPLE\s*MUSIC'
        r'|HULU|DISNEY\+?|HBO(?:\s|MAX)|PARAMOUNT\+?|PEACOCK|SHOWTIME'
        r'|YOUTUBE\s*PREMIUM|AMAZON\s*PRIME|AMAZON\s*VIDEO'
        r'|GOOGLE\s*(?:ONE|STORAGE|PLAY)|MICROSOFT\s*365|OFFICE\s*365'
        r'|DROPBOX|SOFTWARE\s*SUBSCR|GITHUB|NOTION|FIGMA|CANVA'
        r'|NEW\s*YORK\s*TIMES|WASHINGTON\s*POST|WALL\s*STREET\s*JOURNAL'
        r'|AUDIBLE|HEADSPACE|CALM|ST\s*SUBSCRIPTIONS|NYTIMES'
        r'|LINKEDIN|FREETAXUSA|OPENSNOW|MEDIUM\s*(?:MONTHLY|\.COM)'
        r'|GOOGLE\s*(?:ONE|STORAGE|PLAY|VOICE|VPN)|GOOGLE\s*\*YOUTUBE',
        re.I), "Subscriptions"),

    # Gas & Fuel (includes EV charging)
    (re.compile(
        r'SHELL(?:\s+OIL|\s+GAS|\s+SERV)?|CHEVRON|EXXON|MOBIL'
        r'|\bBP\b|ARCO|VALERO|\b76\s+(?:GAS|STATION)\b|MARATHON\s*(?:GAS|OIL|PETRO)'
        r'|SUNOCO|CITGO|QUIK\s*STOP|QUIKSTOP|CIRCLE\s*K|CASEY|WAWA|SPEEDWAY'
        r'|PILOT\s*TRAVEL|LOVE\'?S\s*TRAVEL|FLYING\s*J'
        r'|TESLA\s*SUPERCHARGER|ELECTRIFY\s*AMERICA|BLINK\s*CHARGING|EV\s*CHARGING'
        r'|GAS\s*STATION|GASOLINE|\bFUEL\b',
        re.I), "Gas & Fuel"),

    # Food & Dining — broad keywords and payment-terminal prefixes (TST*, SQ*, BITES*, OTTER*)
    (re.compile(
        r'RESTAURANT|THAI|SUSHI|PIZZA|BURGER|BBQ|GRILL|\bCAFE\b|BISTRO'
        r'|\bKITCHEN\b|\bEATERY\b|DINER|BAKERY|DONUT|BAGEL|SANDWICH|RAMEN'
        r'|\bPOKE\b|BOBA|COFFEE|CREAMERY|GELATO|BREWERY|TAPROOM|\bPUB\b'
        r'|CHIPOTLE|STARBUCKS|MCDONALDS|SUBWAY|DOMINOS|PAPA\s*JOHN|PIZZA\s*HUT'
        r'|FIVE\s*GUYS|IN-N-OUT|WENDYS|TACO\s*BELL|CHICK-FIL-A|CHILIS|APPLEBEES'
        r'|PANDA\s*EXPRESS|JAMBA|JERSEY\s*MIKE|JIMMY\s*JOHN|PANERA|SHAKE\s*SHACK'
        r'|SWEETGREEN|JACK\s*IN\s*THE\s*BOX|DEL\s*TACO|CARL\'?S\s*JR|DAIRY\s*QUEEN'
        r'|DUNKIN|PEET\'?S|DUTCH\s*BROS|CAFFE|PIROSHKY|BISCUIT\s*LOVE'
        r'|NORTH\s*ITALIA|EL\s*BORRACHO|ETHIOPIAN|INDIAN\s*STREET\s*FOOD'
        r'|RAMESHWARAM|DINTAIFUNG|IDLY\s*EXPRESS|TIFFINS|DOSA'
        r'|HAIDILAO|MEET\s*FRESH|SWARAJ|CHIANGS|SEA\s*WOLF\s*BAKER'
        r'|FOOD\s*TRUCK|HOT\s*POT|GHIRARDELLI|THE\s*MELT\b'
        r'|TST\*|BITES\*|OTTER\*|SQ\s*\*',
        re.I), "Food & Dining"),

    # Shopping
    (re.compile(
        r'AMAZON(?!\s*PRIME|\s*VIDEO)|BEST\s*BUY|BESTBUYCOM|TARGET|WALMART|EBAY'
        r'|ETSY|WAYFAIR|OVERSTOCK|CHEWY|REI(?:\s+|\.|$)|HOME\s*DEPOT|LOWE\'?S'
        r'|IKEA|PATAGONIA|NORTH\s*FACE|COLUMBIA\s*SPORT|NIKE|ADIDAS'
        r'|GAP(?:\s|$)|OLD\s*NAVY|BANANA\s*REPUBLIC|H&M|ZARA|UNIQLO'
        r'|MACY|NORDSTROM|TJ\s*MAXX|MARSHALLS|ROSS\s*STORES'
        r'|DOLLAR\s*TREE|DOLLAR\s*GENERAL|FIVE\s*BELOW|BIG\s*LOTS'
        r'|OFFICE\s*DEPOT|STAPLES|BATH\s*&\s*BODY|SEPHORA|ULTA'
        r'|WARBY\s*PARKER|SKECHERS|LE\s*CREUSET|VITACOST|BTOD\.COM'
        r'|DICK\'?S\s*SPORTING|HOMEGOODS|POTTERY\s*BARN|DMI\*'
        r'|FEDEX|UPS\s*STORE|USPS|WAL-MART|O\'REILLY\s*AUTO'
        r'|SP\s+(?!CUPPINGS)'
        r'|OFFICE\s*SUPPLIES|HARDWARE\s*STORE|PRINTING',
        re.I), "Shopping"),

    # Utilities
    (re.compile(
        r'AT&T|ATT(?:\s|\*)|VERIZON|T-MOBILE|TMOBILE|SPRINT|METRO\s*PCS'
        r'|XFINITY|SPECTRUM|COMCAST|COX\s*COMM|CENTURYLINK|LUMEN'
        r'|PUGET\s*SOUND\s*ENERGY|\bPSE\b|SEATTLE\s*(?:CITY\s*LIGHT|PUBLIC\s*UTIL)'
        r'|PG&(?:AMP;)?E|PACIFIC\s*GAS|CONED|DUKE\s*ENERGY|DOMINION\s*ENERGY'
        r'|SANTA\s*CLARA\s*UTIL|WASTE\s*MANAGEMENT|RECOLOGY'
        r'|WATER\s*UTILITY|ELECTRIC\s*(?:CO|COMPANY)|INTERNET\s*SERVICE',
        re.I), "Utilities"),

    # Transportation
    (re.compile(
        r'\bUBER\b|LYFT|PARKING|METRO(?:\s|$)|BART|\bTRANSIT\b|AMTRAK|GREYHOUND'
        r'|SOUNDER|LINK\s*LIGHT\s*RAIL|KING\s*COUNTY\s*METRO|ORCA\s*CARD'
        r'|ENTERPRISE\s*RENT|HERTZ|AVIS|BUDGET\s*RENT|NATIONAL\s*CAR|ALAMO'
        r'|ZIPCAR|TURO|\bDMV\b|WA\s*DOL\b|VEHICLE\s*REG'
        r'|ACE\s*PARKING|METROPOLIS\s*PARKING',
        re.I), "Transportation"),

    # Travel
    (re.compile(
        r'DELTA\s*AIR|ALASKA\s*AIR|UNITED\s*AIR|AMERICAN\s*AIR|SOUTHWEST\s*AIR'
        r'|SOUTHWES\b|SWA\*|JETBLUE|FRONTIER\s*AIR|SPIRIT\s*AIR'
        r'|LUFTHANSA|BRITISH\s*AIRWAYS|EMIRATES|AIR\s*CANADA'
        r'|MARRIOTT|HILTON|HYATT|WESTIN|SHERATON|COURTYARD|HAMPTON\s*INN'
        r'|HOLIDAY\s*INN|BEST\s*WESTERN|MOTEL\s*6|EXTENDED\s*STAY|\bESA\s*#'
        r'|AIRBNB|VRBO|BOOKING\.COM|EXPEDIA|HOTELS\.COM|PRICELINE|AGODA'
        r'|ALCATRAZ\s*CRUISES|\bCOT\s*\*|VIASAT.*AIRLIN|VIASATSWAIRLINES'
        r'|SMARTECARTE|RCI\s*BILLING|\bJETX\b',
        re.I), "Travel"),

    # Fitness — gyms and fitness studios
    (re.compile(
        r'PLANET\s*FITNESS|LA\s*FITNESS|24\s*HOUR\s*FITNESS|ANYTIME\s*FITNESS'
        r'|GOLD\'?S\s*GYM|EQUINOX|ORANGE\s*THEORY|CROSSFIT|\bYMCA\b'
        r'|PELOTON|CYCL(?:EBAR|EHOUSE)|\bYOGA\b|\bPILATES\b|\bBARRE\b'
        r'|ROCK\s*CLIMBING|CLIMBING\s*GYM|LIFETIME\s*FITNESS|F45\s*TRAINING'
        r'|VOLO\s*\*|RUN365',
        re.I), "Fitness"),

    # Health & Medical
    (re.compile(
        r'CVS|WALGREENS|RITE\s*AID|PHARMACY|DRUG\s*STORE'
        r'|ZOOMCARE|SUTTER\s*HEALTH|PROVIDENCE|KAISER\s*(?:PERM|FOUND)'
        r'|ONESTOP\s*MEDICAL|\bPAMF\b|THORNE\s*RESEARCH'
        r'|MEDICAL\s*(?:CENTER|GROUP)|URGENT\s*CARE|\bCLINIC\b|HOSPITAL'
        r'|DENTAL|OPTOMETRY|THERAPY|CHIROPRACTOR|DERMATOLOGY|PRESCRIPTION'
        r'|SUPPLEMENT',
        re.I), "Health & Medical"),

    # Insurance
    (re.compile(
        r'INSURANCE|GEICO|STATE\s*FARM|PROGRESSIVE|ALLSTATE|FARMERS\s*INS'
        r'|USAA|NATIONWIDE|LIBERTY\s*MUTUAL|TRAVELERS\s*INS|AETNA'
        r'|BLUE\s*CROSS|CIGNA|HUMANA|UNITED\s*HEALTH|KAISER\s*INS|ANTHEM'
        r'|JEWELERS.*MUTUAL',
        re.I), "Insurance"),

    # Entertainment
    (re.compile(
        r'AMC\s*THEATRE|REGAL\s*CINEMA|CINEMARK|FANDANGO|TICKETMASTER'
        r'|STUBHUB|EVENTBRITE|LIVE\s*NATION|BOWLING|ARCADE'
        r'|ESCAPE\s*ROOM|TRAMPOLINE|MUSEUM|ZOO\b|AQUARIUM'
        r'|ARENA\s*SPORTS|SIX\s*FLAGS|DISNEYLAND|UNIVERSAL\s*STUDIOS'
        r'|STEAM\s*GAMES|PLAYSTATION|XBOX|NINTENDO|TWITCH',
        re.I), "Entertainment"),

    # Housing & Rent
    (re.compile(
        r'\bRENT\b|MORTGAGE|\bHOA\b|\bSTORAGE\b|SELF\s*STORAGE|WESTCOAST\s*STORAGE'
        r'|APARTMENT|PROPERTY\s*MGMT|TWO\s*MEN\s*AND\s*A\s*TRUCK',
        re.I), "Housing & Rent"),

    # Donations
    (re.compile(
        r'GOFUNDME|REEF\s*CHECK|WIKIPEDIA|UNITED\s*WAY|RED\s*CROSS'
        r'|SALVATION\s*ARMY|GOODWILL|HABITAT\s*FOR\s*HUMANITY|DONATE',
        re.I), "Donations"),

    # Shopping — SP CUPPINGS explicitly (user confirmed: Shopping)
    (re.compile(r'SP\s*CUPPINGS', re.I), "Shopping"),
]


def autocat(merchant: str) -> str:
    """Apply static categorization rules to a merchant name. Mirrors JS autocat() in index.html."""
    for pattern, category in _AUTOCAT_RULES:
        if pattern.search(merchant):
            return category
    return "Uncategorized"


def parse_amount(raw: str) -> Optional[float]:
    """Parse amount strings like '$1,234.56', '-$50.00', '(100.00)'.
    Also normalizes Unicode minus variants (em-dash, en-dash, minus sign) that
    some banks (e.g. BofA) use for negative amounts in PDF statements."""
    if not raw:
        return None
    s = str(raw).strip()
    for ch in ('−', '–', '—'):
        s = s.replace(ch, '-')
    s = s.replace(",", "").replace("$", "").replace(" ", "")
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        val = float(s)
        if val != val:  # NaN check
            return None
        return -val if negative else val
    except ValueError:
        return None


def mask_sensitive(value: str) -> str:
    """Mask account numbers, card numbers, SSNs from any string."""
    if not value:
        return value
    # Digit-lookarounds instead of \b: underscores/letters glued to digits
    # (e.g. "stmt_123456789012.csv") defeat \b-based matching.
    # Card numbers — 16-digit (4-4-4-4 grouping or contiguous)
    value = re.sub(r'(?<!\d)\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}(?!\d)', '****-****-****-####', value)
    # Card numbers — Amex 15-digit (4-6-5 grouping or contiguous)
    value = re.sub(r'(?<!\d)\d{4}[\s\-]?\d{6}[\s\-]?\d{5}(?!\d)', '****-****-****-####', value)
    # Card numbers — any remaining 13-19 contiguous digits
    # (13-digit Visa, 14-digit Diners, 16-19 digit UnionPay/Maestro)
    value = re.sub(r'(?<!\d)\d{13,19}(?!\d)', '****-****-****-####', value)
    # Account numbers (8-12 digits standalone)
    value = re.sub(r'(?<!\d)\d{8,12}(?!\d)', '***######', value)
    # Account numbers — grouped in exact 4-digit chunks (e.g. "1234-5678-9012").
    # Groups must be exactly 4 digits (not 2-6) so this can't false-positive on
    # dates like "01-15-2024" (2-2-4 grouping).
    value = re.sub(r'(?<!\d)\d{4}[\s\-]\d{4}(?:[\s\-]\d{4})?(?!\d)', '***######', value)
    # SSN
    value = re.sub(r'(?<!\d)\d{3}[-\s]?\d{2}[-\s]?\d{4}(?!\d)', '***-**-####', value)
    return value


@dataclass
class Transaction:
    """A single normalized expense/income transaction."""
    date: date
    merchant: str          # Merchant or description
    amount: float          # Negative = charge/debit (expense), Positive = payment/credit (income)
    category: str = "Uncategorized"
    account: str = ""      # Masked account label (e.g. "Chase ••4231")
    source_file: str = ""  # Filename it came from
    raw_description: str = "" # Original text before normalization
    transaction_id: str = ""  # Optional bank-assigned ID

    def to_dict(self) -> dict:
        # account and source_file are masked too: fallback paths use the raw
        # filename stem, which can embed a full account/card number.
        # Last-4 labels like "Chase ••4231" are intentionally left readable.
        return {
            "date": self.date.isoformat(),
            "merchant": mask_sensitive(self.merchant),
            "amount": round(self.amount, 2),
            "category": self.category,
            "account": mask_sensitive(self.account),
            "source_file": mask_sensitive(self.source_file),
            "transaction_id": self.transaction_id,
        }

    def __repr__(self):
        return (f"Transaction({self.date} | {mask_sensitive(self.merchant)[:30]} "
                f"| ${self.amount:.2f} | {self.category})")


@dataclass
class ParseResult:
    """Result returned by any parser."""
    transactions: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    source_file: str = ""
    parser_used: str = ""
    row_count: int = 0

    @property
    def success(self) -> bool:
        return len(self.transactions) > 0

    @property
    def summary(self) -> dict:
        if not self.transactions:
            return {"total": 0, "count": 0, "errors": self.errors}
        amounts = [t.amount for t in self.transactions]
        return {
            "count": len(self.transactions),
            "total_expenses": round(abs(sum(a for a in amounts if a < 0)), 2),
            "total_income": round(sum(a for a in amounts if a > 0), 2),
            "net": round(sum(amounts), 2),
            "source_file": self.source_file,
            "parser_used": self.parser_used,
            "errors": self.errors,
        }
