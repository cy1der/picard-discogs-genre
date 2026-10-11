# Discogs Genre & Style

Use genre & style information from Discogs for Picard

## Requirements

- MusicBrainz Picard 3.0 or newer (plugin API v3)
- For Picard 2.x, use the [`apiv2`](https://github.com/cy1der/picard-discogs-genre/tree/apiv2) branch

## Getting Started

1. Install the plugin, either:
   - in Picard: Options > Plugins > Install Plugin…, using `https://github.com/cy1der/picard-discogs-genre`
   - from the command line: `picard-cli plugins install https://github.com/cy1der/picard-discogs-genre`
2. Enable the plugin
3. Turn off "Use genres from MusicBrainz" in Picard settings (Metadata > Genres)
4. Set the [personal access token](https://www.discogs.com/settings/developers) (optional, but recommended)

Settings from the Picard 2 version of this plugin are carried over automatically the first time it is enabled.
