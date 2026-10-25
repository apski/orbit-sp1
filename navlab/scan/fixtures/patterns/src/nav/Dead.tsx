import { createNativeStackNavigator } from '@react-navigation/native-stack';
const Stack = createNativeStackNavigator();
export const Dead = () => <Stack.Navigator><Stack.Screen name="X" component={() => null} /></Stack.Navigator>;
