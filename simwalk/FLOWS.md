# Writing simwalk journeys

A journey is an ES module that default-exports `async ({ d, step }) => {}`. `d` is the driver, and `step(name, fn)`
times and labels each step, so a failure tells you exactly where it broke.

```js
export const name = 'signup-happy-path';

export default async function ({ d, step }) {
  await step('launch', async () => {
    d.launch('com.example.app');
    await d.waitFor({ id: 'welcome.getStarted' }, 10000);
  });
  await step('start signup', () => d.tap({ id: 'welcome.getStarted' }, { until: { id: 'signup.email' } }));
  await step('pick plan', () => d.tap({ label: 'Annual' }, { until: 'Continue' }));
}
```

Run: `node simwalk.mjs flows/signup.mjs [--udid <UDID>] [--repeat 5]`

## The rules

1. **Every navigating tap gets an `until`.** `until` is the element that proves the tap worked, usually something
   that only exists on the next screen. Without it, simwalk can only check that the target moved, which a
   highlight or a scroll can fake. With it, a swallowed touch is detected and re-tapped. This is the most common
   cause of flaky journeys.

2. **Select by `{ id }` first.** Use `accessibilityIdentifier` (UIKit), `.accessibilityIdentifier()` (SwiftUI) or
   `testID` (React Native). Ids survive copy changes and localization. Fall back to `{ label }` (exact), then to a
   plain string (substring, case-insensitive), which is the loosest and the most likely to hit the wrong element.

3. **Pick a `until` that's unique to the destination.** During a push, *both* screens are in the AX tree. A
   back-button id or a title that also exists on the previous screen will match too early. Prefer a row or field
   that only the new screen has (e.g. `'iOS Version'` for About, not `'About'`).

4. **Make the start state explicit.** Apps restore state: Settings reopens on the last screen, and logged-in apps
   skip onboarding. Begin each journey by driving to a known root (walk back, deep-link with
   `xcrun simctl openurl`, or launch with a reset flag your app understands).

5. **Wait on elements, never on time.** No `sleep(1000)`. Use `d.waitFor(sel, timeoutMs)` with a generous
   timeout for slow things (network, launch: 5–10 s). A 15 ms poll returns as soon as the element exists, so a
   long timeout costs nothing when things are fast.

6. **One user intent per `step`.** "open About", not "tap cell". The step names are the failure report and the
   timing table.

7. **No coordinates unless there is no element.** `d.tapXY` / `d.swipe` are for canvases, maps and scrolling.
   For scrolling to an off-screen row, swipe and re-`find` in a loop with a cap:
   ```js
   for (let i = 0; i < 8 && !(await d.find({ id: 'row.privacy' })); i++) await d.swipe(0.5, 0.75, 0.5, 0.35, 250);
   ```

8. **Assert the outcome at the end.** Finish with a `waitFor` on something that proves the journey succeeded
   (a confirmation label, a value), not just "the last tap didn't throw".

## Making your app walkable

- Give every tappable thing and every screen-identifying element an accessibility identifier. In React Native,
  `testID` maps to it on iOS.
- Elements without a label or identifier are invisible to selectors. If `d.outline()` doesn't list it, you can't
  tap it by name.
- Custom views that draw text themselves need `isAccessibilityElement = true` and a label.

## When a step fails

simwalk prints the step name, the error, and an **outline** of the current screen (`id | label | value`, usually
~150–300 tokens), and saves a screenshot next to you. That outline is the only thing worth giving an agent to
repair the journey. Don't paste the full tree or the screenshot unless the outline is empty (e.g. a WebView or a
game canvas).

Suggested agent prompt:

> Flow `<file>` failed at step "<name>": <error>. Current screen outline:
> <outline>
> Fix the selector or the `until` in that step only. Don't add sleeps.

## Explore a new screen

```bash
node -e "import('./driver.mjs').then(async ({connect}) => { const d = await connect(process.argv[1]); console.log(await d.outline()); d.close(); process.exit(0) })" <UDID>
```
