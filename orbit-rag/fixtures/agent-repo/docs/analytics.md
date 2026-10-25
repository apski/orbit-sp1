# Analytics

## Event naming

Event names are snake_case verbs in the past tense, for example `screen_viewed`, `spot_saved`, `alert_created`. Properties are snake_case too. Do not add a new event without a row in the tracking plan.

## Privacy

Never send email addresses, names, precise locations or free-text input. The `user_id` property is the account id hashed with SHA-256 and a salt delivered through remote config, so raw ids never reach the analytics vendor. Raw events are retained for 13 months, then deleted.

## Delivery

Events are batched and flushed every 30 seconds or when 50 events are queued, whichever comes first.

## Vendor

The analytics vendor is Amplitude; the SDK is initialised only after consent.
