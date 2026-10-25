import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { Home } from 'screens/Home';
const S = createNativeStackNavigator({ screens: { Cart: Home, Pay: { screen: Home } } });
export default S;
