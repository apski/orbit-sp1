// Manual walk through a depth-3 mixed tree (tabs > stack > leaf stack): descend, push 3, pop 1,
// switch tabs away and back (leaf stack state must survive), pop to Home.
// Both arms: ARM=rn (default) or ARM=native (the UIKit twin, same ids and labels).
import { execFileSync } from 'node:child_process';
export const name = 'nav-walk';
const BUNDLE = process.env.ARM === 'native' ? 'com.lab.nativenav' : 'com.lab.rnnav';

const gone = async (d, sel, ms = 5000) => {
  const t = Date.now();
  while (await d.find(sel)) if (Date.now() - t > ms) throw new Error(`still present: ${JSON.stringify(sel)}`);
};

export default async function ({ d, step }) {
  await step('launch depth 3 mix', async () => {
    execFileSync('xcrun', ['simctl', 'launch', '--terminate-running-process', d.udid, BUNDLE, '-depth', '3', '-kind', 'mix']);
    await d.waitFor({ id: 'hub.next.1' }, 10000);
  });
  await step('descend to stack L2', () => d.tap({ id: 'hub.next.1' }, { until: { id: 'hub.next.2' } }));
  await step('descend to leaf L3', () => d.tap({ id: 'hub.next.2' }, { until: { id: 'home.push' } }));
  await step('push #1', () => d.tap({ id: 'home.push' }, { until: { label: 'Detail #1 · d3' } }));
  await step('push #2', () => d.tap({ id: 'detail.push' }, { until: { label: 'Detail #2 · d3' } }));
  await step('push #3', () => d.tap({ id: 'detail.push' }, { until: { label: 'Detail #3 · d3' } }));
  // Every back tap has an `until`: the back button reappears in place on the revealed screen, so
  // without one simwalk reads "nothing moved" and re-taps (double pop on UIKit).
  await step('pop to #2', async () => {
    await d.tap({ id: 'BackButton' }, { until: { label: 'Detail #2 · d3' } });
    await gone(d, { label: 'Detail #3 · d3' });
  });
  await step('switch to Hub1 tab', () => d.tap('Hub1', { until: { label: 'Hub 1/3' } }));
  await step('back to L2 tab keeps #2', () => d.tap('L2', { until: { label: 'Detail #2 · d3' } }));
  await step('pop to Home', async () => {
    await d.tap({ id: 'BackButton' }, { until: { label: 'Detail #1 · d3' } });
    await gone(d, { label: 'Detail #2 · d3' });
    await d.tap({ id: 'BackButton' }, { until: { id: 'home.push' } });
    await gone(d, { label: 'Detail #1 · d3' });
  });
}
