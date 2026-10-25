# Engineer onboarding

## Getting the app running

Install Xcode 16, Android Studio with SDK 35, Node 22 and the project's pinned Ruby. Run `yarn bootstrap`, which installs JavaScript dependencies, CocoaPods and the Android Gradle wrapper. The first iOS build takes about eight minutes; later builds are incremental.

## Environments

There are three backends: `local`, `staging` and `production`. The app picks the backend from the build configuration; debug builds default to staging. Never point a debug build at production without the on-call engineer's approval. Session tokens stay valid for 60 minutes, so a debug session rarely needs to sign in again.

## Code review

Every change needs one approval from a code owner of the touched area. Changes that touch native code need an approval from the mobile platform team. Keep pull requests under 400 changed lines where possible; split refactors from behaviour changes.

## Branching

Work happens on short-lived branches off `main`. Release branches are cut by the release train and only accept cherry-picked fixes.

## Where to ask

Ask in the #tidepool-mobile channel. Architecture questions go to the weekly architecture sync; decisions from it are recorded as ADRs or in the meeting notes folder.

## Tests

Unit tests use Jest. End-to-end tests are written in Detox and live in `e2e/`; run them locally with `yarn e2e:ios` before asking for review.
