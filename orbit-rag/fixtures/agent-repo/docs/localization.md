# Localisation

## Languages

Tidepool ships in English, Spanish, Portuguese (Brazil), French and Japanese. Strings live in ICU message format; never concatenate translated fragments.

## Units

Heights default to metres outside the United States, Liberia and Myanmar, where they default to feet. Users can override units in settings. Times are shown in the spot's local time zone with the zone abbreviation.

## Adding a string

Add the key to `en.json` with a description for translators. Translations are pulled from the vendor nightly; untranslated keys fall back to British English (`en-GB`) and are reported in the weekly localisation digest.
