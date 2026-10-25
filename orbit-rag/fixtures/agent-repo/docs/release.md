# Releases

## Over-the-air updates

JavaScript-only changes ship over the air with expo-updates. The `runtimeVersion` policy is `fingerprint`, so any change to native code or native dependencies produces a new runtime and requires a store release.

## Release train

Store releases follow a weekly train: the release branch is cut every Tuesday at 10:00 UTC and submitted after QA sign-off. Hotfixes may ship outside the train with two approvals.

## Rollout

Store releases use phased rollout over seven days. OTA updates go to 10% of users for 24 hours before full rollout.

## CI

Release builds run on macOS runners with Xcode 16 and Node 20; the lockfile is verified before any build step.

## Monitoring

Crashes and JavaScript errors are reported to Sentry, with source maps uploaded by the release job.
