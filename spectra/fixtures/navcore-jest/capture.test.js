import React from 'react';
import { Detail } from 'navcore-app/Activity';
import { DataProvider } from 'navcore-app/data';
const { captureScreen } = require('../../frontends/rn-jest/jest-capture.js');

const navigation = { navigate() {}, goBack() {}, getState: () => ({ routeNames: [] }) };
test('capture Detail', () => {
  const cap = captureScreen(<DataProvider><Detail navigation={navigation} /></DataProvider>, 'Detail', process.env.OUT);
  expect(cap.roots.length).toBeGreaterThan(0);
});
