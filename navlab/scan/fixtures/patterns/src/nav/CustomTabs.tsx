// Bluesky shape: a custom navigator built on TabRouter, mounted via getComponent (gap g0001/g0002)
import { createNavigatorFactory, useNavigationBuilder, TabRouter } from '@react-navigation/native';
import { Home } from 'screens/Home';
function MyTabNavigator(props: any) {
  const { state, descriptors } = useNavigationBuilder(TabRouter, props);
  return null;
}
const createMyTabs = createNavigatorFactory(MyTabNavigator);
const T = createMyTabs();
export default function CustomTabs() {
  return (
    <T.Navigator>
      <T.Screen name="A" component={Home} />
      <T.Screen name="B" component={Home} />
      <T.Screen name="C" component={Home} />
    </T.Navigator>
  );
}
