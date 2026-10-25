// Spectra jest capture harness (written by `spectra install`, owned by this repo). Runs only the generated
// .spectra/jest/*.spectra.test.js files, under the app's own preset and Babel config.
const path = require('path');
module.exports = {
  preset: '{{PRESET}}',
  rootDir: path.resolve(__dirname, '../..'),
  roots: ['<rootDir>/.spectra/jest'],
  testMatch: ['**/*.spectra.test.js'],
  // RN libraries ship untranspiled ESM; add packages here when jest reports "Unexpected token 'export'".
  transformIgnorePatterns: ['node_modules/(?!((jest-)?react-native|@react-native(-community)?|@react-navigation|react-native-screens|react-native-safe-area-context|@tanstack|@expo|expo(nent)?|expo-.*|@unimodules)/)'],
  setupFiles: [{{PRESET_SETUP}}path.join(__dirname, 'setup.js')],
};
