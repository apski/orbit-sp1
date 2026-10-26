module.exports = { plugins: [['expo-build-properties', { ios: { buildReactNativeFromSource: true, ccacheEnabled: true } }]],
  experiments: { buildCacheProvider: 'eas' } }
