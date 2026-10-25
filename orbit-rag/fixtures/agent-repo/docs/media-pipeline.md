# Media pipeline

## Formats

Forecast imagery is encoded as WebP at three sizes (small, medium, large) by the image service. Spot photos uploaded by users are re-encoded to WebP on the server. Uploads are limited to 10 MB per photo.

## Caching

Images are cached on device for seven days and revalidated with ETags.

## Maps

Spot maps use Mapbox GL with the Tidepool house style.
