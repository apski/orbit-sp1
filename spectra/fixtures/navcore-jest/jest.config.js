// Jest capture of the NavCore harness app (its own repo) under its own preset, without adding files to the app.
// App: $NAVCORE_APP, else ~/code/NavCore (or its navcore/ subfolder while that layout lasts).
// Run from the app dir (its Babel config): npx jest --config <orbit-sp1>/spectra/fixtures/navcore-jest/jest.config.js
const fs = require('fs');
const os = require('os');
const path = require('path');
const base = process.env.NAVCORE_APP || path.join(os.homedir(), 'code', 'NavCore');
const app = fs.existsSync(path.join(base, 'navcore', 'package.json')) ? path.join(base, 'navcore') : base;
const nm = path.join(app, 'node_modules');
module.exports = {
  preset: '@react-native/jest-preset',
  rootDir: app,
  roots: [__dirname],
  transformIgnorePatterns: ['node_modules/(?!((jest-)?react-native|@react-native(-community)?|@react-navigation|react-native-screens|react-native-safe-area-context|@tanstack)/)'],
  setupFiles: [require.resolve(path.join(nm, '@react-native/jest-preset/jest/setup.js')), path.join(__dirname, 'setup.js')],
  modulePaths: [nm],
  moduleNameMapper: { '^navcore-app/(.*)$': path.join(app, '$1'), '^react$': path.join(nm, 'react'), '^react-test-renderer$': path.join(nm, 'react-test-renderer'), '^react-native$': path.join(nm, 'react-native') },
};
