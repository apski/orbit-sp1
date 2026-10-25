// Native-module mocks for capturing NavCore screens in jest: the default (no-data) state.
jest.mock('react-native/Libraries/Settings/Settings', () => ({
  __esModule: true, default: { get: () => undefined, set() {}, watchKeys: () => 0, clearWatch() {} },
}));
