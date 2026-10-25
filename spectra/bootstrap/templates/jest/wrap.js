// Providers and props for Spectra captures. `wrap` puts a screen inside what the app normally provides (theme, query
// client, store, safe area, i18n); `props` gives each screen the props it needs (a navigation stub, route params, data).
const React = require('react');
const navigation = { navigate() {}, goBack() {}, push() {}, replace() {}, setOptions() {}, addListener: () => () => {},
  getState: () => ({ routeNames: [], routes: [], index: 0 }), canGoBack: () => false };
exports.wrap = element => element;  // e.g. React.createElement(QueryClientProvider, { client }, element)
exports.props = screen => ({ navigation, route: { key: screen, name: screen, params: {} } });
