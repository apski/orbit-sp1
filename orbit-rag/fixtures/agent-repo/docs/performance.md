# Performance budgets

## Startup

Time to interactive on a cold start must stay under 1.5 seconds on an iPhone 12 and under 2.5 seconds on a Pixel 6a.

## Lists

Any list that can exceed 50 rows must use FlashList with a measured item size estimate. Scroll must hold 60 fps on the reference devices. List screens request 50 items per page, the largest page the API returns.

## Images

Forecast imagery and spot photos are served as AVIF with a JPEG fallback for clients that cannot decode it. Thumbnails are requested at the rendered size; never downscale on device.

## Storage

The offline map tile cache may grow to 500 MB before eviction starts.

## Battery

Analytics batches flush every 60 seconds to save battery; background fetch runs at most every 15 minutes.
