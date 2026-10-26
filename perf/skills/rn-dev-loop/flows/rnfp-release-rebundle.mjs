// After `rnfp build --config Release --udid U` returned `rebundle`: the re-embedded Hermes bundle boots and runs JS.
// Fresh launch (terminates any running copy), into an RN screen, and a JS state change (count 0 -> 1).
// Run: BUNDLE=<bundle id> simwalk <this file> --udid U --repeat 3   (NavCore ids below)
export const name = 'rnfp release rebundle runs JS';

export default async function ({ d, step }) {
  await step('cold launch → Home', async () => {
    d.launch(process.env.BUNDLE ?? 'org.reactjs.native.example.NavCore');
    await d.waitFor({ id: 'home.openActivity' }, 15000);
  });
  await step('open RN screen', () => d.tap({ id: 'home.openActivity' }, { until: { id: 'list.increment' } }));
  await step('JS state changes', async () => {
    await d.waitFor({ label: 'count 0' }, 5000);
    await d.tap({ id: 'list.increment' }, { until: { label: 'count 1' } });
  });
}
