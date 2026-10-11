DISCOGS_HOST = "api.discogs.com"

OPTION_TOKEN = "personal_access_token"
OPTION_STYLE_TAG = "style_tag"
OPTION_MIN_OVERLAP = "minimum_token_overlap"

DEFAULT_TOKEN = ""
DEFAULT_STYLE_TAG = "grouping"
DEFAULT_MIN_OVERLAP = 80

# Plugin API v2 stored settings in Picard's global section under these names
LEGACY_OPTION_NAMES = {
    OPTION_TOKEN: "discogs_personal_access_token",
    OPTION_STYLE_TAG: "discogs_style_tag",
    OPTION_MIN_OVERLAP: "discogs_minimum_token_overlap",
}
