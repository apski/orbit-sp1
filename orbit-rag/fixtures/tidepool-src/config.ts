// Client limits shared by the API layer, the sync engine and the UI kit.
export const RATE_LIMIT_PER_MINUTE = 90;
export const MAX_SYNC_ATTEMPTS = 5;
export const BACKOFF_START_SECONDS = 2;
export const BACKOFF_CAP_MINUTES = 5;
export const TOKEN_TTL_MINUTES = 15;
export const PAGE_SIZE_DEFAULT = 25;
export const PAGE_SIZE_MAX = 100;
export const MAX_UPLOAD_MB = 10;
export const TILE_CACHE_MB = 200;
export const ANALYTICS_FLUSH_SECONDS = 30;
export const ANALYTICS_BATCH_EVENTS = 50;
export const MIN_TOUCH_TARGET_POINTS = 48;
export const PASSWORD_MIN_LENGTH = 12;
export const IMAGE_CACHE_DAYS = 7;
export const OTA_CANARY_PERCENT = 10;
export const API_BASE = "https://api.tidepool.app/v3";
