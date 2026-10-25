// Expo CNG: no ios/ dir; identity only in app.config.js (Bluesky shape, gap g0035)
module.exports = () => ({
  expo: {
    name: 'Fixture',
    ios: { bundleIdentifier: 'com.fixture.app' },
    plugins: [['ext', { targets: [{ name: 'Share', bundleIdentifier: 'com.fixture.app.share' }] }]],
  },
});
