import { AppRegistry } from 'react-native';
import App from './App';
export const init = () => { const Root = withDevTools(App); AppRegistry.registerComponent('x', () => Root); };
const withDevTools = (C: any) => C;
