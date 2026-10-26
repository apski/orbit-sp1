// Settings → General → About → back → back. Works on any stock iOS 26 simulator.
export const name = 'settings-about';

export default async function ({ d, step }) {
  await step('launch Settings at root', async () => {
    d.launch('com.apple.Preferences');
    // Settings restores the last screen on relaunch: walk back to the root.
    await d.waitFor({ id: 'BackButton' }, 3000).catch(() => {});
    while (!(await d.find({ id: 'com.apple.settings.general' }))) await d.tap({ id: 'BackButton' });
  });
  await step('open General', () => d.tap({ id: 'com.apple.settings.general' }, { until: { id: 'About' } }));
  await step('open About', () => d.tap({ id: 'About' }, { until: 'iOS Version' }));
  await step('back to General', () => d.tap({ id: 'BackButton' }, { until: { id: 'SCREEN_CAPTURE' } }));
  await step('back to Settings', () => d.tap({ id: 'BackButton' }, { until: { id: 'com.apple.settings.general' } }));
}
