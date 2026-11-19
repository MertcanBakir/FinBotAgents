from enum import StrEnum


class AnalystType(StrEnum):
    MARKET = "market"
    MACRO = "macro"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"


class AssetType(StrEnum):
    STOCK = "stock"
    CRYPTO = "crypto"
